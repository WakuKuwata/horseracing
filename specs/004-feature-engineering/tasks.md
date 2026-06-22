---
description: "Task list for 特徴量生成 (Feature Engineering)"
---

# Tasks: 特徴量生成 (Feature Engineering)

**Input**: Design documents from `specs/004-feature-engineering/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 含む。spec の Independent Test と憲法 II(リーク防止 NON-NEGOTIABLE)の leakage test のため
test タスクを生成する。**リーク検査が最重要テスト**(過去 035/036 の校正ミス記録を踏まえる)。

**Source of truth**: as-of 機構・特徴量定義・欠損/フラグ・registry は research.md / data-model.md。
スキーマ・labels・validation は Feature 001(`db/`)。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(異なるファイル・依存なし)
- パスはリポジトリ root 基準。特徴量パッケージは `features/`

---

## Phase 1: Setup

- [X] T001 `features/` のディレクトリ構成を plan.md 通りに作成(`features/src/horseracing_features/`, `features/tests/{unit,integration}/`)
- [X] T002 `features/pyproject.toml` を作成し依存定義(`horseracing-db` をパス依存 `../db`、pandas, numpy, sqlalchemy>=2.0。dev: pytest, testcontainers[postgres], ruff)
- [X] T003 [P] `features/pyproject.toml` に ruff 設定と `[tool.pytest.ini_options]`(integration マーカー)を追加

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 完了までユーザーストーリー着手不可

- [X] T004 `features/src/horseracing_features/registry.py`: `AvailabilityTiming`(pre_entry/post_frame/post_weight/post_odds/pre_race/post_result)、`FeatureMeta`、空の `REGISTRY` 雛形、`model_input_features()`(post_result 除外)の枠を作成(contracts/feature_matrix.md)
- [X] T005 `features/src/horseracing_features/schema.py`: 固定列名の一覧 + フラグ列(has_past_race/is_debut/past_race_count/is_low_history)定義(data-model.md に一致)
- [X] T006 `features/src/horseracing_features/loader.py`: `load_frames(session, start_date, end_date)` で races/race_horses/race_results を pandas DataFrame に一括ロード(2007+、`is_in_ingest_scope` 整合)
- [X] T007 `features/tests/conftest.py`: testcontainers PostgreSQL16 + `db/` alembic を head まで適用、session、テスト間 truncate、合成データ投入ヘルパ

**Checkpoint**: 基盤完成

---

## Phase 3: User Story 1 - リーク安全な過去成績特徴量を固定スキーマで生成 (Priority: P1) 🎯 MVP

**Goal**: as-of(race_date < R)で過去成績/履歴件数特徴量 + 発走前静的特徴量を固定スキーマで生成し、欠損は null(0と区別)。

**Independent Test**: 合成データで R の特徴が race_date>=R を 1 件も使わない(リーク検査)、新馬 null≠0、完走前提系が非完走除外。

### Tests for User Story 1 ⚠️

- [X] T008 [P] [US1] ユニット(最重要): **リーク検査** — R より後、および**同日別レース**に好成績を仕込んでも R の全過去成績/履歴特徴量が変化しない(avg_finish/win_rate/prev_finish に加え **days_since_last・career_starts・cancel/exclude/stop_count も**、INV-F1 は全 history 特徴を対象, SC-001)。完走前提系が非完走(中止/失格)・非出走(取消/除外)を除外、career_starts は started を数える(SC-003)— `features/tests/unit/test_history_asof.py`
- [X] T009 [P] [US1] ユニット: 新馬の過去成績系が NaN(0 でない)、is_debut/has_past_race/past_race_count/is_low_history が正しい(SC-002)。履歴件数系は 0 埋め可 — `features/tests/unit/test_missing_flags.py`
- [X] T010 [P] [US1] ユニット: 同一入力・同一 as-of で feature matrix が 2 回生成して完全一致(SC-005)— `features/tests/unit/test_determinism.py`

### Implementation for User Story 1

- [X] T011 [US1] `features/src/horseracing_features/history.py`: `build_history_features`(as-of race_date<R、日単位集約 + distinct-date シフトで同日除外、完走前提=finished のみ/career_starts=started/件数=別系統、出走歴ゼロは null。research R1/R2/R3)
- [X] T012 [US1] `features/src/horseracing_features/static_features.py`: `build_static_features`(レース条件・馬属性・枠・馬体重。各列の timing は metadata に従う)
- [X] T013 [US1] `features/src/horseracing_features/builder.py`: `build_feature_matrix(session, *, start_date, end_date, low_history_max=2)` で static+history+件数+フラグを結合し固定スキーマの DataFrame を返す(決定論的)

**Checkpoint**: US1 単独でリーク安全な feature matrix が生成・テスト可能

---

## Phase 4: User Story 2 - 特徴量メタデータを宣言・強制 (Priority: P1)

**Goal**: 全特徴列が registry に metadata を宣言し、未登録・結果後混入・結果確定オッズ混入を fail-fast。

**Independent Test**: 全列が registry に metadata を持ち、未登録列・結果確定オッズ混入が fail-fast、post_result が model_input から除外される。

### Tests for User Story 2 ⚠️

- [X] T014 [P] [US2] ユニット: feature matrix の全列が REGISTRY に登録され metadata を持つ。未登録列で `FeatureSchemaError`、結果確定 odds/popularity がモデル特徴量に含まれない(SC-004)。**post_result 除外**は MVP に実 post_result 列が無いため、合成的に注入した post_result registry エントリで `model_input_features()` から除外されることを検証(INV-F5)— `features/tests/unit/test_registry.py`

### Implementation for User Story 2

- [X] T015 [US2] `registry.py` の `REGISTRY` に data-model.md の全特徴列の `(source, availability_timing, missing_policy)` を登録
- [X] T016 [US2] `builder.py` に registry 強制を追加: 生成した全列が REGISTRY に登録済みかを検証し未登録は `FeatureSchemaError`(fail-fast)。`model_input_features()` で post_result と識別列(race_id/horse_id)を除外。結果確定 odds/popularity を特徴量集合に含めない

**Checkpoint**: US1+US2 が独立して機能(P1 = MVP 完了、リーク安全 + メタデータ強制)

---

## Phase 5: User Story 3 - カテゴリ target encoding を train-only で計算 (Priority: P2)

**Goal**: 騎手/調教師/開催場の target encoding を train 境界より前のみで fit。

**Independent Test**: encoding が valid 期間の結果を使わない、未知カテゴリは既定値。

### Tests for User Story 3 ⚠️

- [X] T017 [P] [US3] ユニット: `fit_target_encoding(train_cutoff)` が cutoff 以降の結果を使わない、未知カテゴリは既定値(全体平均等)で 0 埋め/エラーにしない(SC-006)— `features/tests/unit/test_encoding.py`

### Implementation for User Story 3

- [X] T018 [US3] `features/src/horseracing_features/encoding.py`: `fit_target_encoding(frames, *, train_cutoff)`(train 境界前のみで fit、未知カテゴリ既定値)

**Checkpoint**: target encoding が train-only で機能

---

## Phase 6: User Story 4 - feature matrix を materialize (Priority: P2)

**Goal**: date range / fold 単位で materialize し再現的にキャッシュ。

**Independent Test**: materialize した matrix が on-the-fly と完全一致。

### Tests for User Story 4 ⚠️

- [X] T019 [P] [US4] 統合: materialize した feature matrix が on-the-fly 計算と完全一致し再現的。**serialize→reload 往復で dtype・列順・NaN(≠0)が保存される**ことも assert(INV-F6 を往復越しに担保)— `features/tests/integration/test_materialize.py`

### Implementation for User Story 4

- [X] T020 [US4] `features/src/horseracing_features/cli.py` + materialize: `build-features --from --to`(parquet 等にキャッシュ、非破壊)

**Checkpoint**: materialize が機能

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T021 [P] 統合: 実 DB(testcontainers、合成多年データ)で as-of リーク検査 — R の特徴が race_date>=R を使わないことを実 DB 経路で確認 — `features/tests/integration/test_asof_real_db.py`
- [X] T022 [P] `features/README.md` を作成(API・テスト・依存・リーク方針・odds 非特徴量化)
- [X] T023 ruff クリーン + 全テスト green を確認(`features/` と `db/`: `uv run ruff check`, `uv run pytest`)
- [X] T024 (ローカル・任意) 実データ検証済み: 2007+2008 取込(errors=0)→ feature matrix 50,110行×33列。odds/popularity 列なし、is_debut=5014/avg_finish NaN=5017(新馬 NaN≠0)、model_input が識別列除外、2回ビルドで決定論一致を確認

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし
- **Foundational (Phase 2)**: Setup 完了に依存。全ストーリーを BLOCK(registry/schema/loader/conftest)
- **US1 (Phase 3)**: Foundational 後。特徴量生成の中核(MVP)
- **US2 (Phase 4)**: US1(builder/全列)に依存。registry 強制を差す
- **US3 (Phase 5)**: Foundational(loader)に依存。US1/US2 非依存(別ファイル)
- **US4 (Phase 6)**: US1/US2(完成 matrix)に依存
- **Polish (Phase 7)**: 望むストーリー完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後に着手。他ストーリー非依存
- **US2 (P1)**: US1 の builder/列に metadata 強制を追加(MVP 完成)
- **US3 (P2)**: loader を使う target encoding。US1/US2 非依存(並列可)
- **US4 (P2)**: 完成 matrix を materialize。US2 後

### Within Each User Story

- テストを先に書き FAIL を確認 → 実装
- registry/schema/loader(基盤)→ history/static → builder の順
- **リーク検査(T008)を最優先で固定**してから実装

### Parallel Opportunities

- Setup の T003、各ストーリーの test タスク [P] は並列可
- US1 完了後、US3 は US1/US2 と並列可(別ファイル)
- Polish の T021/T022 は並列可

---

## Parallel Example: User Story 1

```bash
# US1 テストを並列起動(先に FAIL 確認):
Task: "unit leak check in features/tests/unit/test_history_asof.py"
Task: "unit missing/flags in features/tests/unit/test_missing_flags.py"
Task: "unit determinism in features/tests/unit/test_determinism.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 = P1)

1. Setup → Foundational
2. US1: リーク検査テストを固定 → history(as-of)/static/builder を実装 → 検証
3. US2: 全特徴列の metadata を registry に登録 → builder で強制(未登録 fail-fast、post_result 除外)→ 検証
4. ここで「リーク安全 + メタデータ宣言済み」の feature matrix が完成 → 学習(005)が着手可

### Incremental Delivery

1. Setup + Foundational
2. US1 → リーク安全な特徴量(MVP の核)
3. US2 → メタデータ強制(MVP 完成)
4. US3 → target encoding(train-only)
5. US4 → materialize
6. Polish → 実 DB リーク検査・README・実データスモーク

---

## Notes

- [P] = 異なるファイル・依存なし
- **リーク検査(T008/T021)が本 feature 最重要**。未来/同日の好成績を仕込んでも特徴が変わらないことを assert
- as-of 機構・特徴量定義・欠損/フラグ・registry は research.md / data-model.md を正本
- 結果確定 odds/popularity はモデル特徴量に使わない(評価専用、Feature 003 と一致)。混入は registry 未登録で検出
- 欠損は null(Unknown)で 0 と区別。新馬は is_debut、完走前提系は非完走除外、履歴件数は別系統(0 可)
- 価値検証(baseline 超え)は学習(005)へ委譲。本 feature は正しさ・リーク安全・欠損・メタデータまで
