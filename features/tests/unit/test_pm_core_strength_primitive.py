"""Feature 070 (T006): the shared race_market_primitive returns complete-field q/s/N.

The primitive is the single source of q/s/N for F02/F04/F05 (no re-computation, codex 論点2).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from horseracing_features.pm_core_strength import race_market_primitive


def _runs(odds):
    n = len(odds)
    return pd.DataFrame({
        "race_id": ["R"] * n, "horse_id": [f"H{i}" for i in range(n)],
        "race_date": [pd.Timestamp("2008-01-01")] * n, "odds": odds,
    })


def test_q_sums_to_one_and_s_is_log_qN():
    prim = race_market_primitive(_runs([2.0, 3.0, 6.0])).set_index("horse_id")
    # q_i = (1/O_i)/Σ(1/O): 1/2,1/3,1/6 = 0.5,0.333,0.167 over Σ=1.0 -> q=0.5,0.333,0.167
    assert abs(prim["q"].sum() - 1.0) < 1e-12
    assert (prim["N"] == 3.0).all()
    # s = log(q*N)
    for h in prim.index:
        assert abs(prim.loc[h, "s"] - np.log(prim.loc[h, "q"] * 3.0)) < 1e-12


def test_n1_gives_q1_s0():
    prim = race_market_primitive(_runs([2.5])).iloc[0]
    assert prim["q"] == 1.0 and prim["N"] == 1.0 and abs(prim["s"]) < 1e-12


def test_partial_field_voids_whole_race():
    # one invalid odds (<=0) -> the whole race is dropped (complete-field, never renormalize).
    prim = race_market_primitive(_runs([2.0, 0.0, 6.0]))
    assert prim.empty
