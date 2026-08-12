"""Feature 088: finish-rank decomposition bundle — values / leak boundary / additivity / projection.

REJECTED at the pre-registered gate (2026-08-10) and therefore UNWIRED: the module is not called by
build_asof_features and its columns are not in the registry. These tests call the build function
DIRECTLY so the negative result stays executable (062/070 precedent). The registry-dependent
assertions (group registration / materialized membership) are skipped while unwired.

Frozen contract: specs/088-finish-rank-decomposition/contracts/feature-columns.md (INV-C1..C11).
All hand-computed fixtures below are the pre-registered ones from that contract (do NOT relax them
to match an implementation — the arithmetic is the spec).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from horseracing_features.finish_decomposition_features import (
    FINISH_DECOMP_COLUMNS,
    build_finish_decomposition_features,
)
from tests._frames import make_frames
from tests._leakcheck import assert_invariant
from tests._projection import assert_projected_equals_full


def _row(out, rid, hid):
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def _race(rid, date, horses):
    """horses: list of dicts merged onto the default horse spec (horse_number auto)."""
    return {
        "race_id": rid,
        "race_date": date,
        "horses": [{"horse_number": i + 1, **h} for i, h in enumerate(horses)],
    }


def _filler(n, start_order, prefix="X"):
    """n filler starters finishing at start_order, start_order+1, ... (unique horse ids)."""
    return [
        {"horse_id": f"{prefix}{i}", "finish_order": start_order + i}
        for i in range(n)
    ]


# ---------------------------------------------------------------- INV-C1 / fixtures (T004)


def test_fixture_8_started_5th_is_4_over_7():
    """contract fixture: 8頭出走・全馬完走の5着 -> finish_pct = 4/7."""
    specs = [
        _race("200801010101", "2008-01-01",
              [{"horse_id": "H", "finish_order": 5},
               *_filler(4, 1), *_filler(3, 6, prefix="Y")]),
        _race("200802010101", "2008-02-01",
              [{"horse_id": "H", "finish_order": 1}, {"horse_id": "Z", "finish_order": 2}]),
    ]
    r = _row(build_finish_decomposition_features(make_frames(specs)), "200802010101", "H")
    assert abs(r["prev_finish_pct"] - 4.0 / 7.0) < 1e-12


def test_fixture_18_started_3_dnf_last_finisher_is_14_over_17():
    """contract fixture: 18頭出走・3頭中止(15完走)の最下位完走(15着) -> 14/17 (< 1.0 は仕様)."""
    horses = [{"horse_id": "H", "finish_order": 15}]
    horses += [{"horse_id": f"F{i}", "finish_order": i + 1} for i in range(14)]
    horses += [{"horse_id": f"S{i}", "result_status": ResultStatus.STOPPED} for i in range(3)]
    specs = [
        _race("200801010101", "2008-01-01", horses),
        _race("200802010101", "2008-02-01",
              [{"horse_id": "H", "finish_order": 1}, {"horse_id": "Z", "finish_order": 2}]),
    ]
    r = _row(build_finish_decomposition_features(make_frames(specs)), "200802010101", "H")
    assert abs(r["prev_finish_pct"] - 14.0 / 17.0) < 1e-12
    assert r["prev_finish_pct"] < 1.0  # INV-C1: 最下位=1 は保証しない


def test_fixture_dead_heat_for_last_max_below_one():
    """contract fixture: 3頭出走で着順 1,2,2 -> 0, 0.5, 0.5 (最下位同着で max < 1)."""
    specs = [
        _race("200801010101", "2008-01-01",
              [{"horse_id": "A", "finish_order": 1},
               {"horse_id": "B", "finish_order": 2},
               {"horse_id": "C", "finish_order": 2}]),
        _race("200802010101", "2008-02-01",
              [{"horse_id": "A", "finish_order": 1},
               {"horse_id": "B", "finish_order": 2},
               {"horse_id": "C", "finish_order": 3}]),
    ]
    out = build_finish_decomposition_features(make_frames(specs))
    assert abs(_row(out, "200802010101", "A")["prev_finish_pct"] - 0.0) < 1e-12
    assert abs(_row(out, "200802010101", "B")["prev_finish_pct"] - 0.5) < 1e-12
    assert abs(_row(out, "200802010101", "C")["prev_finish_pct"] - 0.5) < 1e-12


def test_fixture_dead_heat_third_of_ten_is_2_over_9():
    """contract fixture: 10頭出走・2頭同着3着 -> 両頭とも 2/9."""
    horses = [{"horse_id": "A", "finish_order": 3}, {"horse_id": "B", "finish_order": 3},
              {"horse_id": "W", "finish_order": 1}, {"horse_id": "V", "finish_order": 2}]
    horses += [{"horse_id": f"F{i}", "finish_order": 5 + i} for i in range(6)]
    specs = [
        _race("200801010101", "2008-01-01", horses),
        _race("200802010101", "2008-02-01",
              [{"horse_id": "A", "finish_order": 1}, {"horse_id": "B", "finish_order": 2}]),
    ]
    out = build_finish_decomposition_features(make_frames(specs))
    for h in ("A", "B"):
        assert abs(_row(out, "200802010101", h)["prev_finish_pct"] - 2.0 / 9.0) < 1e-12


_SERIES_DENOM = 20  # 21 starters -> finish_pct = (order-1)/20, exact for 0.05 steps


def _series_specs(pcts, target_date="2009-01-01"):
    """Past runs for H whose finish_pct equals each value in ``pcts`` (21 starters -> denom 20)."""
    specs = []
    for i, p in enumerate(pcts):
        exact = p * _SERIES_DENOM
        assert abs(exact - round(exact)) < 1e-9, f"pct {p} not representable with denom {_SERIES_DENOM}"
        order = int(round(exact)) + 1  # (order-1)/(21-1) = p
        others = [{"horse_id": f"O{i}_{j}", "finish_order": j + 1 if j + 1 < order else j + 2}
                  for j in range(_SERIES_DENOM)]
        specs.append(_race(f"20080{i + 1}010101", f"2008-0{i + 1}-01",
                           [{"horse_id": "H", "finish_order": order}, *others]))
    specs.append(_race("200901010101", target_date,
                       [{"horse_id": "H", "finish_order": 1}, {"horse_id": "Z", "finish_order": 2}]))
    return specs


def _order_of(pct):
    """The finish_order a ``_series_specs`` run with this finish_pct carries."""
    return float(round(pct * _SERIES_DENOM) + 1)


def test_fixture_trend5_slope_minus_015():
    """contract fixture: 完走系列 [0.8, 0.65, 0.5, 0.35, 0.2] (古→新) -> OLS 傾き −0.15."""
    out = build_finish_decomposition_features(
        make_frames(_series_specs([0.8, 0.65, 0.5, 0.35, 0.2]))
    )
    r = _row(out, "200901010101", "H")
    assert abs(r["finish_trend5"] - (-0.15)) < 1e-12  # INV-C9: 改善 -> 負


def test_inv_c11_avg_last3_pct_equals_mean_of_three_lags():
    """INV-C11: avg_last3_finish_pct == mean(prev, prev2, prev3 の finish_pct) — 完全従属."""
    out = build_finish_decomposition_features(
        make_frames(_series_specs([0.9, 0.7, 0.5, 0.3, 0.1]))
    )
    r = _row(out, "200901010101", "H")
    expected = np.mean([r["prev_finish_pct"], r["prev2_finish_pct"], r["prev3_finish_pct"]])
    assert abs(r["avg_last3_finish_pct"] - expected) < 1e-12


def test_lag_and_window_values():
    """生値ラグ・5走平均・best(expanding min)の値(完走系列・古→新 0.9,0.7,0.5,0.3,0.1)."""
    out = build_finish_decomposition_features(
        make_frames(_series_specs([0.9, 0.7, 0.5, 0.3, 0.1]))
    )
    r = _row(out, "200901010101", "H")
    orders = [_order_of(p) for p in (0.9, 0.7, 0.5, 0.3, 0.1)]  # 19, 15, 11, 7, 3
    assert abs(r["prev_finish_pct"] - 0.1) < 1e-12
    assert abs(r["prev2_finish"] - orders[3]) < 1e-12
    assert abs(r["prev3_finish"] - orders[2]) < 1e-12
    assert abs(r["prev2_finish_pct"] - 0.3) < 1e-12
    assert abs(r["prev3_finish_pct"] - 0.5) < 1e-12
    assert abs(r["avg_last5_finish"] - np.mean(orders)) < 1e-12
    assert abs(r["avg_last5_finish_pct"] - np.mean([0.9, 0.7, 0.5, 0.3, 0.1])) < 1e-12
    assert abs(r["best_finish_pct"] - 0.1) < 1e-12  # expanding min


def test_inv_c1_range_within_zero_one():
    out = build_finish_decomposition_features(
        make_frames(_series_specs([0.9, 0.7, 0.5, 0.3, 0.1]))
    )
    for c in [c for c in FINISH_DECOMP_COLUMNS if c.endswith("_pct")]:
        v = out[c].dropna()
        assert ((v >= 0.0) & (v <= 1.0)).all(), f"{c} out of [0,1]"


def test_inv_c2_single_starter_race_yields_nan_pct():
    """INV-C2: 出走 1 頭(分母 0)の走の finish_pct は NaN(生値ラグは定義される)."""
    specs = [
        _race("200801010101", "2008-01-01", [{"horse_id": "H", "finish_order": 1}]),
        _race("200802010101", "2008-02-01",
              [{"horse_id": "H", "finish_order": 1}, {"horse_id": "Z", "finish_order": 2}]),
    ]
    r = _row(build_finish_decomposition_features(make_frames(specs)), "200802010101", "H")
    assert pd.isna(r["prev_finish_pct"])
    assert pd.isna(r["best_finish_pct"])  # 有効 finish_pct が 0 件


def test_inv_c2a_out_of_range_finish_order_yields_nan_pct():
    """INV-C2a: finish_order > 出走頭数(データ異常)-> その走の finish_pct は NaN."""
    specs = [
        _race("200801010101", "2008-01-01",
              [{"horse_id": "H", "finish_order": 9},   # 2 頭立てで 9 着 = 異常
               {"horse_id": "Z", "finish_order": 1}]),
        _race("200802010101", "2008-02-01",
              [{"horse_id": "H", "finish_order": 1}, {"horse_id": "Z", "finish_order": 2}]),
    ]
    r = _row(build_finish_decomposition_features(make_frames(specs)), "200802010101", "H")
    assert pd.isna(r["prev_finish_pct"])
    assert pd.isna(r["best_finish_pct"])  # 唯一の過去走が異常 -> 有効 pct 0 件


def test_nan_propagates_through_pct_windows():
    """INV-C6: 窓内に NaN(退化走)があれば正規化集約は NaN(スキップしない)。生値系は無事."""
    specs = []
    # 5 past finished runs; the 3rd one is a single-starter race (pct = NaN)
    for i, order in enumerate([3, 4, 1, 5, 6]):
        if i == 2:
            specs.append(_race(f"20080{i + 1}010101", f"2008-0{i + 1}-01",
                               [{"horse_id": "H", "finish_order": 1}]))
            continue
        others = [{"horse_id": f"O{i}_{j}", "finish_order": j + 1 if j + 1 < order else j + 2}
                  for j in range(10)]
        specs.append(_race(f"20080{i + 1}010101", f"2008-0{i + 1}-01",
                           [{"horse_id": "H", "finish_order": order}, *others]))
    specs.append(_race("200901010101", "2009-01-01",
                       [{"horse_id": "H", "finish_order": 1}, {"horse_id": "Z", "finish_order": 2}]))
    r = _row(build_finish_decomposition_features(make_frames(specs)), "200901010101", "H")
    assert pd.isna(r["avg_last5_finish_pct"])  # 窓内に NaN
    assert pd.isna(r["finish_trend5"])
    assert not pd.isna(r["avg_last5_finish"])  # 生値は定義される


def test_min_periods_four_finishes_yield_nan_for_five_run_columns():
    """完走 4 走では avg_last5 系と trend5 が NaN(min_periods=5)、3 走系は値を持つ."""
    out = build_finish_decomposition_features(make_frames(_series_specs([0.8, 0.6, 0.4, 0.2])))
    r = _row(out, "200901010101", "H")
    assert pd.isna(r["avg_last5_finish"])
    assert pd.isna(r["avg_last5_finish_pct"])
    assert pd.isna(r["finish_trend5"])
    assert not pd.isna(r["avg_last3_finish_pct"])


def test_debut_row_is_all_nan():
    """完走 0 走(新馬)-> 10 列すべて NaN(0 埋め禁止・憲法 IV)."""
    specs = [_race("200801010101", "2008-01-01",
                   [{"horse_id": "D", "finish_order": 1}, {"horse_id": "Z", "finish_order": 2}])]
    r = _row(build_finish_decomposition_features(make_frames(specs)), "200801010101", "D")
    for c in FINISH_DECOMP_COLUMNS:
        assert pd.isna(r[c]), f"{c} must be NaN for a debut row"


def test_non_finishers_excluded_from_series():
    """INV-C4: 系列は完走走のみ — 間に挟まる非完走走はラグを消費しない."""
    specs = [
        _race("200801010101", "2008-01-01",
              [{"horse_id": "H", "finish_order": 3},
               *_filler(10, 1)]),
        _race("200802010101", "2008-02-01",
              [{"horse_id": "H", "result_status": ResultStatus.STOPPED},
               *_filler(10, 1)]),
        _race("200803010101", "2008-03-01",
              [{"horse_id": "H", "finish_order": 1}, {"horse_id": "Z", "finish_order": 2}]),
    ]
    r = _row(build_finish_decomposition_features(make_frames(specs)), "200803010101", "H")
    # prev = the 2008-01 finish (the DNF run is not in the finished series)
    assert abs(r["prev_finish_pct"] - 2.0 / 10.0) < 1e-12


# ---------------------------------------------------------------- INV-C5 leak boundary (T005)


def _leak_specs():
    return [
        _race("200801010101", "2008-01-01",
              [{"horse_id": "H", "finish_order": 3}, *_filler(10, 1)]),
        _race("200802010101", "2008-02-01",
              [{"horse_id": "H", "finish_order": 5}, *_filler(10, 1)]),
        # target day: H's own race + a same-day sibling race
        _race("200803010101", "2008-03-01",
              [{"horse_id": "H", "finish_order": 1}, {"horse_id": "Z", "finish_order": 2}]),
        _race("200803010102", "2008-03-01",
              [{"horse_id": "H2", "finish_order": 1}, {"horse_id": "Z2", "finish_order": 2}]),
    ]


def test_leak_target_own_result_change_is_invariant():
    base = make_frames(_leak_specs())
    mutated = make_frames(_leak_specs())
    rr = mutated.race_results
    sel = (rr["race_id"] == "200803010101") & (rr["horse_id"] == "H")
    mutated.race_results.loc[sel, "finish_order"] = 2
    assert_invariant(build_finish_decomposition_features, base, mutated,
                     "200803010101", "H", cols=FINISH_DECOMP_COLUMNS)


def test_leak_same_day_other_race_change_is_invariant():
    base = make_frames(_leak_specs())
    mutated = make_frames(_leak_specs())
    rr = mutated.race_results
    mutated.race_results.loc[rr["race_id"] == "200803010102", "finish_order"] = 2
    assert_invariant(build_finish_decomposition_features, base, mutated,
                     "200803010101", "H", cols=FINISH_DECOMP_COLUMNS)


def test_leak_future_race_addition_is_invariant():
    base = make_frames(_leak_specs())
    future = _leak_specs() + [
        _race("200812010101", "2008-12-01",
              [{"horse_id": "H", "finish_order": 9}, *_filler(10, 1)])
    ]
    assert_invariant(build_finish_decomposition_features, base, make_frames(future),
                     "200803010101", "H", cols=FINISH_DECOMP_COLUMNS)


# ---------------------------------------------------------------- INV-C7 / C8 additivity (T006)


def test_inv_c7_unique_keys_and_only_bundle_columns():
    """純加算の構造保証: (race_id, horse_id) 一意 + 値列は 10 列のみ(既存列と disjoint)."""
    out = build_finish_decomposition_features(make_frames(_leak_specs()))
    assert not out.duplicated(subset=["race_id", "horse_id"]).any()
    value_cols = [c for c in out.columns if c not in ("race_id", "horse_id")]
    assert set(value_cols) == set(FINISH_DECOMP_COLUMNS)


def test_inv_c7_columns_disjoint_from_all_other_features():
    """Name disjointness still holds while unwired (it is what made the merge additive).

    The 'is wired into materialized_columns' half of INV-C7 is intentionally NOT asserted: the
    bundle was REJECTED and unwired, so requiring registration would fail by design.
    """
    from horseracing_features.registry import FEATURE_GROUPS, materialized_columns

    bundle = set(FINISH_DECOMP_COLUMNS)
    assert not (bundle & set(FEATURE_GROUPS)), "bundle names must stay disjoint from registered ones"
    assert not (bundle & set(materialized_columns())), "rejected bundle must NOT be materialized"


def test_inv_c8_all_float64_including_debut_only_pool():
    """10 列とも float64。完走ゼロのプール(全馬デビュー)でも dtype が変わらない."""
    out = build_finish_decomposition_features(make_frames(_leak_specs()))
    for c in FINISH_DECOMP_COLUMNS:
        assert out[c].dtype == np.float64, f"{c} dtype {out[c].dtype} != float64"

    debut_only = [_race("200801010101", "2008-01-01",
                        [{"horse_id": "A", "finish_order": 1}, {"horse_id": "B", "finish_order": 2}])]
    out2 = build_finish_decomposition_features(make_frames(debut_only))
    for c in FINISH_DECOMP_COLUMNS:
        assert out2[c].dtype == np.float64, f"debut-only pool: {c} dtype {out2[c].dtype}"


def test_cancelled_entry_rows_are_emitted_like_other_blocks():
    """出走取消(race_results 行なし)でも行が落ちない(左結合の行数保存)."""
    specs = [
        _race("200801010101", "2008-01-01",
              [{"horse_id": "H", "finish_order": 3}, *_filler(10, 1)]),
        _race("200802010101", "2008-02-01",
              [{"horse_id": "H", "entry_status": EntryStatus.CANCELLED},
               {"horse_id": "Z", "finish_order": 1}]),
    ]
    out = build_finish_decomposition_features(make_frames(specs))
    assert len(out[(out.race_id == "200802010101") & (out.horse_id == "H")]) == 1


# ---------------------------------------------------------------- INV-C10 projection (T007)


def test_inv_c10_projection_matches_full_build():
    assert_projected_equals_full(
        build_finish_decomposition_features, make_frames(_leak_specs()), ["200803010101"]
    )


def test_inv_c10_projection_same_day_multiple_races():
    """同日複数レースを同時に投影しても full build と一致."""
    assert_projected_equals_full(
        build_finish_decomposition_features, make_frames(_leak_specs()),
        ["200803010101", "200803010102"],
    )


def test_inv_c10_projection_keeps_race_level_primitive_over_full_pool():
    """投影時も n_started は全過去レースから計算される(対象馬が絡まない馬の出走も分母に入る).

    H の過去走には H 以外の 10 頭が出走しており、投影で source を H に絞っても分母 (N−1) は
    変わってはならない = full build と同じ finish_pct になる。
    """
    frames = make_frames(_leak_specs())
    proj = assert_projected_equals_full(
        build_finish_decomposition_features, frames, ["200803010101"]
    )
    r = proj[(proj.race_id == "200803010101") & (proj.horse_id == "H")].iloc[0]
    assert abs(r["prev_finish_pct"] - 4.0 / 10.0) < 1e-12  # 11 starters -> (5-1)/10
