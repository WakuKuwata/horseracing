# Data Model: Early-Mid Pace Features (097)

スキーマ変更ゼロ・migration なし。全て導出値(features パッケージ内)。

## 走単位の中間量(永続化しない)

| 量 | 定義 | 定義域 | 欠損規則 |
|---|---|---|---|
| `em` | `finish_time(秒) − last_3f` | (0, ∞) | finish_time/last_3f いずれか欠損 → NaN。em ≤ 0 → NaN(入力破損・埋めない) |
| `race_mean_em` | そのレースの完走馬の em 平均 | — | 完走馬の em が全て NaN → NaN |
| `rel_em` | `em − race_mean_em` | ℝ(負=速い) | 伝播 |

意味論: 1200m では `em ≡ first_3f`(実測 187,833 行・最大誤差 0.0000 秒)。1200m 超では
「前半+中盤」= 別の物理量だが**全履歴で一貫した定義**。既存 `rel_first3f` とは独立に共存し、
値を混ぜない(spec 非交渉点)。

## モデル入力列(新規 2 列・FEATURE_GROUP `early_mid_pace`)

| 列 | 集約 | dtype | 欠損 |
|---|---|---|---|
| `asof_rel_early_mid_avg` | 直近 5 完走の rel_em の mean(strictly-before・同日除外) | float64 | 過去完走ゼロ or 全走 NaN → NaN |
| `asof_rel_early_mid_best` | 同 min(=最速) | float64 | 同上 |

- 集約機構は既存 `_rolling_asof`(pace_features)をそのまま使う(窓 `_RECENT_N=5` 共有)。
- **開示済みの従属**: `_avg ≈ rel_time_avg − rel_last3f_avg`(欠損パターン差のみ)。`_best` は
  分解不能(research D2)。

## 版・互換

| 項目 | 値 |
|---|---|
| FEATURE_VERSION | features-021 → **features-022** |
| 焼却済み(再利用禁止) | features-019(070)・features-020(088) |
| compat pin | features-022 → { features-021: `663fe86c7564…`(lgbm-094-cap900 実測) } |
| source_fingerprint | **不変**(新規ソース列ゼロ)= materialize-safe |

## 判定成果物(append-only・追跡パス)

`specs/097-early-mid-pace/` 配下: gate-config.json(+hash)・verdict JSON
(artifact_kind="full_walk_forward"・pooled CI・guard 2 本・両アーム recipe hash・マスク定義・
採点窓・レース集合 hash)。
