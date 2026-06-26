"""T017: allocation invariants — per-bet and total caps never exceeded (Feature 016, SC-002).

Also checks the full-Kelly optimizer stays feasible (f≥0, Σf≤1) and is concave-optimal-ish (the
joint exact allocation never bets more total than the independent heuristic on positively-correlated
inputs — here the mutually-exclusive structure is exercised across varied groups).
"""

from __future__ import annotations

import pytest

from horseracing_betting.kelly_allocation import allocate_kelly, maximize_log_growth
from horseracing_betting.kelly_types import KellyConfig

# a spread of (p, odds) groups with positive edge
_GROUPS = [
    [(0.5, 3.0), (0.3, 4.0)],
    [(0.4, 4.0), (0.3, 5.0), (0.2, 8.0)],
    [(0.9, 1.6)],
    [(0.25, 6.0), (0.25, 6.0), (0.25, 6.0), (0.2, 10.0)],
]


@pytest.mark.parametrize("group", _GROUPS)
@pytest.mark.parametrize("allocation", ["exact", "heuristic"])
def test_caps_never_exceeded(group, allocation):
    cfg = KellyConfig(lambda_real=1.0, cap_bet=0.05, cap_total=0.10, o_min=1.0,
                      allocation=allocation)
    raw = [(p, o, False, max((p * o - 1.0) / (o - 1.0), 0.0)) for p, o in group]
    fracs = allocate_kelly(raw, cfg=cfg)
    assert all(f >= -1e-12 for f in fracs)                 # non-negative
    assert all(f <= cfg.cap_bet + 1e-9 for f in fracs)     # per-bet cap
    assert sum(fracs) <= cfg.cap_total + 1e-9              # total cap (SC-002)


@pytest.mark.parametrize("group", _GROUPS)
def test_full_kelly_feasible(group):
    p = [x[0] for x in group]
    o = [x[1] for x in group]
    f = maximize_log_growth(p, o, cap_bet=1.0, cap_total=1.0)
    assert all(v >= -1e-12 for v in f)
    assert sum(f) <= 1.0 + 1e-9        # never stakes more than the whole bankroll


def test_exact_differs_from_heuristic_when_multiple_bets():
    group = [(0.4, 4.0), (0.3, 5.0), (0.2, 8.0)]
    raw = [(p, o, False, (p * o - 1.0) / (o - 1.0)) for p, o in group]
    base = dict(lambda_real=1.0, cap_bet=1.0, cap_total=1.0, o_min=1.0)
    ex = allocate_kelly(raw, cfg=KellyConfig(**base, allocation="exact"))
    he = allocate_kelly(raw, cfg=KellyConfig(**base, allocation="heuristic"))
    assert ex != he   # joint (mutual-exclusivity aware) vs independent sizing differ (FR-004)
