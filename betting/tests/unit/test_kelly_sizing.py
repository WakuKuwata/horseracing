"""Unit tests for single-bet Kelly fraction (Feature 016, SC-001/SC-005)."""

from __future__ import annotations

import math

from horseracing_betting.kelly_sizing import single_kelly
from horseracing_betting.kelly_types import KellyConfig


def test_kelly_formula_matches_definition():
    cfg = KellyConfig(lambda_real=1.0, cap_bet=1.0, o_min=1.0)  # raw f* (no λ/cap shrink)
    # P=0.5, O=3.0 → edge=0.5, f*=edge/(O-1)=0.5/2=0.25
    res = single_kelly(0.5, 3.0, is_estimated=False, cfg=cfg)
    assert res is not None
    edge, raw, eff = res
    assert math.isclose(edge, 0.5)
    assert math.isclose(raw, 0.25)
    assert math.isclose(eff, 0.25)


def test_fractional_lambda_and_cap_applied():
    cfg = KellyConfig(lambda_real=0.25, cap_bet=0.05, o_min=1.0)
    edge, raw, eff = single_kelly(0.5, 3.0, is_estimated=False, cfg=cfg)
    # λ·f* = 0.25·0.25 = 0.0625, capped to cap_bet 0.05
    assert math.isclose(raw, 0.25)
    assert math.isclose(eff, 0.05)


def test_negative_edge_rejected():
    cfg = KellyConfig(o_min=1.0)
    # P=0.2, O=3 → edge = 0.6-1 = -0.4 ≤ 0 → skip
    assert single_kelly(0.2, 3.0, is_estimated=False, cfg=cfg) is None


def test_o_min_filter_rejects_low_odds():
    cfg = KellyConfig(o_min=1.5)
    # O=1.2 < o_min → rejected even if edge positive
    assert single_kelly(0.95, 1.2, is_estimated=False, cfg=cfg) is None


def test_estimated_uses_conservative_lambda():
    cfg = KellyConfig(lambda_real=0.25, lambda_est=0.10, cap_bet=1.0, o_min=1.0,
                      min_edge_est=0.0)
    _, _, eff_real = single_kelly(0.5, 3.0, is_estimated=False, cfg=cfg)
    _, _, eff_est = single_kelly(0.5, 3.0, is_estimated=True, cfg=cfg)
    # same bet, estimated is more conservative (λ_est < λ_real) → smaller fraction (SC-005)
    assert eff_est < eff_real
    assert math.isclose(eff_est, 0.10 * 0.25)


def test_estimated_disabled_returns_none():
    cfg = KellyConfig(enable_estimated=False, o_min=1.0)
    assert single_kelly(0.5, 3.0, is_estimated=True, cfg=cfg) is None
    # real still allowed
    assert single_kelly(0.5, 3.0, is_estimated=False, cfg=cfg) is not None


def test_estimated_min_edge_filter_stricter():
    cfg = KellyConfig(min_edge=0.0, min_edge_est=0.2, o_min=1.0, cap_bet=1.0)
    # edge = 0.55*2 ... pick P,O for small positive edge below est floor but above real floor
    # P=0.4, O=2.7 → edge = 1.08 - 1 = 0.08; real floor 0.0 passes, est floor 0.2 rejects
    assert single_kelly(0.4, 2.7, is_estimated=False, cfg=cfg) is not None
    assert single_kelly(0.4, 2.7, is_estimated=True, cfg=cfg) is None
