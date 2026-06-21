---
description: "Task list for Core DB スキーマと基盤テーブル契約"
---

# Tasks: Core DB スキーマと基盤テーブル契約

**Input**: Design documents from `specs/001-core-db-schema/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 含む。spec の各 Independent Test と憲法の品質ゲート (leakage / 時系列 split / 確率整合性 /
ラベル導出の各 test) が必須のため、test タスクを生成する。

**Organization**: User Story 単位。US1/US2 は P1 (MVP)、US3/US4 は P2。各ストーリーは独立して
テスト可能。

**Source of truth**: 列定義・制約・状態コードの正本は **data-model.md** とする (FR-001 が参照する
`docs/database.md` は T002 で repo に同期するが、実装時の確定仕様は data-model.md)。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可 (異なるファイル・未完依存なし)
- **[Story]**: US1〜US4。Setup / Foundational / Polish にはストーリーラベルなし
- パスはリポジトリ root 基準。共有データパッケージは `db/`

## マイグレーション分割

ストーリー単位の 3 リビジョン (詳細は data-model.md「マイグレーション」を正とする):
`0001_core_schema` (US1/US2) / `0002_ingestion_id_schema` (US3) / `0003_prediction_contract` (US4)。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: `db/` パッケージの初期化・依存導入・docs 同期

- [X] T001 `db/` のディレクトリ構成を plan.md 通りに作成 (`db/src/horseracing_db/models/`, `db/src/horseracing_db/sql/`, `db/migrations/versions/`, `db/tests/unit/`, `db/tests/integration/`)
- [X] T002 [P] Obsidian Vault の docs を repo `docs/` に同期 (database.md / modeling.md / odds-roi.md / data-sources.md / scraping-netkeiba.md / architecture.md / open-decisions.md / overview.md) — FR-001 の参照先を repo 内で解決 (research R11)。確定仕様は data-model.md
- [X] T003 `db/pyproject.toml` を作成し依存を定義 (sqlalchemy>=2.0, alembic, psycopg[binary]>=3。dev: pytest, testcontainers[postgres], ruff)
- [X] T004 [P] `db/pyproject.toml` に lint/format/pytest 設定 (ruff 設定、`[tool.pytest.ini_options]`) を追加
- [X] T005 Alembic を初期化: `db/alembic.ini` と `db/migrations/env.py` を `horseracing_db.base` の metadata と `DATABASE_URL` 環境変数に接続

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 全ストーリーが依存する基盤モジュール

**⚠️ CRITICAL**: このフェーズ完了までユーザーストーリー着手不可

- [X] T006 `db/src/horseracing_db/base.py` を作成: `DeclarativeBase` + 制約命名規約 (ck/uq/ix/fk/pk の naming_convention)
- [X] T007 [P] `db/src/horseracing_db/enums.py` を作成: 状態コード定数 (EntryStatus, ResultStatus, MappingStatus, JobStatus, AdoptionStatus, Source, EntityType, BetType=7券種) — data-model.md の状態コード体系に一致
- [X] T008 [P] `db/src/horseracing_db/constraints.py` を作成: CHECK 式文字列と制約名を一元定義 (data-model.md の制約名に一致)
- [X] T009 [P] `db/src/horseracing_db/session.py` を作成: `DATABASE_URL` から engine と sessionmaker を生成
- [X] T010 `db/src/horseracing_db/sql/triggers.py` を作成: `set_updated_at()` 関数 SQL と、テーブルへ `BEFORE UPDATE` トリガを発行するヘルパ (各 migration が利用)
- [X] T011 `db/tests/conftest.py` を作成: testcontainers PostgreSQL fixture、`alembic upgrade head` 適用 fixture、session fixture

**Checkpoint**: 基盤完成 — ユーザーストーリー着手可

---

## Phase 3: User Story 1 - コアレースデータを正規化して保持できる (Priority: P1) 🎯 MVP

**Goal**: コア6テーブルを一意キー・制約・upsert 付きで構築し、正規化済みデータ基盤を成立させる。

**Independent Test**: ダミー1レースを投入し、制約違反なく保存され、同一 `race_id` 再投入で重複せず
更新されることを検証。

### Tests for User Story 1 ⚠️ (先に書いて FAIL を確認)

- [X] T012 [P] [US1] 統合テスト: migration 後にコア6テーブルが存在し、`(race_id, horse_id)` 複合 PK の upsert で重複行が増えない — `db/tests/integration/test_core_tables.py`
- [X] T013 [P] [US1] 統合テスト: `race_id` 形式 CHECK (11/13桁・英字を reject)、`race_number` 範囲 CHECK (0,13 を reject) — `db/tests/integration/test_core_constraints.py`

### Implementation for User Story 1

- [X] T014 [P] [US1] コア ORM モデル (races, horses, jockeys, trainers, race_horses, race_results) を `db/src/horseracing_db/models/core.py` に作成
- [X] T015 [US1] migration `0001_core_schema` を `db/migrations/versions/0001_core_schema.py` に作成: 6テーブル・PK・FK・`race_id`/`race_number` CHECK・`entry_status`/`result_status` 列+CHECK・`finish_order`-when-finished CHECK・`race_date` 索引・`updated_at` トリガ (T010 利用)
- [X] T016 [US1] `db/src/horseracing_db/models/__init__.py` でコアモデルを export

**Checkpoint**: US1 単独で機能・テスト可能 (コアスキーマ完成)

---

## Phase 4: User Story 2 - リーク防止と学習ラベルがデータ層で保証される (Priority: P1)

**Goal**: リークなしに学習ラベルと時系列分割を作れる土台を、制約・バリデータ・検証で担保する。

**Independent Test**: 取消/除外/中止/同着を含む結果からラベル導出が status-aware に整合し、odds 上書きで
履歴が増えず、`race_date` 分割が「対象レースより前のみ」を表現できることを検証。

### Tests for User Story 2 ⚠️

- [X] T017 [P] [US2] ユニットテスト: `is_valid_race_id`、`is_in_ingest_scope` の境界 (2006-12-31=false / 2007-01-01=true) — `db/tests/unit/test_validation.py`
- [X] T018 [P] [US2] 統合テスト: `race_horses.odds` を2回上書きしても行は1つ、`updated_at` が進む (履歴なし, INV-4) — `db/tests/integration/test_odds_overwrite.py`
- [X] T019 [P] [US2] 統合テスト: ラベル導出が status-aware (`finished` のみ集計、cancelled/excluded/stopped/disqualified 除外、同着は finish_order 共有、標準ケースで1着1頭/2着以内2頭/3着以内3頭)。**INV-1 も検証**: `cancelled`/`excluded` 行は `race_results` 行を持たない — `db/tests/integration/test_label_derivation.py`
- [X] T020 [P] [US2] 統合テスト: `race_date` 基準日より前のみ集計し以降が混入しない (walk-forward)、欠損(null) と 0 の区別 — `db/tests/integration/test_timesplit_and_missing.py`

### Implementation for User Story 2

- [X] T021 [P] [US2] `db/src/horseracing_db/validation.py` に `is_valid_race_id` と `is_in_ingest_scope` (2007 境界の唯一の正本) を実装 — contracts/validation.md のシグネチャ通り
- [X] T022 [US2] status-aware なラベル導出参照クエリ/ヘルパを `db/src/horseracing_db/labels.py` に実装 (`finished` のみ、win/top2/top3 を finish_order から導出)

**Checkpoint**: US1+US2 が独立して機能・テスト可能 (P1 = MVP 完了)

---

## Phase 5: User Story 3 - 異種ソースの ID を安全に対応付けられる + 取込が監査できる (Priority: P2)

**Goal**: 推測結合せず対応表経由でのみ突き合わせられ、取込ジョブ状態を追える器を用意する。

**Independent Test**: 未対応 ID が推測結合されず未対応として記録され、ジョブ状態とエラー理由が残ることを検証。

### Tests for User Story 3 ⚠️

- [X] T023 [P] [US3] 統合テスト: `id_mappings` 未対応 (`mapping_status='unmapped'`, `canonical_id IS NULL`) を記録、`UNIQUE(entity_type,source,source_id)` で重複 reject、衝突を `conflict`+`conflict_group_id` で表現、status/source/entity_type CHECK — `db/tests/integration/test_id_mappings.py`
- [X] T024 [P] [US3] 統合テスト: `ingestion_jobs` の status CHECK と partial/failed + `error_message` 記録 — `db/tests/integration/test_ingestion_jobs.py`

### Implementation for User Story 3

- [X] T025 [P] [US3] ID対応/取込 ORM モデル (id_mappings, ingestion_jobs) を `db/src/horseracing_db/models/ingestion.py` に作成
- [X] T026 [US3] migration `0002_ingestion_id_schema` を `db/migrations/versions/0002_ingestion_id_schema.py` に作成 (テーブル・CHECK・UNIQUE・`updated_at` トリガ)

**Checkpoint**: US3 が独立して機能・テスト可能

---

## Phase 6: User Story 4 - 予測・推奨を後付け破壊変更なしに保存できる (最小契約) (Priority: P2)

**Goal**: 予測実行・馬別確率・推奨・特徴量スナップショットを、監査可能な最小契約で保存できるようにする。

**Independent Test**: 予測実行+推奨を1件保存し、最新オッズ上書き後も監査列だけで提示時点の判断根拠を
再構成できることを検証。

### Tests for User Story 4 ⚠️

- [X] T027 [P] [US4] 統合テスト: `race_predictions` の単調 CHECK (`0<=win<=top2<=top3<=1`) が違反値を reject — `db/tests/integration/test_prediction_probs.py`
- [X] T028 [P] [US4] 統合テスト: prediction_run + race_predictions + recommendation を保存し、`race_horses.odds` 上書き後も `recommendations` の監査列だけで判断根拠を再構成 (SC-004) — `db/tests/integration/test_recommendation_audit.py`
- [X] T029 [P] [US4] 統合テスト: `recommendations.bet_type` CHECK (**7券種**: win/place/quinella/exacta/wide/trio/trifecta) と `is_estimated_odds` が実 vs 推定オッズを区別 (FR-022) — `db/tests/integration/test_recommendation_bet_type.py`
- [X] T030 [P] [US4] 統合テスト: `feature_snapshots` が `prediction_run_id`+`horse_id` で予測時の特徴量を保持し、予測を再現できる参照になる (FR-020 / US4 AC4) — `db/tests/integration/test_feature_snapshots.py`
- [X] T031 [P] [US4] 統合テスト: `model_versions.adoption_status` CHECK (`candidate`/`active`/`retired`) が不正値を reject (FR-017) — `db/tests/integration/test_model_versions.py`

### Implementation for User Story 4

- [X] T032 [P] [US4] 予測・推奨 ORM モデル (model_versions, prediction_runs, race_predictions, feature_snapshots, recommendations) を `db/src/horseracing_db/models/prediction.py` に作成
- [X] T033 [US4] migration `0003_prediction_contract` を `db/migrations/versions/0003_prediction_contract.py` に作成 (テーブル・単調 CHECK・bet_type CHECK・adoption_status CHECK・FK・`updated_at` トリガ)

**Checkpoint**: 全ストーリーが独立して機能

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 横断検証とドキュメント

- [X] T034 [P] 統合テスト: マイグレーション冪等性 (`upgrade head` → `downgrade base` → `upgrade head` がクリーン, SC-005) — `db/tests/integration/test_migration_roundtrip.py`
- [X] T035 [P] `db/README.md` を作成 (セットアップ・migration・テスト実行コマンド。quickstart.md を要約)
- [X] T036 quickstart.md を end-to-end 実行し SC-001〜SC-006 をすべて確認

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし。即着手可
- **Foundational (Phase 2)**: Setup 完了に依存。全ストーリーを BLOCK
- **User Stories (Phase 3-6)**: Foundational 完了に依存
  - US1 → US2 は同一マイグレーション 0001 を共有するため US2 は US1 (特に T015) に依存
  - US3 (0002)・US4 (0003) は US1 完了後に着手可。US3 と US4 は相互独立 (並列可)
- **Polish (Phase 7)**: 望むストーリー完了に依存。T034/T036 は全 migration 完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後に着手。他ストーリー非依存
- **US2 (P1)**: US1 の migration 0001 (T015) に依存 (同一テーブル群の制約・ラベルを検証)
- **US3 (P2)**: US1 後に着手可。US2/US4 非依存
- **US4 (P2)**: US1 後に着手可。US2/US3 非依存。FK が races/horses を参照するため core 必須

### Within Each User Story

- テストを先に書き FAIL を確認 → 実装
- モデル → マイグレーション → export の順
- ストーリー完了後に次優先へ

### Parallel Opportunities

- Setup の [P] (T002/T004)、Foundational の [P] (T007/T008/T009) は並列可
- 各ストーリーの test タスク [P] は並列可
- Foundational 完了後、US3 と US4 は別担当で並列可 (US1 完了前提)
- Polish の T034/T035 は並列可

---

## Parallel Example: User Story 4

```bash
# US4 のテストを並列起動 (先に FAIL 確認):
Task: "統合テスト prediction probs in db/tests/integration/test_prediction_probs.py"
Task: "統合テスト recommendation audit in db/tests/integration/test_recommendation_audit.py"
Task: "統合テスト recommendation bet_type in db/tests/integration/test_recommendation_bet_type.py"
Task: "統合テスト feature_snapshots in db/tests/integration/test_feature_snapshots.py"
Task: "統合テスト model_versions in db/tests/integration/test_model_versions.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 = P1)

1. Phase 1 Setup → Phase 2 Foundational
2. Phase 3 US1 (コアスキーマ) → **STOP & VALIDATE** (制約・upsert)
3. Phase 4 US2 (リーク防止・ラベル) → **STOP & VALIDATE** (ラベル導出・時系列・odds 上書き)
4. ここまでで取込・評価 feature が乗れる MVP データ基盤が完成

### Incremental Delivery

1. Setup + Foundational → 基盤
2. US1 → 独立検証 → コアスキーマ提供
3. US2 → 独立検証 → リーク防止・ラベル土台 (MVP!)
4. US3 → 独立検証 → ID対応・取込監査
5. US4 → 独立検証 → 予測・推奨契約
6. Polish → 冪等性・README・quickstart 全検証

---

## Notes

- [P] = 異なるファイル・依存なし
- 各ストーリーは独立して完了・テスト可能 (US2 のみ US1 の 0001 に依存)
- テストは実装前に FAIL を確認
- タスクまたは論理グループごとにコミット
- 状態コード・制約名・列定義は data-model.md を正とし、`enums.py`/`constraints.py` で一元管理
- bet_type は **7券種** (win/place/quinella/exacta/wide/trio/trifecta = 単勝/複勝/馬連/馬単/ワイド/3連複/3連単)
- 2007 境界は `validation.py` の `is_in_ingest_scope` が唯一の正本 (スキーマに日付 CHECK を入れない)
