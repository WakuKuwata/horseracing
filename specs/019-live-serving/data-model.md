# Data Model: ライブ serving (019)

**スキーマ変更なし**。既存 prediction_runs / race_predictions / recommendations + 008 テーブル + 016
stake_fraction を再利用。新規は orchestration の value object（非永続）のみ。

---

## 1. 永続スキーマ

変更なし。live 予測 → prediction_runs / race_predictions（006）。live 推奨 → recommendations（011/016、
append-only、使用オッズ値・computed_at・logic_version を保持）。scrape → race_horses(entries+odds) /
id_mappings / ingestion_jobs（008）。migration head = 0006。

---

## 2. fail-closed ガード（live モード前提条件、非永続判定）

| ガード | 条件 | 違反時 |
|---|---|---|
| valid_race_id | `^[0-9]{12}$`（JRA-VAN） | 拒否（書き込みなし） |
| result_pending | `race_results` に当該 race の行が無い | 拒否（走行済み → retrospective を案内） |
| entries_complete | started 馬 ≥1・horse_number 揃い・重複/頭数不整合なし | 拒否（部分取得） |
| odds_present（推奨段） | 対象出走集合に pre-race win オッズが揃う | 推奨を出さない（予測 p は可） |

順序: scrape → valid_race_id / result_pending / entries_complete を満たせば予測、odds_present を満たせば推奨。

---

## 3. LiveServeReport（value object、非永続 / prospective ログ）

| フィールド | 意味 |
|---|---|
| race_id / race_date | 対象 |
| mode | `live`（result-pending）。走行済みは拒否 |
| scrape | entries/odds の取得件数・欠損（008 Counts） |
| guards | 各ガードの pass/fail + 拒否理由 |
| prediction_run_id / n_horses | run_serving 結果（予測時） |
| recommendations | 生成数・bet_type 別・is_estimated_odds・shadow フラグ |
| odds_as_of | 使用オッズの updated_at（race_horses.odds） |
| computed_at | 実行時刻（prospective ログのキー） |
| shadow | live Kelly は実資金執行なし（FR-016） |

prospective: report + 永続化された prediction_run/recommendations（computed_at + 使用オッズ値）で、後日結果
確定後に既存 backtest（007/011/016）へ投入可能。

---

## 4. 予測経路（run_serving 再利用）

`run_serving(race_id, model_version)` → build_feature_matrix(end_date=race_date)（as-of・結果非参照・同日除外）
→ predict_race → check_consistency（IV）→ persist_run。**cutoff = race_date**（時刻粒度は deferred）。
新馬/unmapped は Unknown 特徴 + 出走頭数に含む（004/IV）。

---

## 5. 推奨経路（011/016 再利用）

prediction_run → 009 結合確率 → 010 推定オッズ（race_horses.odds=pre-race 由来）→ 011 exotic EV / 016 Kelly。
未来レースに実 exotic オッズは無いため estimated（double-pseudo）。recommendations に使用オッズ値・
is_estimated_odds・stake_fraction（Kelly）・logic_version（校正/Kelly 設定）・computed_at を保存。

---

## 6. 不変条件 / 境界

- リーク境界（II）: features は結果を読まない（run_serving as-of）。cutoff=race_date 以降・他レース・結果を使わない。
  odds/stake はモデル特徴に戻さない。
- fail-closed: result-pending かつ valid id かつ完全性を満たさなければ予測しない。odds 欠損で推奨しない。
- 再現性（V）: computed_at + 使用オッズ値 + model_version/logic_version。スナップショット履歴なし（最新上書き）。
- 決定論: 同一 entries・同一オッズ値・同一 model/calibrator → 同一予測・推奨。
- shadow: live Kelly は記録のみ（実資金執行なし）。
- スキーマ変更ゼロ。
