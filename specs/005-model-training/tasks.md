---
description: "Task list for モデルトレーニングと校正"
---

# Tasks: モデルトレーニングと校正 (Model Training & Calibration)

**Input**: Design documents from `specs/005-model-training/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 含む。spec の Independent Test と憲法 II/III/IV/V のため test タスクを生成する。
**校正 fold 漏れ検査と確率整合性が最重要テスト**(過去 035/036 の校正ミス対策)。

**Source of truth**: モデル設計・校正 fold 安全・母集団・採用ゲートは research.md / data-model.md。
Predictor 契約・harness・baseline は Feature 003、leak-safe 特徴量は Feature 004。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(異なるファイル・依存なし)
- パスはリポジトリ root 基準。学習パッケージは `training/`

---

## Phase 1: Setup

- [X] T001 `training/` のディレクトリ構成を plan.md 通りに作成(`training/src/horseracing_training/`, `training/tests/{unit,integration}/`)
- [X] T002 `training/pyproject.toml` を作成し依存定義(`horseracing-db`/`horseracing-features`/`horseracing-eval` をパス依存、lightgbm, scikit-learn>=1.4, numpy, pandas, sqlalchemy>=2.0。dev: pytest, testcontainers[postgres], ruff)
- [X] T003 [P] `training/pyproject.toml` に ruff 設定と `[tool.pytest.ini_options]`(integration マーカー)を追加

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 完了までユーザーストーリー着手不可

- [X] T004 `eval/src/horseracing_eval/baselines.py` の `_harville_topk` を公開関数 `harville_topk` に変更(非破壊、内部参照も更新)し、`eval` のテストが通ることを確認。training から再利用する(research R8)
- [X] T005 `training/src/horseracing_training/dataset.py`: Feature 004 `build_feature_matrix`(started 母集団)+ win ラベル(`race_results` から started 全頭、finished&finish_order==1=1 else 0=DNF 含む)を結合。X=`model_input_features()`(research R3/R10)
- [X] T006 `training/tests/conftest.py`: testcontainers PostgreSQL16 + `db/` alembic を head まで適用、session、テスト間 truncate、合成データ投入ヘルパ

**Checkpoint**: 基盤完成

---

## Phase 3: User Story 1 - LightGBM win モデルを walk-forward 学習し Predictor として評価 (Priority: P1) 🎯 MVP

**Goal**: 単一 win LightGBM(started 全頭・DNF=0)を学習し、raw→clip→正規化→Harville で Predictor を実装、評価ハーネスで walk-forward 評価。

**Independent Test**: 合成データで確率整合性を満たし、ResultMarket/結果確定オッズを参照せず、walk-forward 評価が完走して label 別指標が出る。

### Tests for User Story 1 ⚠️

- [X] T007 [P] [US1] ユニット: `predict_race` 出力が全レースで `0<=win<=top2<=top3<=1`・レース内合計が harness 許容内(`check_consistency` を通る)、端点(win≈0/1)で clip が効く(SC-001)— `training/tests/unit/test_consistency.py`
- [X] T008 [P] [US1] ユニット(リーク検査): Predictor が `model_input_features()` のみを使い、`ResultMarket`(結果確定 odds/popularity)を参照しない — `training/tests/unit/test_leak.py`
- [X] T009 [P] [US1] 統合: 合成多年データで harness 経由の walk-forward 評価が完走し label 別指標が出る、決定論(同一 seed で一致)— `training/tests/integration/test_train_eval.py`

### Implementation for User Story 1

- [X] T010 [US1] `training/src/horseracing_training/win_model.py`: LightGBM win 学習(seed 固定、`deterministic=True`、固定ハイパラ)
- [X] T011 [US1] `training/src/horseracing_training/predictor.py`: `LightGBMPredictor.fit/predict_race`(MVP は校正なし=identity、raw→clip→レース内正規化→`harville_topk`)。Feature 003 Predictor 契約を満たす
- [X] T012 [US1] `predictor.py` で `from horseracing_eval.baselines import harville_topk` を使い top2/top3 を導出(再実装しない)

**Checkpoint**: US1 単独で win モデルが Predictor として評価可能

---

## Phase 4: User Story 2 - 校正器を train-only で fit し ECE を改善 (Priority: P1)

**Goal**: Platt(既定)校正を train 内 時系列 held-out で fit(valid/test 不使用)、raw→校正→clip→正規化→Harville。

**Independent Test**: 校正器が valid 期間を使わない(fold 漏れ検査)、校正後も整合性、校正で win ECE 改善。

### Tests for User Story 2 ⚠️

- [X] T013 [P] [US2] ユニット(最重要): 校正器が train 内 held-out のみで fit され、**valid 期間に極端な結果を仕込んでも校正器(校正写像)が変化しない**(fold 漏れ検査、SC-002)。「変化しない」は校正器パラメータ(および校正後 valid 予測)が float eps 内で完全一致と定義し assert する。isotonic 選択時も clip で端点崩れを防ぐ — `training/tests/unit/test_calibration_foldleak.py`
- [X] T014 [P] [US2] ユニット: 合成の誤校正データで Platt 校正後に win ECE が改善する — `training/tests/unit/test_calibration_ece.py`

### Implementation for User Story 2

- [X] T015 [US2] `training/src/horseracing_training/calibration.py`: Platt(既定)/ isotonic、train 内 時系列 held-out で fit(valid/test 不参照)。clip 併用
- [X] T016 [US2] `predictor.py` を更新: `fit` で train を model-fit / calibration-fit に時系列分割、校正器を fit。`predict_race` で raw→校正→clip→正規化→Harville の順(INV-T1)

**Checkpoint**: US1+US2 が独立して機能(校正済み・整合性保持)

---

## Phase 5: User Story 3 - baseline 比較 + ECE で採用判定し model_versions に保存 (Priority: P1)

**Goal**: 評価結果を baseline と同一条件で gate 判定し、model_versions + artifacts に保存。

**Independent Test**: ゲート合格が active・不合格が candidate、model_versions 行 + artifacts が保存され再現情報が揃う。

### Tests for User Story 3 ⚠️

- [X] T017 [P] [US3] ユニット: 採用ゲート(win LogLoss<baseline 厳密 + top2/top3 LogLoss<=baseline + win ECE<=閾値)が合格→adopted、不合格→not で判定(SC-004)— `training/tests/unit/test_adoption_gate.py`
- [X] T018 [P] [US3] 統合: `save_model_version` が `model_versions` 行(model_family='lightgbm', adoption_status, metrics_summary)+ artifacts(model.txt/calibrator.pkl/metadata.json)を保存し再読込でき、metadata に seed/params/fold/校正方式/feature_version/feature hash/git sha がある(SC-005/FR-013)— `training/tests/integration/test_save_model.py`

### Implementation for User Story 3

- [X] T019 [US3] `training/src/horseracing_training/adoption.py`: `AdoptionGate`/`evaluate_gate`(全 label + ECE、閾値設定可能)。baseline は model_versions の market/uniform metrics_summary を参照
- [X] T020 [US3] `training/src/horseracing_training/artifacts.py`: `save_model_version`(model_versions upsert + artifacts dir + metadata.json、weights_uri/calibrator_uri)
- [X] T021 [US3] `training/src/horseracing_training/cli.py` + `__main__.py`: `train-evaluate`(walk-forward 学習+校正→harness 評価→gate→保存、サマリ表示)

**Checkpoint**: US1+US2+US3 = MVP(学習→校正→評価→採用→保存)完了

---

## Phase 6: User Story 4 - ハイパラ探索と OOF target encoding (Priority: P2)

**Goal**: train 内 CV でハイパラ選択(valid 不使用)、OOF target encoding の正しい統合。

**Independent Test**: ハイパラ選択が valid を使わない、OOF encoding が train 内未来を漏らさない。

### Tests for User Story 4 ⚠️

- [X] T022 [P] [US4] ユニット: ハイパラ探索が train 内 CV のみで選択し valid を使わない、OOF target encoding が fit-all-train→apply-all-train を避け train 内未来を漏らさない — `training/tests/unit/test_hpo_oof.py`

### Implementation for User Story 4

- [X] T023 [US4] `training/src/horseracing_training/hpo.py`(train 内 CV)+ OOF target encoding 統合(Feature 004 encoding を OOF で使用)

**Checkpoint**: ハイパラ探索・OOF encoding が機能

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T024 [P] 統合: 同一データ・同一 fold・同一 seed で学習→評価を 2 回実行し指標が完全一致(決定論、SC-006)— `training/tests/integration/test_determinism.py`
- [X] T025 [P] `training/README.md` を作成(CLI・テスト・依存・校正 fold 安全・母集団・採用ゲート)
- [X] T026 ruff クリーン + 全テスト green を確認(`training/` と `eval/`(T004 で変更)と `db/`: `uv run ruff check`, `uv run pytest`)
- [X] T027 (ローカル・任意) 実データ(取込済み 2007+2008、baseline 保存済み)で `train-evaluate` を実行し、LightGBM が uniform baseline を win LogLoss で上回り、model_versions + artifacts に保存され、決定論を確認

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし
- **Foundational (Phase 2)**: Setup 完了に依存。全ストーリーを BLOCK(harville 公開化/dataset/conftest)
- **US1 (Phase 3)**: Foundational 後。win モデル + Predictor の中核(MVP)
- **US2 (Phase 4)**: US1(predictor)に校正を差す
- **US3 (Phase 5)**: US1/US2(評価可能な校正済みモデル)に採用判定+保存を追加
- **US4 (Phase 6)**: US1-US3 の後。P2
- **Polish (Phase 7)**: 望むストーリー完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後に着手。他ストーリー非依存
- **US2 (P1)**: US1 の predictor に校正を統合(同一ファイル編集)
- **US3 (P1)**: US1/US2 の評価結果を gate 判定し保存
- **US4 (P2)**: US1-US3 の後

### Within Each User Story

- テストを先に書き FAIL を確認 → 実装
- **校正 fold 漏れ検査(T013)と確率整合性(T007)を最優先で固定**してから実装
- dataset/harville(基盤)→ win_model → predictor → calibration → adoption/artifacts/cli の順

### Parallel Opportunities

- Setup の T003、各ストーリーの test タスク [P] は並列可
- US1 完了後、US2 と US3 は predictor を編集するため順次推奨
- Polish の T024/T025 は並列可

---

## Parallel Example: User Story 1

```bash
# US1 テストを並列起動(先に FAIL 確認):
Task: "unit consistency in training/tests/unit/test_consistency.py"
Task: "unit leak check in training/tests/unit/test_leak.py"
Task: "integration train-eval in training/tests/integration/test_train_eval.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 + US3 = P1)

1. Setup → Foundational(harville 公開化・dataset・conftest)
2. US1: win LightGBM + 整合性(clip/正規化/Harville)+ リーク検査 → harness 評価
3. US2: 校正 train-only(fold 漏れ検査を最優先)→ predictor に統合
4. US3: 採用ゲート + model_versions/artifacts 保存
5. ここで「学習→校正→評価→採用→保存」の完全ループが完成(評価先行の到達点)

### Incremental Delivery

1. Setup + Foundational
2. US1 → win モデル Predictor(整合性)
3. US2 → 校正(fold 安全、ECE 改善)
4. US3 → 採用判定・保存(MVP 完成)
5. US4 → ハイパラ探索・OOF encoding
6. Polish → 決定論・README・実データスモーク

---

## Notes

- [P] = 異なるファイル・依存なし
- **校正 fold 漏れ検査(T013)が本 feature 最重要**(035/036 の再発防止)。valid に好成績を仕込んでも校正器が変わらないことを assert
- 単一 win + clip + 正規化 + Harville で確率整合性を機構保証(Harville は eval の公開関数を再利用)
- 学習母集団 = started 全頭・DNF→win=0。評価ハーネスは finished 採点の母集団ミスマッチを既知バイアスとして記録
- 結果確定 odds/popularity・ResultMarket をモデルが参照しない(リーク検査 T008)
- 採用ゲート構造は固定、ECE 閾値は設定可能(実データ確定)。target encoding は MVP 不使用(US4)
