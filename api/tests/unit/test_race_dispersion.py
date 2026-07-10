"""Feature 066 US1 axis A: build_race_dispersion (pure, no DB).

Covers the honesty-critical contract: q missing/partial → unavailable with NO fallback to model p
(SC-002), full field → band + raw numbers, band omitted when no boundary artifact (F8).
"""

from __future__ import annotations

from horseracing_api.dispersion import LoadedBoundary, build_race_dispersion

_PNUMS = {1, 2, 3, 4}


def _full_q() -> dict[int, float]:
    return {1: 0.4, 2: 0.3, 3: 0.2, 4: 0.1}


def test_q_missing_returns_unavailable_no_p_fallback():
    d = build_race_dispersion(qmap={}, p_numbers=_PNUMS, odds_as_of=None, odds_source="prerace")
    assert d.available is False
    assert d.unavailable_reason == "no_market_odds"
    # NO fallback to model p — every number stays null.
    assert d.band is None and d.normalized_entropy is None and d.favorite_win_prob is None
    assert d.model_delta is None


def test_partial_q_returns_partial_unavailable():
    d = build_race_dispersion(
        qmap={1: 0.6, 2: 0.4}, p_numbers=_PNUMS, odds_as_of=None, odds_source="prerace"
    )
    assert d.available is False and d.unavailable_reason == "partial_market_odds"
    assert d.normalized_entropy is None


def test_full_field_populates_raw_numbers_band_null_without_boundary():
    d = build_race_dispersion(
        qmap=_full_q(), p_numbers=_PNUMS, odds_as_of=None, odds_source="final"
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
        qmap=_full_q(), p_numbers=_PNUMS, odds_as_of=None, odds_source="final", boundary=boundary
    )
    assert d.band == "somewhat_open"  # 0.85 < 0.926 <= 0.95
    assert d.boundary_version == "dispbands-test"


def test_model_delta_deferred_null():
    d = build_race_dispersion(
        qmap=_full_q(), p_numbers=_PNUMS, odds_as_of=None, odds_source="final"
    )
    assert d.model_delta is None  # deferred until two_gamma calibrator on read path


def test_band_independent_of_model_p_magnitudes():
    """T009 behavioral leak-guard: the dispersion axis is a function of market q ONLY. Axis A never
    reads model-p magnitudes (only the canonical p SET for availability), so it cannot feed back
    into / be driven by the decision-support p. Same q + same field → identical band regardless of
    what the model p values are. (The api import-graph test separately guards betting/training.)"""
    a = build_race_dispersion(
        qmap=_full_q(), p_numbers=_PNUMS, odds_as_of=None, odds_source="final"
    )
    b = build_race_dispersion(
        qmap=_full_q(), p_numbers=_PNUMS, odds_as_of=None, odds_source="final"
    )
    assert (a.band, a.normalized_entropy, a.favorite_win_prob) == (
        b.band, b.normalized_entropy, b.favorite_win_prob
    )
    # build_race_dispersion has no model-p-value parameter at all — structurally leak-safe.
    import inspect

    from horseracing_api.dispersion import build_race_dispersion as fn
    params = set(inspect.signature(fn).parameters)
    assert "pmap" not in params and "win_prob" not in params and "p_values" not in params
