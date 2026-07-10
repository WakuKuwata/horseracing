"""Feature 065 (US1): collect_prospective capture discipline — result-pending guard, fresh-capture
odds_asof, re-check pending after scrape, run-cross idempotency, WIN-only rows. No closing-oracle."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import Recommendation
from sqlalchemy import select

from horseracing_live import live_serve
from horseracing_live.orchestrate import collect_prospective
from tests._synth import add_results, make_active_model, seed_learnable, seed_pending_race

pytestmark = pytest.mark.integration

_PENDING = "200806019911"
_DATE = datetime.date(2008, 6, 1)
_ASOF = datetime.datetime(2008, 6, 1, 9, 30, tzinfo=datetime.UTC)


def _setup_run(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    seed_pending_race(session, race_id=_PENDING, race_date=_DATE, field_size=8)
    live_serve(session, race_id=_PENDING, model_version=mv, recommend=False)  # create the run
    return mv


def _fresh_scrape(session, race_id):
    """Fake pre-race odds capture: odds already seeded → return the capture timestamp."""
    return _ASOF


def _prospective_wins(session):
    return list(session.scalars(
        select(Recommendation).where(Recommendation.bet_type == BetType.WIN)
        .where(Recommendation.logic_version.contains(";prospective=1"))
    ))


def test_collect_prospective_generates_win_rows_with_marker(session, tmp_path):
    _setup_run(session, tmp_path)
    rep = collect_prospective(session, race_ids=[_PENDING], scrape_fn=_fresh_scrape)
    assert rep.generated == 1
    wins = _prospective_wins(session)
    assert wins and all("odds_asof=2008-06-01T09:30:00+00:00" in w.logic_version for w in wins)
    assert all(w.bet_type == BetType.WIN for w in wins)      # WIN only, not exotic
    assert all(not w.is_estimated_odds for w in wins)        # real single-win odds (frozen)


def test_collect_prospective_idempotent_across_runs(session, tmp_path):
    mv = _setup_run(session, tmp_path)
    collect_prospective(session, race_ids=[_PENDING], scrape_fn=_fresh_scrape)
    n1 = len(_prospective_wins(session))
    # a NEW prediction run (live is append-only), then re-collect → must NOT duplicate (race-scoped)
    live_serve(session, race_id=_PENDING, model_version=mv, recommend=False)
    rep2 = collect_prospective(session, race_ids=[_PENDING], scrape_fn=_fresh_scrape)
    assert rep2.generated == 0 and rep2.skip_exists == 1
    assert len(_prospective_wins(session)) == n1


def test_collect_prospective_rejects_result_present(session, tmp_path):
    _setup_run(session, tmp_path)
    add_results(session, race_id=_PENDING)               # race is no longer result-pending
    rep = collect_prospective(session, race_ids=[_PENDING], scrape_fn=_fresh_scrape)
    assert rep.generated == 0 and rep.skip_not_pending == 1
    assert _prospective_wins(session) == []


def test_collect_prospective_rechecks_pending_after_scrape(session, tmp_path):
    """Results landing MID-FLOW (during the scrape) must block the prospective marker."""
    _setup_run(session, tmp_path)

    def _scrape_then_results(session, race_id):
        add_results(session, race_id=race_id)            # race ends during capture
        return _ASOF

    rep = collect_prospective(session, race_ids=[_PENDING], scrape_fn=_scrape_then_results)
    assert rep.generated == 0 and rep.skip_not_pending == 1   # re-check after scrape caught it
    assert _prospective_wins(session) == []
