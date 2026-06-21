---
description: "Task list for JRA-VAN 過去データ取込 (2007+)"
---

# Tasks: JRA-VAN 過去データ取込 (2007+)

**Input**: Design documents from `specs/002-jra-van-ingest/`

**Prerequisites**: plan.md, spec.md, research.md (73列マップ), data-model.md, contracts/

**Tests**: 含む。spec の Independent Test と憲法 NON-NEGOTIABLE (リーク防止・状態正規化のラベル整合)
のため test タスクを生成する。状態正規化は golden fixture でロックする (最大リスク)。

**Source of truth**: 列レイアウト・raceId 導出・venue 表・状態規則は research.md / data-model.md を正本。
コアスキーマ・enums・validation・labels は Feature 001 (`db/`) を正本。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可 (異なるファイル・依存なし)
- パスはリポジトリ root 基準。取込パッケージは `ingest/`

---

## Phase 1: Setup

- [X] T001 `ingest/` のディレクトリ構成を plan.md 通りに作成 (`ingest/src/horseracing_ingest/`, `ingest/tests/{unit,integration,fixtures}/`)
- [X] T002 `ingest/pyproject.toml` を作成し依存定義 (`horseracing-db` をパス依存 `../db`、sqlalchemy>=2.0, psycopg[binary]>=3。dev: pytest, testcontainers[postgres], ruff)
- [X] T003 [P] `ingest/pyproject.toml` に ruff 設定と `[tool.pytest.ini_options]` (integration マーカー) を追加

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 完了までユーザーストーリー着手不可

- [X] T004 `ingest/src/horseracing_ingest/layout.py` を作成: 73列の index 定数 (research R1)、venue_code 表 (R3 の10コース)、状態判定に使う列参照を一元定義
- [X] T005 `ingest/tests/conftest.py` を作成: testcontainers PostgreSQL16 fixture、Feature 001 の alembic を `head` まで適用 (`db/alembic.ini` 参照)、session、テスト間 truncate
- [X] T006 [P] `ingest/tests/_sjis.py` を作成: Shift_JIS(cp932) で golden CSV を書き出すヘルパ (73列の行を組み立てて fixture を生成する)
- [X] T007 `db/` に migration `0004_ingestion_job_counts` を追加 (`db/migrations/versions/0004_ingestion_job_counts.py`) し、(a) `ingestion_jobs` に nullable な `processed_rows`(int)/`skipped_rows`(int)/`error_count`(int)/`summary`(jsonb) を**非破壊追加**、(b) `JobStatus` に `skipped` を追加 (`ck_ingestion_jobs_status` の CHECK 差し替え + `db/src/horseracing_db/enums.py` の `JobStatus.ALL` に `skipped`)。`db/src/horseracing_db/models/ingestion.py` の `IngestionJob` に件数列を追加。`db/tests/` に `skipped` 受理と件数列のテストを足し、`test_migration_roundtrip.py` の冪等性が通ること (監査用、憲法 V/VI)

**Checkpoint**: 基盤完成 (取込先スキーマ + 監査列 + テスト土台)

---

## Phase 3: User Story 1 - 1 年分をコアテーブルに取り込める (Priority: P1) 🎯 MVP

**Goal**: 2007 年ファイルを parse → map → 冪等 upsert し、`ingestion_jobs` に件数監査を記録する。

**Independent Test**: 2007 golden fixture を取込み、期待レース/出走/結果件数が制約違反なく入り、
同一ファイル再取込で重複せず、欠損(馬体重等)が null で 0 と区別されることを検証。

### Tests for User Story 1 ⚠️

- [X] T008 [P] [US1] ユニット: `parse_rows` が cp932 をストリーム解析、73列検証、列数不正で RowError を返す。未使用列があっても解析が成功する (FR-002) — `ingest/tests/unit/test_parser.py`
- [X] T009 [P] [US1] ユニット: `derive_race_id` (12桁・`is_valid_race_id` 合格)、`venue_to_code` (10コース・未知場名でエラー) — `ingest/tests/unit/test_mapping_ids.py`
- [X] T010 [P] [US1] 統合: 2007 golden fixture を取込み期待件数 (races/race_horses/race_results) が入り、再取込で重複しない (SC-001/SC-003)。**欠損 (馬体重・増減・血統登録番号) が null で保存され 0 と区別される (FR-019/憲法 IV)** — `ingest/tests/integration/test_ingest_year.py`

### Implementation for User Story 1

- [X] T011 [US1] `ingest/src/horseracing_ingest/parser.py`: `parse_rows(path)` (cp932 ストリーム)、`ParsedRow`、`RowError(line_no, reason)`
- [X] T012 [US1] `ingest/src/horseracing_ingest/mapping.py`: `derive_race_id`、`venue_to_code`、`to_core_records` (列→コア各テーブル dict、欠損は None=Unknown で 0 にしない、全角空白 trim)。状態は当面 finished 既定 (US2 で精緻化)
- [X] T013 [US1] `ingest/src/horseracing_ingest/upsert.py`: PostgreSQL `ON CONFLICT DO UPDATE` で FK 順 upsert (races→horses/jockeys/trainers→race_horses→race_results)
- [X] T014 [US1] `ingest/src/horseracing_ingest/pipeline.py`: `ingest_year(session, path)` = parse→map→バッチ upsert→`ingestion_jobs` 記録 (processed/skipped/error_count + summary 件数)、`IngestSummary` を返す
- [X] T015 [US1] `ingest/src/horseracing_ingest/cli.py` + `__main__.py`: argparse `ingest-year <path>` (終了コード 0/1/2/3、サマリ表示)

**Checkpoint**: US1 単独で 1 年取込が機能・テスト可能 (MVP)

---

## Phase 4: User Story 2 - 状態を正しく正規化しラベルを汚染しない (Priority: P1)

**Goal**: 取消/除外/中止/失格/同着を `entry_status`/`result_status` に正しくマップし、`labels.derive_labels`
が finished のみを返す状態を保証する。

**Independent Test**: 各状態を含む golden fixture を取込み、状態が期待通り、取消・除外は race_results
行なし (INV-1)、`labels.derive_labels` が finished のみを返すことを検証。

### Tests for User Story 2 ⚠️

- [X] T016 [P] [US2] ユニット: `normalize_status` が完走/DNF(走行あり)/DNS(走行なし) を区別し、未知状態でエラーを返す (finished にしない)。finished/DNF/DNS の 3 区分を保証 — `ingest/tests/unit/test_status.py`
- [X] T017 [P] [US2] 統合: 取消/除外/中止/失格/同着 fixture を取込み、**finished/DNF/DNS の 3 区分を hard gate として検証** (DNS は race_results なし=INV-1、DNF は result_status 非 finished、`labels.derive_labels` が finished のみ・同着で複数勝ち馬, SC-002)。取消 vs 除外・中止 vs 失格 の 4 分類は、異常区分を特定できた場合のみ条件付きで検証 (未特定なら 3 区分の保証で可) — `ingest/tests/integration/test_status_normalization.py`

### Implementation for User Story 2

- [X] T018 [US2] `mapping.py` に `normalize_status(row) -> StatusDecision` を実装 (research R4: finish_order + 走行データ有無、未知→エラー、疑似着順にしない、finish_order=0 は null。4分類細分は異常区分を特定できた範囲で best-effort)
- [X] T019 [US2] `pipeline.py`/`upsert.py` を更新: `entry_status` を race_horses に設定し、DNS は race_results 行を作らない/DNF は result_status 付きで作る条件分岐

**Checkpoint**: US1+US2 が独立して機能 (P1 = MVP 完了、ラベル整合担保)

---

## Phase 5: User Story 3 - 複数年一括・境界・再開・監査 (Priority: P2)

**Goal**: 2007〜2025 を一括取込でき、2007 境界の強制・checkpoint 再開・年単位監査ができる。

**Independent Test**: 2006/2007 fixture で 2006 がスキップ記録され 2007 のみ取込まれ、checkpoint 再開で
重複せず、年ごとの件数が `ingestion_jobs` の件数列で確認できることを検証。

### Tests for User Story 3 ⚠️

- [X] T020 [P] [US3] 統合: 2006 と 2007 fixture で 2006 がスキップ記録され、コアデータに 2006 が 1 行も入らない (SC-004) — `ingest/tests/integration/test_boundary_skip.py`
- [X] T021 [P] [US3] 統合: checkpoint からの再開で重複が出ない、partial/failed と**年ごとの件数 (processed/skipped/error + summary) が `ingestion_jobs` に記録される (SC-006 の監査)** — `ingest/tests/integration/test_resume_audit.py`

### Implementation for User Story 3

- [X] T022 [US3] `pipeline.py`: 2007 境界を `validation.is_in_ingest_scope` で判定 (独自日付比較を書かない)、<2007 を skip 記録、checkpoint(処理済み行番号) で再開対応
- [X] T023 [US3] `cli.py`: `ingest-all <dir>` (年順に各年取込、年ごとに `ingestion_jobs` 1 行 + 件数列、skip 集計)

**Checkpoint**: 全ストーリーが独立して機能

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T024 [P] 統合: 不正行 (列数≠73・cp932 デコード不能・raceId 形式不正・未知状態) が黙って捨てられず `ingestion_jobs.error_message` に行番号付きで記録され、`error_count` に計上される (SC-005) — `ingest/tests/integration/test_error_recording.py`
- [X] T025 [P] `ingest/README.md` を作成 (CLI・テスト実行・依存)
- [X] T026 ruff クリーン + 全テスト green を確認 (`ingest/` と `db/` 両方: `uv run ruff check`, `uv run pytest`)
- [X] T027 (ローカル・任意) 実データ `raw_data/jra-van/2007` をスモーク取込し検証済み: races=3453, race_horses=49009 (全行), race_results=48692, **errors=0**。`ingestion_jobs` の件数列に記録。実データは gitignore のため CI 対象外

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし
- **Foundational (Phase 2)**: Setup 完了に依存。全ストーリーを BLOCK。T007 (migration 0004) は監査記録の前提
- **US1 (Phase 3)**: Foundational 後。MVP の中核
- **US2 (Phase 4)**: US1 (parse/map/upsert/pipeline) に依存。状態ロジックを差し込む
- **US3 (Phase 5)**: US1 に依存。境界・一括・再開・件数監査を pipeline/cli に追加。US2 とは独立
- **Polish (Phase 6)**: 望むストーリー完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後に着手。他ストーリー非依存
- **US2 (P1)**: US1 の mapping/pipeline に状態ロジックを追加 (同一ファイル群を編集)
- **US3 (P2)**: US1 の pipeline/cli に境界・一括を追加。US2 非依存 (pipeline.py 編集競合に注意し順次推奨)

### Within Each User Story

- テストを先に書き FAIL を確認 → 実装
- parser/mapping → upsert → pipeline → cli の順
- 状態正規化 (US2) は golden fixture で期待値を固定してから実装

### Parallel Opportunities

- Setup の T003、Foundational の T006 は並列可 (T007 migration は T005 conftest と独立だが db/ を触る)
- 各ストーリーの test タスク [P] は並列可
- US1 完了後、US2 と US3 は pipeline.py を双方が編集するため順次推奨
- Polish の T024/T025 は並列可

---

## Parallel Example: User Story 1

```bash
# US1 テストを並列起動 (先に FAIL 確認):
Task: "unit parser in ingest/tests/unit/test_parser.py"
Task: "unit mapping ids in ingest/tests/unit/test_mapping_ids.py"
Task: "integration ingest year in ingest/tests/integration/test_ingest_year.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 = P1)

1. Setup → Foundational (migration 0004 含む)
2. US1: 2007 を parse→upsert→件数監査 (状態は finished 既定) → 検証
3. US2: 状態正規化を差し込み、ラベル整合 (取消/除外/中止/失格/同着) を golden fixture で固定 → 検証
4. ここで「2007 年が正しくコアに乗り、ラベルが汚染されない」MVP が完成 → 評価ハーネス feature が着手可

### Incremental Delivery

1. Setup + Foundational
2. US1 → 1 年取込 (MVP の土台)
3. US2 → 状態正規化 (MVP 完成、ラベル整合)
4. US3 → 全年一括・境界・再開・件数監査
5. Polish → エラー記録・README・実データスモーク

---

## Notes

- [P] = 異なるファイル・依存なし
- 状態正規化 (US2) が本 feature 最大リスク。取消/除外/中止/失格/同着の golden fixture で期待値を固定し、
  未知状態は黙って finished にせずエラー化 (FR-012)
- 73列マップ・venue 表・状態規則は research.md / data-model.md を正本。Feature 001 の enums/validation/
  labels を import して使う (再実装しない)
- 2007 境界は `validation.is_in_ingest_scope` が唯一の正本
- 取消/除外 vs 中止/失格 の 4 分類細分は、異常区分列を golden fixture で特定できた範囲で精緻化。最低限
  finished/DNF/DNS の 3 区分は保証する (ラベル整合に必要十分)
- 監査件数は `ingestion_jobs` の非破壊追加列 (processed_rows/skipped_rows/error_count/summary、migration
  0004) に保持。core 集計では再取込後にジョブ時点の件数を復元できないため (憲法 V)
