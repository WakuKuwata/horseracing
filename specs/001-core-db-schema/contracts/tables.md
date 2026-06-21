# Contract: 凍結テーブル契約

下流 feature (取込・特徴量・評価・serving・推奨・UI) が依存する、安定インターフェース。
変更は破壊的とみなし、憲法 VI に従い非破壊拡張 (列追加) を原則とする。詳細列定義は
[data-model.md](../data-model.md) を正とする。

## 安定保証 (これらは変えない)

| 契約項目 | 値 |
|---|---|
| `races` PK | `race_id` (text, `^[0-9]{12}$`) |
| `race_horses` PK | `(race_id, horse_id)` |
| `race_results` PK | `(race_id, horse_id)` |
| ラベル正本 | `race_results.finish_order` + `result_status` |
| 出走状態列 | `race_horses.entry_status ∈ {started,cancelled,excluded}` |
| 完走状態列 | `race_results.result_status ∈ {finished,stopped,disqualified}` |
| 最新オッズ列 | `race_horses.odds` (履歴なし、上書き) |
| 時系列基準 | `races.race_date` |
| 横断 ID 解決 | `id_mappings` 経由のみ (推測結合禁止) |
| 予測確率列 | `race_predictions.{win_prob,top2_prob,top3_prob}` (単調 CHECK) |
| 監査保持 | `recommendations` の使用オッズ/疑似オッズ/疑似ROI/各 version/computed_at |
| 監査列 | 全テーブル `created_at` / `updated_at` (トリガ自動更新) |

## テーブル一覧 (13)

コア: `races`, `horses`, `jockeys`, `trainers`, `race_horses`, `race_results`
取込/ID: `id_mappings`, `ingestion_jobs`
予測/推奨: `model_versions`, `prediction_runs`, `race_predictions`, `feature_snapshots`,
`recommendations`

## 拡張ポイント (後続 feature が非破壊で足す想定)

- 券種別結合確率の詳細列 (P0 未決): `recommendations.selection` (jsonb) で吸収、専用列は後続。
- 推定市場オッズ変換規則の詳細: `is_estimated_odds` + `estimated_market_odds_used` を起点に拡張。
- 特徴量定義の実体: `feature_snapshots.features` (jsonb) + `feature_version`。
- 血統 embedding 等の補助特徴量: `horses` への列追加 or 別表。
