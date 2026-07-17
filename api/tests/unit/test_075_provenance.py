"""Feature 075: counterfactual/current provenance renaming + guards (values unchanged).

Locks in the naming migration at the model layer: provenance-labelled response fields exist,
the empirical calibration realized_rate is NOT renamed, and the ShadowLogMonth splat trap
(analyze I1) is now a loud failure instead of a silent null.
"""

from __future__ import annotations

import datetime

import pytest
from pydantic import ValidationError

from horseracing_api.schemas import (
    CalibrationBin,
    FavoriteBaseline,
    RecommendationRow,
    ShadowLogMonth,
    ShadowLogResponse,
)


def test_win_backtest_uses_counterfactual_snapshot_names_and_valuation_basis():
    r = RecommendationRow(
        recommendation_id="r1", bet_type="win", selection=[1], is_estimated_odds=False,
        double_pseudo=False, logic_version="lv", computed_at=datetime.datetime(2026, 5, 1),
        prediction_run_id="run1", settled=True, hit=True,
        counterfactual_snapshot_gross_return=4.2, counterfactual_snapshot_net_return=3.2,
        valuation_basis="frozen_snapshot_odds",
    )
    assert r.counterfactual_snapshot_gross_return == 4.2
    assert r.counterfactual_snapshot_net_return == 3.2
    assert r.valuation_basis == "frozen_snapshot_odds"
    # the misnomer fields are gone from the model
    assert not hasattr(r, "realized_return") and not hasattr(r, "realized_roi")


def test_favorite_uses_current_odds_names():
    fav = FavoriteBaseline(
        horse_number=1, odds=2.0, settled=True, hit=True,
        current_odds_gross_return=2.0, current_odds_net_return=1.0, valuation_basis="current_odds",
    )
    assert fav.current_odds_gross_return == 2.0 and fav.current_odds_net_return == 1.0
    assert fav.valuation_basis == "current_odds"
    assert not hasattr(fav, "realized_return")


def test_shadow_log_recovery_is_counterfactual_snapshot():
    s = ShadowLogResponse(counterfactual_snapshot_recovery_rate=0.9, valuation_basis="frozen_snapshot_odds")
    assert s.counterfactual_snapshot_recovery_rate == 0.9
    assert not hasattr(s, "recovery_rate")


def test_shadow_log_month_splat_trap_is_loud_not_silent_null():
    # analyze I1: the internal neutral dict key is "recovery"; a splat that does not map it must
    # FAIL (extra=forbid), never silently default counterfactual_snapshot_recovery to None.
    ok = ShadowLogMonth(month="2026-05", n_settled=3, counterfactual_snapshot_recovery=0.9)
    assert ok.counterfactual_snapshot_recovery == 0.9
    with pytest.raises(ValidationError):  # extra="forbid" rejects the legacy key
        ShadowLogMonth(**{"month": "2026-05", "n_settled": 3, "recovery": 0.9})


def test_calibration_realized_rate_is_NOT_renamed():
    # US3 / FR-006: empirical realized win-rate stays "realized_rate" (not counterfactual).
    b = CalibrationBin(pred_lo=0.0, pred_hi=0.1, realized_rate=0.05,
                       realized_ci_low=0.01, realized_ci_high=0.09, count=42)
    assert b.realized_rate == 0.05 and b.realized_ci_low == 0.01 and b.realized_ci_high == 0.09
