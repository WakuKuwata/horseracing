"""T013 (US2): bankroll backtest — Kelly vs flat, segment separation, determinism (Feature 016).

Covers SC-006/SC-007/SC-008. Uses no-takeout estimated odds so positive-edge bets exist, and injects
real exotic odds for a subset of races so both the `real` and `double_pseudo` segments populate and
are reported separately (never combined).
"""

from __future__ import annotations

import datetime
import math

import pytest
from horseracing_db.models import ExoticOdds
from horseracing_features.builder import build_feature_matrix
from horseracing_serving.model_loader import load_serving_model
from sqlalchemy import select

from horseracing_betting.exotic_ev import candidate_bets
from horseracing_betting.exotic_types import ALL_EXOTIC
from horseracing_betting.kelly_backtest import _field_and_outcome, run_bankroll_backtest
from horseracing_betting.kelly_types import KellyConfig
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_FROM = datetime.date(2008, 1, 1)
_TO = datetime.date(2008, 12, 31)
_NO_TAKEOUT = {bt: 1.0 for bt in ALL_EXOTIC}
_CFG = KellyConfig(lambda_real=0.5, lambda_est=0.25, cap_bet=0.05, cap_total=0.10,
                   o_min=1.0, min_edge=0.0, min_edge_est=0.0, bankroll=100.0)


def _seed(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    return make_active_model(session, tmp_path, model_version="bt")


def _inject_real(session, mv):
    """Price real exotic odds for candidates of even-numbered races (EV≈1.5 → positive edge)."""
    model = load_serving_model(session, mv)
    rows = build_feature_matrix(session, end_date=_TO)
    present = set(rows["race_id"].unique())
    from horseracing_db.models import Race
    races = session.execute(
        select(Race.race_id).where(Race.race_date >= _FROM).where(Race.race_date <= _TO)
        .order_by(Race.race_id)
    ).all()
    for (race_id,) in races:
        # day index is encoded at race_id[8:10] (the trailing "01" is the race number suffix).
        if race_id not in present or int(race_id[8:10]) % 2 != 0:
            continue
        field, _ = _field_and_outcome(session, model, race_id, rows)
        if field is None or not field.p_norm:
            continue
        for bets in candidate_bets(field, payout_rates=_NO_TAKEOUT).values():
            for b in bets:
                session.add(ExoticOdds(race_id=race_id, bet_type=b.bet_type,
                                       selection=list(b.selection),
                                       odds=round(1.5 / b.p_model, 4), coverage_scope="full"))
    session.commit()


def _run(session, *, ruin_threshold=0.0, **kw):
    return run_bankroll_backtest(
        session, date_from=_FROM, date_to=_TO, cfg=_CFG, threshold=1.0, top_k=3,
        payout_rates=_NO_TAKEOUT, ruin_threshold=ruin_threshold, bootstrap_blocks=50, seed=123,
        **kw,
    )


def test_backtest_reports_six_metrics_both_strategies(session, tmp_path):
    mv = _seed(session, tmp_path)
    report = _run(session, model_version=mv)
    seg = {(s.strategy, s.segment): s for s in report.segments}
    # 2 strategies × 3 segments = 6 rows (SC-006)
    assert len(report.segments) == 6
    for strat in ("kelly", "flat"):
        all_seg = seg[(strat, "all")]
        assert all_seg.n_bets > 0
        # the six metrics are all populated/finite (flat additive bankroll may go negative = ruin)
        assert math.isfinite(all_seg.terminal_bankroll)
        assert 0.0 <= all_seg.ruin_probability <= 1.0
        assert all_seg.max_drawdown >= 0.0
        assert all_seg.variance >= 0.0
        assert all_seg.max_losing_streak >= 0


def test_backtest_segments_separated(session, tmp_path):
    mv = _seed(session, tmp_path)
    _inject_real(session, mv)
    # disable ruin truncation so per-segment bet counts are additive (independent sub-paths).
    report = _run(session, model_version=mv, ruin_threshold=-1e18)
    seg = {(s.strategy, s.segment): s for s in report.segments}
    for strat in ("kelly", "flat"):
        a, r, d = seg[(strat, "all")], seg[(strat, "real")], seg[(strat, "double_pseudo")]
        # each bet is in exactly one of real / double_pseudo; never combined (SC-008)
        assert a.n_bets == r.n_bets + d.n_bets
        assert r.n_bets > 0 and d.n_bets > 0  # both populated by the injected subset


def test_backtest_deterministic_and_verdict(session, tmp_path):
    mv = _seed(session, tmp_path)
    r1 = _run(session, model_version=mv)
    r2 = _run(session, model_version=mv)
    s1 = [(s.strategy, s.segment, round(s.terminal_bankroll, 6), round(s.ruin_probability, 6))
          for s in r1.segments]
    s2 = [(s.strategy, s.segment, round(s.terminal_bankroll, 6), round(s.ruin_probability, 6))
          for s in r2.segments]
    assert s1 == s2                                  # seeded block bootstrap is reproducible (F1)
    # verdict is risk-adjusted (not a bare ROI>1 statement)
    assert "SUCCESS" in r1.verdict or "NOT-ADOPTED" in r1.verdict
    assert r1.seed == 123
