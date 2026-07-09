"""Feature 043: recommend-serve — product-flow single-race generation.

Generates ONE coherent set (Kelly EV+stake) for the active-model run, is idempotent (no
append-only duplication on re-run), and prints a machine-parseable SKIPPED/OK line (ops maps it).
"""

from __future__ import annotations

import argparse

import pytest
from horseracing_db.models import ExoticOdds, Recommendation
from sqlalchemy import func, select

from horseracing_betting.cli import _cmd_recommend_serve
from horseracing_betting.exotic_ev import candidate_bets, canonical_field
from horseracing_betting.exotic_recommend import _load_field_inputs
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"


def _rec_count(session, run_id) -> int:
    return session.scalar(
        select(func.count()).select_from(Recommendation)
        .where(Recommendation.prediction_run_id == run_id)
    )


def _setup_positive_edge(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    run_id = make_prediction_run(session, race_id=_RACE, model_version=mv)
    # real exotic odds priced so EV = P_model·O = 1.5 (edge > 0) → Kelly stakes something
    preds, odds, scr, n2id = _load_field_inputs(session, run_id, _RACE)
    field = canonical_field(_RACE, preds, odds, scratched=scr, number_to_id=n2id)
    for bets in candidate_bets(field).values():
        for b in bets:
            session.add(ExoticOdds(race_id=_RACE, bet_type=b.bet_type, selection=list(b.selection),
                                   odds=round(1.5 / b.p_model, 4), coverage_scope="full"))
    session.commit()
    return run_id


def test_recommend_serve_generates_and_is_idempotent(session, tmp_path, capsys):
    run_id = _setup_positive_edge(session, tmp_path)
    rc = _cmd_recommend_serve(session, argparse.Namespace(race_id=_RACE))
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("OK:")
    n1 = _rec_count(session, run_id)
    assert n1 > 0                                   # a set was generated

    # second run → idempotent skip, no new rows (append-only not duplicated)
    rc2 = _cmd_recommend_serve(session, argparse.Namespace(race_id=_RACE))
    assert rc2 == 0
    assert "SKIPPED" in capsys.readouterr().out
    assert _rec_count(session, run_id) == n1


def _win_recs(session, run_id):
    from horseracing_db.enums import BetType
    return session.scalars(
        select(Recommendation).where(Recommendation.prediction_run_id == run_id)
        .where(Recommendation.bet_type == BetType.WIN)
    ).all()


def test_recommend_serve_policy_aware_idempotency_legacy_win_plus_cap(session, tmp_path, capsys):
    """Feature 064: a cap policy can be added to a legacy (cap-off) win run without duplicating,
    and re-running the SAME cap policy skips."""
    run_id = _setup_positive_edge(session, tmp_path)
    # 1) legacy generation (cap-off): win recs have no ;oddscap= fragment
    _cmd_recommend_serve(session, argparse.Namespace(race_id=_RACE))
    capsys.readouterr()
    legacy_win = _win_recs(session, run_id)
    assert legacy_win and all(";oddscap=" not in w.logic_version for w in legacy_win)

    # 2) same run, cap=21 policy → win group for THIS policy is absent → tops up a new win group
    rc = _cmd_recommend_serve(session, argparse.Namespace(race_id=_RACE, win_odds_cap=21.0))
    assert rc == 0
    all_win = _win_recs(session, run_id)
    cap_win = [w for w in all_win if ";oddscap=21.0" in w.logic_version]
    assert cap_win, "cap policy should be added to the legacy win run"
    assert len(all_win) > len(legacy_win)                       # topped up, legacy not removed
    # capped win recs never carry an over-cap odds (21+) selection
    assert all(w.market_odds_used is None or float(w.market_odds_used) < 21.0 for w in cap_win)

    # 3) re-run the SAME cap policy → skip (no new rows)
    n_before = len(all_win)
    _cmd_recommend_serve(session, argparse.Namespace(race_id=_RACE, win_odds_cap=21.0))
    assert len(_win_recs(session, run_id)) == n_before


def test_recommend_serve_skips_when_no_run(session, capsys):
    # race with no prediction_run at all → skip, not failure
    from datetime import date

    from tests._synth import insert_race
    insert_race(session, race_id="200801020101", race_date=date(2008, 1, 2),
                horses=[{"horse_id": "x", "horse_number": 1, "odds": 2.0}])
    rc = _cmd_recommend_serve(session, argparse.Namespace(race_id="200801020101"))
    assert rc == 0
    assert "SKIPPED" in capsys.readouterr().out


def test_recommend_backfill_idempotent_and_reconciles(session, tmp_path, capsys):
    from datetime import date

    from horseracing_betting.cli import _cmd_recommend_backfill
    run_id = _setup_positive_edge(session, tmp_path)  # _RACE 2008-01-01, has run+odds
    args = argparse.Namespace(from_=date(2007, 1, 1), to=date(2008, 12, 31))

    rc = _cmd_recommend_backfill(session, args)
    assert rc == 0
    n1 = _rec_count(session, run_id)
    assert n1 > 0                                    # the eligible race got a set

    # re-run → idempotent: existing run skipped, no new rows (count reconciliation asserted in cmd)
    _cmd_recommend_backfill(session, args)
    out = capsys.readouterr().out
    assert "skip_exists=" in out
    assert _rec_count(session, run_id) == n1
