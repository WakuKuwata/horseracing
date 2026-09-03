# Data Model: arm E 系モデルの OOF attestation 対応 (108)

DB スキーマ変更なし・migration なし。以下は attestation payload(content-addressed)と
生成 artifact の構造。

## attestation payload(方式別)

共通キー(現行のまま・順序は canonical JSON が決める):
`base_model_version` / `resolved_lgbm_params` / `objective` / `postprocess` /
`ordered_feature_columns` / `feature_version` / `target_encode_cols` / `te_smoothing` /
`internal_calibration` / `seed` / `num_threads` / `drop_features` / `source_fingerprint` /
`materialized_hash` / `code_sha`

### `internal_calibration` — 校正方式で形が変わる

| 方式 | `method` | `calib_frac` | `calibration_split_unit` | 追加キー |
|---|---|---|---|---|
| legacy(70/30) | `isotonic` | `0 < x < 1` **必須** | 非空文字列 **必須** | なし |
| arm E(OOF) | `isotonic_strict_past_oof` | `{requested, effective}` **必須** | **欠落を既定で埋めない** | `protocol_version`・`n_oof_blocks`(正整数)**必須** |

**arm E の `calib_frac` は宣言値と実効値を分けて持つ**(codex 指摘 1): `requested` は recipe が
宣言した値・`effective` は booster が実際に使った値(0.0)。**この protocol version では
`requested` は非挙動項目**であり、挙動一致の比較から除く。将来効くようになったら
protocol version を上げることで検知する(FR-005d)。

**未知の `method` / 未知の `protocol_version` は拒否**(既定値へのフォールバック禁止)。
既知の method は実測どおり `isotonic`(legacy)と `isotonic_strict_past_oof`(arm E)の 2 値。

### arm E 固有の追加キー(payload トップレベル・**arm E のときだけ存在**)

| キー | 型 | 意味 |
|---|---|---|
| `weight_mask` | `{rate: float, seed: int}` または省略 | 学習時の特徴 mask。`rate` は [0,1]・`seed` は整数。両方揃うか両方無いかのみ(片方だけは拒否) |

**legacy 構成では上記キーが payload に存在しない** → payload はバイト同一 → digest 不変。

### 不変条件

- **INV-A1**: legacy モデルの payload と `attestation_digest` は本 feature の前後で完全一致
- **INV-A2**: payload の 1 バイト改竄で digest 不一致(改竄検出を緩めない)
- **INV-A3**: モデルディレクトリからの再計算照合で世代取り違えを検出(同名上書き耐性)
- **INV-A4**: 方式と必須キーの矛盾(OOF 宣言 + `n_oof_blocks` 欠落、OOF 宣言 + 非 null split_unit、
  mask の rate/seed 片方だけ)は**型付きエラー**。既定値へのフォールバック禁止
- **INV-A5**: 再構成された手続きは記録された全項目(目的関数・校正方式・`n_oof_blocks`・
  mask rate/seed・解決済みパラメータ・特徴列順・seed・スレッド数)と一致する
- **INV-A6**: attestation は `calibration_protocol` / `calibrator_params` を権威として読む。
  top-level の `calibrator_degenerate` を信頼しない(D4: base からの残留値でありうる)

## OOF 予測束(生成 artifact・既存形式のまま)

レースごとの**校正後**予測(win/top2/top3)+ fold 境界 + fold ごとの train/valid ハッシュ +
`attestation_digest`。content-addressed。**保存形式は変更しない**(D5)。

## 校正 manifest(生成 artifact・既存形式のまま)

`schema_version=3` / `artifact_scope` / `activation_eligible` / `fit_through` /
`base_model_version` / `full_precision_params{two_gamma, stage_lambdas{top2,top3}}` /
`attestation_digest` / `bundle_digest` / `manifest_digest` / `evaluation`(verdict)。

- **INV-M1**: 生成 manifest の `base_model_version` は現行 active と一致する
- **INV-M2**: 旧世代 manifest(`d9f45bb0…`)の内容と digest は不変(append-only)
- **INV-M3**: 同一入力から同一 `manifest_digest` が再現される(決定論)

## 校正 verdict

`ADOPT` / `REJECT` / `NO_DECISION` の三値 × 2 stage(two_gamma・stage 割引)。
**測定結果であり選択の対象ではない**。非採用なら恒等校正を出荷する。
