---
description: "Task list for 予測 serving(推論専用パイプライン)"
---

# Tasks: 予測 serving(推論専用パイプライン)

**Input**: Design documents from `specs/006-prediction-serving/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 含む。spec の Independent Test と憲法 II/IV/V のため test タスクを生成する。
**前処理器 save/load 往復・リーク/as-of・決定論が最重要テスト**(codex BLOCKER 由来)。

**Source of truth**: ロード経路・前処理器保存・as-of・母集団・logic_version・snapshot 内容は research.md /
data-model.md / contracts/。特徴は Feature 004、推論順序・純部品は Feature 005、整合性は Feature 003。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(異なるファイル・依存なし)
- パスはリポジトリ root 基準。serving パッケージは `serving/`

---

## Phase 1: Setup

- [X] T001 `serving/` のディレクトリ構成を plan.md 通りに作成(`serving/src/horseracing_serving/`, `serving/tests/{unit,integration}/`)
- [X] T002 `serving/pyproject.toml` を作成し依存定義(`horseracing-db`/`horseracing-features`/`horseracing-eval`/`horseracing-training` をパス依存、lightgbm, scikit-learn>=1.4, numpy, pandas, sqlalchemy>=2.0。dev: pytest, testcontainers[postgres], ruff)
- [X] T003 [P] `serving/pyproject.toml` に ruff 設定と `[tool.pytest.ini_options]`(integration マーカー、tests E501 ignore)を追加

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 完了までユーザーストーリー着手不可

- [X] T004 `training/src/horseracing_training/artifacts.py` を**非破壊拡張**し `preprocessor.pkl` を保存(feature_cols 列順・categorical_cols・target_encode_cols・te_smoothing・encoders・feature_version・feature_hash)。既存 model.txt/calibrator.pkl/metadata.json・model_versions スキーマは不変。training の既存テストが緑のまま + preprocessor 保存・再読込を検証(BLOCKER 解消、contracts/artifacts.md)
- [X] T005 `serving/tests/conftest.py`: testcontainers PostgreSQL16 + `db/` alembic head + session + テスト間 truncate + 合成データ投入 + 「active モデル + 成果物(model.txt/calibrator.pkl/preprocessor.pkl)」を training 経由で作るヘルパ
- [X] T006 `serving/src/horseracing_serving/model_loader.py`: `resolve_model_version`(active 0/1/複数 + 明示)+ `load_serving_model`(model.txt/calibrator.pkl/preprocessor.pkl ロード、preprocessor 欠落時の後方互換再構成=TE 不使用なら feature_cols 再構成)。`ServingModel` を返す(contracts/serving.md, R2/R3/R4)

**Checkpoint**: 基盤完成(成果物に前処理器・active 解決・ロード)

---

## Phase 3: User Story 1 - 指定レースを推論して永続化 (Priority: P1) 🎯 MVP

**Goal**: race_id 指定で active モデル推論 → 整合的な win/top2/top3 を 3 テーブルに保存。

**Independent Test**: ある race_id を推論実行し、prediction_runs/race_predictions/feature_snapshots に行が作られ、
各馬の確率が整合性(0<=win<=top2<=top3<=1・Σ 許容内・PROB_MONOTONIC)を満たす。

### Tests for User Story 1 ⚠️

- [X] T007 [P] [US1] ユニット: `ServingPredictor.predict_race` 出力が全馬 `0<=win<=top2<=top3<=1`・レース内合計が許容内(`check_consistency` 通過)、端点で clip、出走全頭に予測(欠落なし)。**エッジ: 小頭数 N<3 で目標和 `min(k,N)` を満たす**、**デビュー馬の欠損特徴(NaN)を 0 と混同せず推論できる**(spec Edge Cases)— `serving/tests/unit/test_predict_consistency.py`
- [X] T008 [P] [US1] 統合: race_id 指定で `run_serving` が prediction_runs に 1 行 + race_predictions(PROB_MONOTONIC)+ feature_snapshots(前処理後 model-input ベクトル + feature_version)を保存。**prediction_runs.logic_version が `feat=...;serve=...` 形式で記録される**ことを assert(FR-014)— `serving/tests/integration/test_run_serving.py`

### Implementation for User Story 1

- [X] T009 [US1] `serving/src/horseracing_serving/predictor.py`: `ServingPredictor.predict_race`(started 整列 → encoders 適用 → booster.predict → 校正 → clip → 正規化 → `harville_topk`。`assemble_predictions` 再利用。前処理後ベクトル + `_raw_win`/`_calibrated_win` の snapshot を生成。session 非依存)
- [X] T010 [US1] `serving/src/horseracing_serving/persistence.py`: prediction_runs + race_predictions + feature_snapshots を append-only 書き込み(uuid run、computed_at=now)
- [X] T011 [US1] `serving/src/horseracing_serving/pipeline.py`: `run_serving(race_id=...)`(load_serving_model → `build_feature_matrix(end_date=対象日)` → 対象 race 行抽出 → predict_race → `check_consistency` → persist)。`logic_version`(`feat=<feature_version>;serve=<SERVING_LOGIC_VERSION>`)を構成・記録
- [X] T012 [US1] `serving/src/horseracing_serving/cli.py` + `__main__.py`: `predict --race-id`(サマリ表示:保存件数・整合性)

**Checkpoint**: US1 単独で 1 レースの推論 → 整合性 → 3 テーブル保存が成立(MVP の中核)

---

## Phase 4: User Story 2 - リーク無し・決定論・学習特徴と一致 (Priority: P1)

**Goal**: 結果情報を一切使わず、同一入力で同一出力、学習特徴スキーマと一致した時のみ推論。

**Independent Test**: (a) 結果(着順/odds/popularity)を変えても予測不変・未来データ挿入でも不変(as-of)、
(b) 同一(race, model, logic_version)2 回で race_predictions 完全一致、(c) feature_hash 不一致 / TE モデルの
前処理器欠落で fail-fast。

### Tests for User Story 2 ⚠️

- [X] T013 [P] [US2] ユニット(最重要): 前処理器 save→load 往復で予測が in-memory(training の predict_race)と一致。**TE 使用モデルで preprocessor.pkl 欠落 → fail-fast**、**feature_hash/feature_version 不一致 → fail-fast** — `serving/tests/unit/test_loader_validate.py`
- [X] T014 [P] [US2] 統合(リーク+as-of): 対象レースの着順/odds/popularity を変更しても `race_predictions` 不変。対象日より後のレース/結果を挿入しても当該レース予測不変 — `serving/tests/integration/test_leak_asof.py`
- [X] T015 [P] [US2] 統合(決定論): 同一(race, model, logic_version)で 2 回 `run_serving` → race_predictions が完全一致(append-only で 2 run 存在)— `serving/tests/integration/test_determinism.py`

### Implementation for User Story 2

- [X] T016 [US2] `model_loader.py` に fail-fast 検証を追加: 現行 `model_input_features()` の feature_hash と保存 feature_hash / feature_version 不一致で `ServingError`、TE 使用かつ preprocessor 欠落で `ServingError`(INV-S4、contracts/artifacts.md)

**Checkpoint**: US1+US2 が独立して機能(リーク無し・決定論・スキーマ整合 fail-fast)

---

## Phase 5: User Story 3 - 日付一括推論 + active モデル解決 (Priority: P2)

**Goal**: --date で当日の対象レース全件を推論・保存。active 0/複数の解決ルール。

**Independent Test**: ある日付で当日複数レースが推論・保存(各レース prediction_runs 1 行)、active 0/複数で
明確なエラー + `--model-version` 明示要求。

### Tests for User Story 3 ⚠️

- [X] T017 [P] [US3] 統合: `--date` で当日複数レースが推論・保存(各レース prediction_runs 1 行)。active が 0/複数のとき fail + `--model-version` 明示要求。明示指定で動作 — `serving/tests/integration/test_date_and_resolve.py`

### Implementation for User Story 3

- [X] T018 [US3] `pipeline.py` の `run_serving` に `date` 分岐(当日の対象レース全件、end_date=当日で 1 度構築し race_date==当日を抽出)
- [X] T019 [US3] `cli.py` に `--date` / `--model-version` と active 解決エラー時のメッセージ(明示指定を促す)

**Checkpoint**: US1+US2+US3 = 単一/一括推論 + active 解決が完成

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T020 [P] `serving/README.md` を作成(概要・CLI・テスト・リーク/as-of/決定論・前処理器成果物・active 解決・logic_version)
- [X] T021 ruff クリーン + 全テスト green を確認(`serving/` と `training/`(T004 で変更): `uv run ruff check`, `uv run pytest`)
- [X] T022 (ローカル・任意) 実データ(active `lightgbm-win-v1`、TE 不使用)で `predict --race-id` と `predict --date` を実行し、3 テーブル保存・決定論・リーク検査を確認。preprocessor 後方互換再構成パス(preprocessor.pkl 無し + feature_hash 一致)も検証

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし
- **Foundational (Phase 2)**: Setup 完了に依存。全ストーリーを BLOCK(artifacts 拡張 / conftest / loader)
- **US1 (Phase 3)**: Foundational 後。推論 + 永続化の中核(MVP)
- **US2 (Phase 4)**: US1(loader/pipeline)にリーク検査・決定論・fail-fast を追加
- **US3 (Phase 5)**: US1/US2 の上に日付一括 + active 解決
- **Polish (Phase 6)**: 望むストーリー完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後に着手。中核
- **US2 (P1)**: US1 の loader/pipeline を硬化(同一ファイル編集あり)
- **US3 (P2)**: US1/US2 の後

### Within Each User Story

- テストを先に書き FAIL を確認 → 実装
- **前処理器往復(T013)・リーク/as-of(T014)・決定論(T015)を最優先で固定**
- loader(基盤)→ predictor → persistence → pipeline → cli の順

### Parallel Opportunities

- Setup の T003、各ストーリーの test タスク [P] は並列可
- US1 完了後、US2 と US3 は pipeline/loader を編集するため順次推奨
- Polish の T020 は並列可

---

## Implementation Strategy

### MVP First (US1 + US2 = P1)

1. Setup → Foundational(artifacts 拡張・conftest・loader)
2. US1: 推論 + 整合性 + 3 テーブル保存(race_id 指定)
3. US2: リーク検査・決定論・スキーマ fail-fast
4. ここで「採用モデルで未来レースを推論し監査可能に記録する」完全ループが完成

### Incremental Delivery

1. Setup + Foundational
2. US1 → 単一レース推論 + 永続化
3. US2 → リーク無し・決定論・整合 fail-fast(MVP 完成)
4. US3 → 日付一括 + active 解決
5. Polish → README・実データスモーク

---

## Notes

- [P] = 異なるファイル・依存なし
- **前処理器 save/load 往復(T013)が本 feature 最重要**(codex BLOCKER: TE モデルの encoder 復元)。
- as-of は Feature 004、推論順序・純部品(booster/Calibrator/TargetEncoder/assemble_predictions)は Feature 005 を再利用(再実装しない)
- リーク検査 = 結果由来情報(ResultMarket/race_results)をモデル入力に使わない + 未来 as-of(T014)
- 再実行は破壊的 upsert せず append-only 新 run(T015 で 2 run 確認)
- スキーマ変更なし。成果物拡張(preprocessor.pkl)は非破壊。推奨/ROI は Feature 007 へ
