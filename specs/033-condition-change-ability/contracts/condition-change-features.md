# Contract: condition_change features (033)

## モジュール契約

`features/src/horseracing_features/condition_change_features.py`

```python
CONDITION_CHANGE_COLUMNS = [
    "dist_change", "surface_switch", "going_change",        # base (027, new info)
    "dist_extension", "dist_shortening",                    # hinge
    "dist_ext_x_closing", "dist_short_x_speed",             # ability interactions
]

def build_condition_change_features(
    frames: Frames, *, pace: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Per (race_id, horse_id) の condition_change 7 列。base は直前 started レース
    (merge_asof allow_exact_matches=False)。能力は 023 build_pace_features の as-of 出力
    (pace を渡せば再計算しない)。返り値 columns=[race_id,horse_id,*CONDITION_CHANGE_COLUMNS]、
    全特徴 float64。
    """
```

## 集計契約

- `dist_change` = 今走 distance − 直前 started レース distance(前走無し→NaN)。
- `surface_switch` = 同 surface→0.0 / 芝→ダ→+1.0 / ダ→芝→−1.0 / その他変化(障害等)→0.0 / 前走無し→NaN。
- `going_change` = 今走 going_ord − 前走 going_ord(`_GOING_ORD`={良0,稍/稍重1,重2,不/不良3}、不明→NaN)。
- `dist_extension` = where(dist_change.notna(), max(dist_change, 0), NaN)。
- `dist_shortening` = where(dist_change.notna(), max(−dist_change, 0), NaN)。
- `dist_ext_x_closing` = dist_extension × (−rel_last3f_best)（末脚 小=良 → 符号反転で「良い末脚」を正に）。
- `dist_short_x_speed` = dist_shortening × (−rel_time_avg)（時計 小=速 → 符号反転）。
- 片側 NaN → NaN(numpy 既定)。0 埋め禁止。出力 `.astype("float64")`。

## 不変条件（テストで担保）

### 正しさ（test_condition_change_features.py）
- INV-C1: 前走 1600→今走 2000 → dist_change==400・dist_extension==400・dist_shortening==0。
- INV-C2: 前走 2000→今走 1400 → dist_change==−600・dist_shortening==600・dist_extension==0。
- INV-C3: 芝→ダ → surface_switch==1.0；良→重 → going_change==2.0。
- INV-C4: dist_ext_x_closing == dist_extension × (−rel_last3f_best)（pace 出力から検算）。
- INV-C5: デビュー(前走無し) → base/hinge/交互作用 全 NaN。
- INV-C6: 能力 NaN → 交互作用 NaN。
- INV-C7: 全列 float64。

### リーク（test_condition_change_leak.py）
- INV-L1: 自馬の今走 finish_order/result_status を変えても全列不変。
- INV-L2: 同日他レース・未来レースの結果/条件を変えても不変(strictly-before)。
- INV-L3: grep: ソースが今走 finish_order/result_status/odds を生参照しない。

### パリティ（test_materialize_core.py 拡張）
- INV-P1: materialize==in-memory が assert_frame_equal(check_exact, check_dtype)。
- INV-P2: materialized_columns に 7 列、odds/payout/dividend トークン無し。
- INV-P3: FEATURE_VERSION == "features-011"。

## 採用ゲート契約

- baseline = candidate − `--drop-groups`(既定 `condition_change`)= features-010。
- candidate = full features-011。
- primary_pass = 平均 win LogLoss 改善 かつ ECE 非悪化(tol 1e-3)。
- fold ガード = strict majority + worst dECE ≤ 2e-3 + worst dLogLoss ≤ 5e-3。
- adopted = primary_pass かつ fold ガード。ablation/セグメントは SECONDARY。
