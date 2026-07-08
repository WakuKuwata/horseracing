# Data Model: 062 as-of レーティング特徴

**スキーマ変更なし・migration なし・新規 read 列なし。** 既存ロード列(finish_order/race_date/race_id/horse_id)からの派生(非永続・ビルド時決定論再計算・materialized parquet に他 as-of 列と同様に載る)。

## 派生エンティティ

### 馬レーティング状態(中間値・非公開)

- horse_id → 現レーティング(スカラー、初期 1500)+ 出走数 + 履歴(recent delta / max 用)
- (race_date, race_id) 昇順の 1 パスで更新。各レースは「朝スナップショット」を記録してから日末に更新(D3 日単位凍結)

### レーティング更新イベント(中間値)

- 1 レースの finish_order 付き馬集合 → Elo 多者ペアワイズ差分(D1)
- DNF/取消/初出走は D4 の規律

### rating 特徴群(features-017 の新列、FEATURE_GROUPS="rating")

| 列 | 定義 | dtype/欠損 |
|---|---|---|
| asof_rating | レース開始時点(朝スナップショット)のレーティング水準 | float64 / 初出走=初期値 1500(事実) |
| asof_rating_recent_delta | 直近 n レースのレーティング変化(勢い) | float64 / 履歴不足 NaN |
| asof_rating_vs_field | 今走出走馬の as-of レーティング平均との差(LOO) | float64 / NaN |
| asof_rating_max | 自己ベストレーティング | float64 / 初出走=初期値 |
| asof_rating_starts | as-of 出走数(信頼度) | float64 / 初出走=0.0(事実) |

(列セットは codex レビュー反映後に確定 — 変更時はこの表を更新)

## バージョニング

- `FEATURE_VERSION`: features-016 → **features-017**
- `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-017"]` = {"features-016": `300b28a9...`(lgbm-061)、"features-015": `0a93f210...`(lgbm-058-acc/lgbm-060-mkt)}
- 既存列バイト不変(additive)・source_fingerprint 不変(新ソース列なし)
- materialized parquet は要 1 回再 materialize
