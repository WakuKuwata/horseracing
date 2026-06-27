"""T016: edge haircut in single_kelly (Feature 017, FR-006)."""

from __future__ import annotations

import math

from horseracing_betting.kelly_sizing import single_kelly
from horseracing_betting.kelly_types import KellyConfig


def test_no_haircut_is_passthrough():
    cfg = KellyConfig(lambda_real=1.0, cap_bet=1.0, o_min=1.0, haircut_type="none")
    edge, raw, _ = single_kelly(0.5, 3.0, is_estimated=False, cfg=cfg)
    assert math.isclose(edge, 0.5)
    assert math.isclose(raw, 0.25)   # 0.5/(3-1)


def test_relative_haircut_shrinks_fraction_but_not_reported_edge():
    cfg = KellyConfig(lambda_real=1.0, cap_bet=1.0, o_min=1.0,
                      haircut_type="relative", haircut=0.5)
    edge, raw, _ = single_kelly(0.5, 3.0, is_estimated=False, cfg=cfg)
    assert math.isclose(edge, 0.5)            # reported edge stays RAW (pseudo_roi)
    assert math.isclose(raw, 0.125)           # (0.5·0.5)/(3-1) — sizing uses adjusted edge


def test_absolute_haircut():
    cfg = KellyConfig(lambda_real=1.0, cap_bet=1.0, o_min=1.0,
                      haircut_type="absolute", haircut=0.1)
    edge, raw, _ = single_kelly(0.5, 3.0, is_estimated=False, cfg=cfg)
    assert math.isclose(edge, 0.5)
    assert math.isclose(raw, (0.5 - 0.1) / 2.0)


def test_haircut_can_push_below_min_edge_and_skip():
    # edge 0.08; relative haircut 0.5 → 0.04; with min_edge 0.05 → skip
    cfg = KellyConfig(lambda_real=1.0, cap_bet=1.0, o_min=1.0, min_edge=0.05,
                      haircut_type="relative", haircut=0.5)
    assert single_kelly(0.36, 3.0, is_estimated=False, cfg=cfg) is None
    # without haircut the same bet survives (edge 0.08 > 0.05)
    cfg2 = KellyConfig(lambda_real=1.0, cap_bet=1.0, o_min=1.0, min_edge=0.05)
    assert single_kelly(0.36, 3.0, is_estimated=False, cfg=cfg2) is not None


def test_haircut_independent_of_calibration():
    # haircut is purely a sizing-side shrink; it works with or without a calibrator (config-only)
    cfg = KellyConfig(lambda_real=1.0, cap_bet=1.0, o_min=1.0,
                      haircut_type="relative", haircut=0.2)
    _, raw_h, _ = single_kelly(0.5, 3.0, is_estimated=False, cfg=cfg)
    _, raw_n, _ = single_kelly(0.5, 3.0, is_estimated=False,
                               cfg=KellyConfig(lambda_real=1.0, cap_bet=1.0, o_min=1.0))
    assert raw_h < raw_n
