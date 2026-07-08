# Data Model: 061 本格スピード指数特徴

**スキーマ変更なし・migration なし・新規 read 列なし。** すべて既存ロード列からの派生(非永続、ビルド時決定論再計算。materialized parquet には他 as-of 列と同様に載る)。

## 派生エンティティ

### 基準タイムセル統計(中間値・非公開)

- キー: (venue_code, track_type, distance[正確値], going)
- 標本: **race-level 1 レース 1 標本**(そのレースの finisher タイム平均)— 多頭数レースの過重を防ぐ(codex)
- 値: 対象日より strictly-before(同日除外)のレース標本の count / mean / std(daily cumsum − 当日)
- `min_races=50` 未満 or std 退化 → そのセルのその日の基準は NaN(実測: 全期間で 93.2% のレースがカバー)

### 過去走スピード指数(中間値)

- `z = clip((cell_mean_before − time_s) / cell_std_before, −5, +5)`(正=速い)
- finish_time 欠損(DNF 等)・基準 NaN の過去走は集約から除外

### speed_figure 特徴群(features-016 の新列、FEATURE_GROUPS="speed_figure")

| 列 | 定義 | dtype/欠損 |
|---|---|---|
| asof_spdfig_avg | 過去走 z の全期間平均(strictly-before+同日除外) | float64 / NaN |
| asof_spdfig_best | 過去走 z の cummax | float64 / NaN |
| asof_spdfig_recent3 | 直近 3 走の z 平均 | float64 / NaN |
| asof_spdfig_last | 前走の z | float64 / NaN |
| asof_spdfig_count | 有効 z を持つ過去走数(信頼度、codex 提案) | float64 / 履歴ゼロは 0.0(事実としての 0) |

(codex レビュー反映済み・確定)

## バージョニング

- `FEATURE_VERSION`: features-015 → **features-016**
- `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-016"]` = {"features-014": 既存 pin 値(lgbm-057)、"features-015": bump 前に計測する canonical hash(lgbm-058-acc / lgbm-060-mkt)}
- 既存列はバイト不変(additive)・source_fingerprint 不変(新ソース列なし)
- materialized parquet は要 1 回再 materialize(FEATURE_VERSION 変更のため)
