"""Feature 066 US1 axis A: build_race_dispersion (pure, no DB).

Covers the honesty-critical contract: q missing/partial → unavailable with NO fallback to model p
(SC-002), full field → band + raw numbers, band omitted when no boundary artifact (F8), and the
model_delta (calibrated-p vs q concentration) which is populated only when a calibrator is passed —
while the BAND stays a function of market q ONLY (behavioral leak-guard).
"""

from __future__ import annotations

from horseracing_probability.model_calibration import PCalibrator

from horseracing_api.dispersion import LoadedBoundary, build_race_dispersion

_PNUMS = {1, 2, 3, 4}


def _full_q() -> dict[int, float]:
    return {1: 0.4, 2: 0.3, 3: 0.2, 4: 0.1}


def _full_pmap() -> dict[int, float]:
    # A model p over the same field; magnitudes differ from q on purpose (p≠q).
    return {1: 0.35, 2: 0.30, 3: 0.20, 4: 0.15}


def _cal(method: str = "identity", params: dict | None = None) -> PCalibrator:
    """A frozen calibrator for read-time delta tests. identity → calibrated p == normalized p."""
    return PCalibrator(
        method=method, params=params or {}, train_window=None, n_races=100, n_samples=100,
        prob_range=(0.01, 0.95), select="mle", base_model_version=None, logic_version="pcal-test",
    )


def test_q_missing_returns_unavailable_no_p_fallback():
    d = build_race_dispersion(qmap={}, pmap=_full_pmap(), odds_as_of=None, odds_source="prerace")
    assert d.available is False
    assert d.unavailable_reason == "no_market_odds"
    # NO fallback to model p — every number stays null.
    assert d.band is None and d.normalized_entropy is None and d.favorite_win_prob is None
    assert d.model_delta is None


def test_partial_q_returns_partial_unavailable():
    d = build_race_dispersion(
        qmap={1: 0.6, 2: 0.4}, pmap=_full_pmap(), odds_as_of=None, odds_source="prerace",
        p_calibrator=_cal(),
    )
    assert d.available is False and d.unavailable_reason == "partial_market_odds"
    assert d.normalized_entropy is None
    # Field mismatch (canonical_consistent=false analogue) → no delta even with a calibrator.
    assert d.model_delta is None


def test_full_field_populates_raw_numbers_band_null_without_boundary():
    d = build_race_dispersion(
        qmap=_full_q(), pmap=_full_pmap(), odds_as_of=None, odds_source="final"
    )
    assert d.available is True and d.unavailable_reason is None
    assert d.favorite_win_prob == 0.4
    assert d.top3_cumulative == 0.9  # 0.4+0.3+0.2
    assert d.normalized_entropy is not None and 0.0 < d.normalized_entropy < 1.0
    assert d.is_pseudo is True and d.odds_source == "final"
    # F8: no boundary artifact loaded -> band + version omitted, raw numbers still present.
    assert d.band is None and d.boundary_version is None


def test_band_assigned_when_boundary_present():
    # entropy of {0.4,0.3,0.2,0.1} ~ 0.926; edges below it -> a higher (more open) band.
    boundary = LoadedBoundary(edges=[0.5, 0.7, 0.85, 0.95], version="dispbands-test")
    d = build_race_dispersion(
        qmap=_full_q(), pmap=_full_pmap(), odds_as_of=None, odds_source="final", boundary=boundary
    )
    assert d.band == "somewhat_open"  # 0.85 < 0.926 <= 0.95
    assert d.boundary_version == "dispbands-test"


def test_model_delta_null_without_calibrator():
    d = build_race_dispersion(
        qmap=_full_q(), pmap=_full_pmap(), odds_as_of=None, odds_source="final"
    )
    assert d.model_delta is None  # fail-open: no calibrator artifact loaded → omit the delta


def test_model_delta_model_more_open_when_p_flatter_than_q():
    # Uniform p (H=1.0) is more spread than q (H~0.926) → model sees the race as MORE open.
    d = build_race_dispersion(
        qmap=_full_q(), pmap={1: 0.25, 2: 0.25, 3: 0.25, 4: 0.25},
        odds_as_of=None, odds_source="final", p_calibrator=_cal(),
    )
    assert d.model_delta is not None
    assert d.model_delta.direction == "model_more_open"
    assert d.model_delta.normalized_entropy_delta > 0
    assert d.model_delta.calibrator_version == "pcal-test"


def test_model_delta_model_more_firm_when_p_sharper_than_q():
    # A concentrated p (favourite ~0.9) is far less spread than q → model sees it as MORE firm.
    d = build_race_dispersion(
        qmap=_full_q(), pmap={1: 0.90, 2: 0.05, 3: 0.03, 4: 0.02},
        odds_as_of=None, odds_source="final", p_calibrator=_cal(),
    )
    assert d.model_delta is not None
    assert d.model_delta.direction == "model_more_firm"
    assert d.model_delta.normalized_entropy_delta < 0


def test_band_and_raw_numbers_independent_of_model_p_magnitudes():
    """Behavioral leak-guard: band + raw numbers are a function of market q ONLY. Changing the model
    p magnitudes (same field) changes ONLY model_delta — never the band/entropy/favorite/top3. This
    keeps p from feeding back into the q-derived readout (constitution II)."""
    cal = _cal()
    a = build_race_dispersion(
        qmap=_full_q(), pmap={1: 0.25, 2: 0.25, 3: 0.25, 4: 0.25},
        odds_as_of=None, odds_source="final", p_calibrator=cal,
        boundary=LoadedBoundary(edges=[0.5, 0.7, 0.85, 0.95], version="v"),
    )
    b = build_race_dispersion(
        qmap=_full_q(), pmap={1: 0.90, 2: 0.05, 3: 0.03, 4: 0.02},
        odds_as_of=None, odds_source="final", p_calibrator=cal,
        boundary=LoadedBoundary(edges=[0.5, 0.7, 0.85, 0.95], version="v"),
    )
    # q-derived readout is byte-identical across the two very different p vectors...
    assert (a.band, a.normalized_entropy, a.favorite_win_prob, a.top3_cumulative) == (
        b.band, b.normalized_entropy, b.favorite_win_prob, b.top3_cumulative
    )
    # ...but the model_delta reflects the p difference (open vs firm).
    assert a.model_delta.direction == "model_more_open"
    assert b.model_delta.direction == "model_more_firm"
