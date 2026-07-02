---
description: "Task list — 予測根拠表示 (040)"
---

# Tasks: 予測根拠表示 (Prediction Explanation Display)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/prediction-explanation.md](contracts/prediction-explanation.md) / [quickstart.md](quickstart.md)

**Tests**: 機械検証不変条件（INV-E1 加法性 / INV-E2 p バイト一致 / リーク境界 / read-only / 注記必須）が採否ゲート相当（憲法 III、021 前例）のため**テスト中核**。

**Organization**: user story 単位。MVP = US1（スコア寄与の永続化と表示）。US2/US3 は独立増分。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup（T0 de-risk spike — 全 story をブロック）

- [X] T001 **T0 spike（最初・不成立なら本 feature 中断）**: 実 DB の active lgbm-039 で、serving と同一の X（`load_serving_model`→TE 適用後・`feature_cols` 順）に対し `booster.predict(X, pred_contrib=True)` の再構成 `contrib[:, :-1].sum(axis=1)+contrib[:, -1] ≈ booster.predict(X, raw_score=True)`（rel 1e-6）を確認。あわせて 1 レース分の pred_contrib レイテンシ実測（目標 +100ms 未満）。結果を research.md に追記（codex R1 レシピ、039 eval-spike 教訓）
- [X] T002 [P] [contracts/prediction-explanation.md](contracts/prediction-explanation.md) の JSONB スキーマ・API 契約・front 表示契約（禁止語/注記）・回帰境界を確定（spike の実測値で method_version/tolerance を最終化）

**Checkpoint**: pred_contrib の加法性とレイテンシが実モデルで成立 → 続行判定。

---

## Phase 2: Foundational（migration + 寄与計算の純関数 — US1 の前提）

- [X] T003 `db/migrations/versions/0008_prediction_explanation.py`（新）: `race_predictions` に nullable JSONB `explanation` 追加。downgrade は列 drop。`db/src/horseracing_db/models/prediction.py` の `RacePrediction` に `explanation: Mapped[dict | None] = mapped_column(JSONB)` 追加
- [X] T004 `training/src/horseracing_training/explanation.py`（新）: `compute_explanations(booster, X, feature_cols, *, k=5) -> list[dict | None]`。pred_contrib 実行 → per-row top-K（|contribution| 降順・feature 名昇順タイブレーク）→ other_contribution 合算 → 固定スキーマ dict（method="lgbm_pred_contrib"/method_version=1/k/base_value/score/other_contribution/items）。score は `predict(raw_score=True)` と一致（INV-E1 自己検証、不成立行は None + 警告）。value の JSON 化は serving `_jsonable` と同等（NaN→None・category→str）
- [X] T005 [P] `training/tests/unit/test_explanation.py`（新）: INV-E1（加法性 rel 1e-6）・INV-E3（決定論・タイブレーク）・k 切り詰めと other 合算・NaN/category 値の JSON 化・小データでの booster 実測（binary と cond_logit 両 objective）

**Checkpoint**: 説明の生成・検証が training 純関数として成立。

---

## Phase 3: User Story 1 - 各馬の予測根拠（スコア寄与 top-K） (P1, MVP)

**Goal**: 予測時に explanation を計算・永続化し、API 透過、front 馬行展開で表示。

**Independent Test**: 予測 1 レース生成 → race_predictions.explanation 非 NULL・加法性成立・win_prob バイト一致 → API が透過 → front 展開でスコア寄与+限界注記表示。旧 run は「未提供」。

### 実装

- [X] T006 [US1] `serving/src/horseracing_serving/predictor.py`: `predict_race` が booster あり時に `compute_explanations`（T004）を呼び per-horse explanation を第 3 戻り値で返す（degenerate モデル→全 None）。既存 predictions/snapshots の計算・値は不変
- [X] T007 [US1] `serving/src/horseracing_serving/persistence.py` + `pipeline.py`: `persist_run(..., explanations)` で `RacePrediction(explanation=...)` 保存。explanation None 行は NULL のまま。呼び出し側（run_serving/live）結線
- [X] T008 [P] [US1] `serving/tests/` 拡張: INV-E2（explanation 有無で win/top2/top3 バイト一致）・persist で JSONB 保存・degenerate→NULL・cond_logit モデルで score が margin（softmax 前）であること
- [X] T009 [US1] `api/src/horseracing_api/schemas.py`: `ExplanationItem`/`Explanation` pydantic モデル + `HorsePrediction.explanation: Explanation | None`。`api/src/horseracing_api/queries.py`: `run_predictions` に explanation 列追加。`routers/predictions.py`: 透過結線（再計算しない）
- [X] T010 [P] [US1] `api/tests/` 拡張: explanation 透過（NULL→null / 非 NULL→型付き）・既存レスポンス後方互換
- [X] T011 [US1] `front/openapi.json` 再生成 + `front/src/api/types.ts` 型再生成（openapi-typescript、drift-check 維持）
- [X] T012 [US1] `front/src/components/featureLabels.ts`（新）: 特徴名→{label, derived?} 単一対応表（全 model_input_features + te_* を列挙、未知キー fail-open）。`front/src/components/ExplanationPanel.tsx`（新）: 馬行展開のスコア寄与バー（正負色分け・日本語ラベル・値併記・te_*「導出特徴」バッジ・**限界注記 2 種常時表示**・NULL→「未提供」）。`RaceDetailPage.tsx` 結線（PredictionTable/HorseEntriesTable の行展開）
- [X] T013 [P] [US1] `front/src/components/ExplanationPanel.test.tsx`（新）: 表示・未提供・**named invariant「注記なしで寄与が render されない」**・導出特徴バッジ・fail-open ラベル

**Checkpoint**: MVP 完成 — スコア寄与が end-to-end で見える。

---

## Phase 4: User Story 2 - グローバル特徴量重要度 (P2)

**Goal**: gain 重要度を学習時に記録し、限定命名で表示。

**Independent Test**: train-evaluate 後の model_versions.metrics_summary に importance → GET /models/{mv}/importance 200、旧モデル 404 importance_unavailable。

### 実装

- [X] T014 [US2] `training/src/horseracing_training/artifacts.py`: `save_artifacts` で `metrics_summary["importance"] = {"type": "gain", "values": {feature: gain}}`（全列、booster なし=キー不在）。既存 summary キーと衝突しないことをテスト
- [X] T015 [US2] `api/src/horseracing_api/routers/importance.py`（新、021 calibration 同型）: `GET /models/{model_version}/importance` — 200（gain 降順・feature 昇順、`{model_version, type, values:[{feature, gain}]}`）/ 404 model_not_found / 404 importance_unavailable。`schemas.py` に ImportanceResponse、`app.py` に router 登録
- [X] T016 [P] [US2] `api/tests/test_importance.py`（新）: 200/404×2・ソート決定論・read-only（GET のみ）
- [X] T017 [US2] `front/src/components/ImportanceChart.tsx`（新）: 「分割利得（gain）重要度」限定命名で top-20 横棒（featureLabels 共有）、未収録→「収録されていない」表示。モデル情報表示（CalibrationChart 隣接）に結線 + openapi/types 再生成 + `ImportanceChart.test.tsx`

**Checkpoint**: モデル全体の傾向が監査可能。

---

## Phase 5: User Story 3 - モデル-市場乖離バッジ (P3)

**Goal**: p vs q の事前登録バンドを純事実比較バッジで表示（保存なし）。

**Independent Test**: p/q 揃い→バッジ、q 欠損 or canonical_consistent=false→非表示、境界値は similar 側。

### 実装

- [X] T018 [US3] `api/src/horseracing_api/selection.py`: 純関数 `divergence_band(p, q) -> str | None`（FR-011 事前登録: p < q−max(0.03,0.5q)→"market_higher" / p > q+max(0.03,0.5q)→"model_higher" / 他 "similar"、等号は similar）。`routers/predictions.py`: `HorsePrediction.divergence` 結線（q None or canonical_consistent=false → None）
- [X] T019 [P] [US3] `api/tests/test_divergence.py`（新）: バンド境界値（等号含む）・抑制 2 条件・決定論。**閾値定数が spec FR-011 と一致することを literal テストで固定**（事前登録の機械保証）
- [X] T020 [US3] `front/src/components/DivergenceBadge.tsx`（新）: 純事実比較文言 3 種のみ・similar は控えめ表示・ツールチップ「意見相違であり的中/利益の保証ではない」+ odds_as_of 参照・**禁止語（危険/妙味/買い/儲かる/弱気/強気/edge）不使用**・損益色/乖離ソートなし。HorseEntriesTable 結線 + openapi/types 再生成 + `DivergenceBadge.test.tsx`（named invariant「注記なしで render されない」+ 禁止語 grep）

**Checkpoint**: vault の「危険な人気馬/穴馬」要求が中立形で充足。

---

## Phase 6: Polish & 横断

- [X] T021 [P] `features/tests/unit/test_feature040_leak_guard.py`（新）: model_input_features に explanation/importance/divergence トークン不在（SC-007）・migration head が 0008・`__tablename__` 追加なし（既存 leak_guard 群の head assert 0007→0008 波及更新: test_feature020/021/023_leak_guard.py・test_materialize_fallback_columns.py・test_leakcheck 群）
- [X] T022 [P] `api/tests/`: read-only 維持（全 route GET のみ、021 テストの拡張で /importance 含む）
- [X] T023 実 DB e2e（quickstart §2-3）: migration 0008 適用 → serving 1 レース予測 → explanation JSONB 保存・加法性 SQL 検証・API curl 確認・旧 run 未提供
- [X] T024 [P] 全パッケージ lint/test 緑（db/features/training/serving/api/front）+ front drift-check（openapi.json/types.ts コミット済み一致）
- [X] T025 [P] `CLAUDE.md` 040 サマリ更新（結果・migration 0008・codex 反映）

---

## Dependencies & Execution Order

- **T001（spike）が全てをブロック** — 不成立なら中断。
- Phase 2（T003 migration・T004 explanation.py）→ US1。T004→T006。T003→T007。
- US1 内: T006→T007→T009→T011→T012（縦結線）。T005/T008/T010/T013 は各実装後 [P]。
- US2（T014-T017）・US3（T018-T020）は US1 の openapi 再生成（T011）以降なら独立・並行可（front 型再生成が共有点 — 最後にまとめて 1 回でも可）。
- Polish は最後。T021 の head assert 更新は T003 直後でも可（features テストが赤くなるため早めに）。

## Parallel 実行例

- T002[P] は T001 と並行可（契約文書化）。
- テスト系 T005/T008/T010/T013/T016/T019[P] は対応実装後に別ファイルで並行。
- US2 と US3 は API/front の別ファイルなので並行可。

## 実装戦略

1. **T0 spike で de-risk**（不成立なら撤退、コスト最小）。
2. MVP = Phase 2 + US1（スコア寄与 end-to-end）→ この時点でユーザー価値成立。
3. US2/US3 を独立増分で追加。
4. 憲法: II（leak-guard T021）/III（不変条件テスト群・FR-011 literal 固定 T019）/IV（009 非介入・p バイト一致 T008）/V（監査付帯・決定論・注記）/VI（migration 0008 正当化済み・API 契約先行 T011）。

## 注意

- explanation の score は **booster margin（raw_score=True）** — serving `raw_predict`（cond_logit で softmax 済み）とは別物。テストで取り違えを固定（T008）。
- feature_snapshots には explanation を混入しない（契約汚染、codex 却下案）。
- 乖離バンド閾値（FR-011）は実データの結果を見て動かさない。禁止語リストは front テストで grep 固定。
- 旧 run の explanation は NULL のまま（backfill 禁止 — 当時のモデルで再計算できない値を後付けしない）。
