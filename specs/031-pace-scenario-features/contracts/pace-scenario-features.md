# Contract: pace_scenario features (031)

## モジュール契約

`features/src/horseracing_features/pace_scenario_features.py`

```python
PACE_SCENARIO_COLUMNS = [
    "field_front_rate_ex_self", "field_closer_rate_ex_self", "pace_imbalance_ex_self",
    "front_pressure", "closer_setup", "style_mismatch", "field_style_coverage",
]

def build_pace_scenario_features(frames: Frames) -> pd.DataFrame:
    """Per (race_id, horse_id) の pace_scenario 7 列を返す。
    入力は build_pace_features(frames) の as-of 出力のみ(生今走列は読まない)。
    返り値: columns = [race_id, horse_id, *PACE_SCENARIO_COLUMNS]、全特徴 float64。
    """
```

## 集計契約（leave-one-out, per race_id）

フィールド = `entry_status == STARTED` の馬。集計母数は対象列が **非 null** の馬のみ。

- race ごとに対象列の「非 null の和 S」「非 null 件数 C」を求める。
- 自馬の値 v(null 可)について:
  - `ex_self_mean = (S - v) / (C - 1)` if 自馬非 null and C > 1
  - `ex_self_mean = S / C`           if 自馬 null and C >= 1
  - それ以外(他馬非 null = 0)→ `NaN`
- `field_front_rate_ex_self` = front_runner_rate の ex_self_mean
- `field_closer_rate_ex_self` = closer_rate の ex_self_mean
- `pace_imbalance_ex_self` = field_front_rate_ex_self − field_closer_rate_ex_self
- `style_mismatch` = own.rel_corner_pos_avg − (rel_corner_pos_avg の ex_self_mean)
- `front_pressure` = own.front_runner_rate × field_front_rate_ex_self
- `closer_setup` = own.closer_rate × field_front_rate_ex_self
- `field_style_coverage` = (race 内 STARTED で front_runner_rate 非 null の馬数) / field_size  ※leave-one-out しない

NaN 伝播: 積・差は片側が NaN なら NaN(numpy 既定)。0 埋め禁止。出力は `.astype("float64")` 固定。

## 不変条件（テストで担保）

### 正しさ（test_pace_scenario_features.py）
- INV-C1: 3 頭 A(front=1.0),B(front=0.5),C(front=0.0) で C 行 `field_front_rate_ex_self` == mean(1.0,0.5)=0.75。
- INV-C2: `pace_imbalance_ex_self` == field_front − field_closer。
- INV-C3: own.closer_rate × field_front_rate_ex_self == `closer_setup`。
- INV-C4: `style_mismatch` == own.rel_corner_pos_avg − 他馬平均。
- INV-C5: 全馬デビュー(脚質 null) → field 列・相互作用 NaN、`field_style_coverage`==0.0。
- INV-C6: 1 頭のみ脚質判明 → 他馬 ex_self は判明 1 頭基準、coverage=1/field_size。
- INV-C7: 出力列はすべて dtype float64。

### リーク（test_pace_scenario_leak.py）
- INV-L1: 自馬の今走 finish_order/corner_orders/running_style/result_status を変えても pace_scenario 全列不変。
- INV-L2: 同レース他馬の今走結果を変えても不変(他馬の過去のみ使用)。
- INV-L3: 同日他レース・未来レースの結果を変えても不変。
- INV-L4: ソース grep: `pace_scenario_features.py` が今走の running_style/corner_orders/finish_order/result_status を **生参照しない**(build_pace_features 経由のみ)。

### パリティ/カバレッジ（test_materialize_core.py 拡張）
- INV-P1: 実 DB(または合成)で materialize 経路 == in-memory `build_feature_matrix` が `assert_frame_equal(check_exact=True, check_dtype=True)`。
- INV-P2: `materialized_columns()` に pace_scenario 7 列が含まれ、odds/payout/dividend トークンを含まない。
- INV-P3: FEATURE_VERSION == "features-009"。

## 採用ゲート契約（training feature-eval）

- baseline = candidate − `--drop-groups`(既定 `pace_scenario`)= features-008。
- candidate = full features-009。
- primary_pass = 平均 win LogLoss 改善 かつ 平均 ECE 非悪化(tol 1e-3)。
- fold ガード = strict majority(n_win*2 > n_folds) かつ worst-fold dECE ≤ 2e-3 かつ worst-fold dLogLoss ≤ 5e-3。
- adopted = primary_pass かつ fold ガード。ablation/market_edge は SECONDARY(採否に使わない)。
