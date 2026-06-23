---
description: "Task list for netkeiba スクレイピングによる未来レース取り込み"
---

# Tasks: netkeiba スクレイピングによる未来レース取り込み

**Input**: Design documents from `specs/008-netkeiba-scraping/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 含む。spec の Independent Test と憲法 I/II/V のため test タスクを生成する。
**ID 名前空間/未マッピング debut・偽 race_id 不作成・結果 insert-only・odds 保護が最重要テスト**(codex 5 BLOCKER)。

**Source of truth**: ID 解決・race_id 構成・insert-only・odds 保護・監査は research.md / data-model.md / contracts/。
upsert/監査の作法は Feature 002、未マッピング=debut の leak-safe 経路は Feature 004。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(異なるファイル・依存なし)
- パスはリポジトリ root 基準。scrape パッケージは `scrape/`

---

## Phase 1: Setup

- [X] T001 `scrape/` のディレクトリ構成を plan.md 通りに作成(`scrape/src/horseracing_scrape/parse/`, `scrape/tests/{unit,integration,fixtures}/`)
- [X] T002 `scrape/pyproject.toml` を作成し依存定義(`horseracing-db` をパス依存、httpx, selectolax(or beautifulsoup4+lxml), sqlalchemy>=2.0。dev: pytest, testcontainers[postgres], ruff)
- [X] T003 [P] `scrape/pyproject.toml` に ruff 設定 + `[tool.pytest.ini_options]`(integration マーカー、tests E501 ignore)+ `__init__.py`(`SCRAPE_PARSER_VERSION`)を追加

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 完了までユーザーストーリー着手不可

- [X] T004 `scrape/src/horseracing_scrape/venues.py`: netkeiba 開催場→JRA-VAN コード対応表 + `build_race_id`(構成→`is_valid_race_id`→不能/2007未満は None。**偽 ID を返さない**)(contracts/idmap.md, INV-N3)
- [X] T005 `scrape/src/horseracing_scrape/idmap.py`: `resolve_entity`(id_mappings 経由で canonical_id、未対応は `nk:{id}` 代替 + UNMAPPED upsert。**推測結合しない**)。`SURROGATE_PREFIX="nk:"`(contracts/idmap.md, INV-N1/N2)
- [X] T006 `scrape/src/horseracing_scrape/fetch.py`: `PoliteFetcher` プロトコル + `HttpFetcher`(robots/最小間隔/file cache/UA/指数バックオフ/エンコーディング正規化)+ `FixtureFetcher`(テスト用 url→ローカル HTML)。**礼儀の単体テスト**(httpx + robotparser をモックし robots 不許可で取得拒否・最小間隔の待機・バックオフ・キャッシュ命中を assert)— `scrape/tests/unit/test_fetch_polite.py`(FR-001/C1)(R7)
- [X] T007 `scrape/tests/conftest.py`: testcontainers PostgreSQL16 + `db/` alembic head + session + テスト間 truncate + 合成 core データ(JRA-VAN 既存行)投入ヘルパ

**Checkpoint**: 基盤完成(race_id 構成・ID 解決・取得層・テスト基盤)

---

## Phase 3: User Story 1 - 出馬表を取り込み ID を安全に対応付ける (Priority: P1) 🎯 MVP

**Goal**: 出馬表を races/race_horses/horses/jockeys/trainers に upsert、netkeiba ID を id_mappings 経由で対応付け。

**Independent Test**: 保存済み出馬表 HTML フィクスチャで、マッピング済み=canonical_id / 未対応=一意 `nk:{id}` +
UNMAPPED キュー、構成不能 race_id は行を作らない。

### Tests for User Story 1 ⚠️

- [X] T008 [P] [US1] ユニット: `parse_entries` が出馬表 HTML フィクスチャから race/出走馬(枠/馬番/騎手/調教師/斤量/性齢/entry_status)を抽出、必須欠損で `ParseError`(fail-close)— `scrape/tests/unit/test_parse_entries.py`
- [X] T009 [P] [US1] ユニット: `build_race_id` が valid 構成を返し、未知開催場/2007未満/不正は None(偽 ID なし)。`resolve_entity` がマッピング済み=canonical / 未対応=一意 `nk:{id}`、**異なる netkeiba ID は異なる代替 ID**(履歴非共有)、**代替 ID が JRA-VAN 数値 ID/12桁形式と一致しない**(非衝突)— `scrape/tests/unit/test_idmap_venues.py`
- [X] T010 [P] [US1] 統合: `scrape_entries`(FixtureFetcher)が core テーブルに upsert、マッピング済み馬は canonical_id、未対応は `nk:{id}` + id_mappings UNMAPPED。構成不能 race_id は行を作らず skip 計上。idempotent(2回で重複なし)— `scrape/tests/integration/test_entries.py`
- [X] T011 [P] [US1] 統合(leak-safe): 未マッピング馬(`nk:{id}`)が `build_feature_matrix` で debut/Unknown(career_starts=0)になり他馬履歴が混入しない — `scrape/tests/integration/test_unmapped_debut.py`

### Implementation for User Story 1

- [X] T012 [US1] `scrape/src/horseracing_scrape/parse/entries.py`: `parse_entries(html) -> ScrapedEntry`(DOM セレクタ、fail-close)
- [X] T013 [US1] `scrape/src/horseracing_scrape/upsert.py`: `upsert_entries`(build_race_id→None なら skip、resolve_entity の id で horses/jockeys/trainers PK upsert、races/race_horses upsert、entry_status 反映)
- [X] T014 [US1] `scrape/src/horseracing_scrape/pipeline.py`: `scrape_entries(session, race_id|date, fetcher)`(fetch→parse→upsert + `ingestion_jobs` 監査、idempotent)
- [X] T015 [US1] `scrape/src/horseracing_scrape/cli.py` + `__main__.py`: `scrape-entries --race-id/--date`(件数・未マッピング数・skip 表示)

**Checkpoint**: US1 単独で出馬表取り込み + 安全な ID 対応付けが成立(MVP の中核)

---

## Phase 4: User Story 2 - 前売りオッズを取り込み最終オッズを保護 (Priority: P1)

**Goal**: 締切前単勝オッズを結果未確定レースのみに最新値上書き。JRA-VAN 最終オッズを壊さない。

**Independent Test**: 結果未確定レースに odds を取り込むと更新+updated_at 前進。結果確定済み(race_results あり)レースの
odds は不変。

### Tests for User Story 2 ⚠️

- [X] T016 [P] [US2] ユニット: `parse_odds` がオッズ HTML フィクスチャから (netkeiba horse_id, 単勝オッズ, 人気) を抽出、欠損/不正で除外 — `scrape/tests/unit/test_parse_odds.py`
- [X] T017 [P] [US2] 統合(最重要): `scrape_odds` が結果未確定レースの race_horses.odds を最新値上書き(updated_at 前進)、**結果確定済みレースの odds を更新しない**(JRA-VAN 保護)、idempotent — `scrape/tests/integration/test_odds.py`

### Implementation for User Story 2

- [X] T018 [US2] `scrape/src/horseracing_scrape/parse/odds.py`: `parse_odds(html) -> list[ScrapedOdds]`(fail-close)
- [X] T019 [US2] `upsert.py` に `update_odds`(対象 race に race_results があれば skip、無ければ結果未確定として odds 最新値上書き、欠損/不正は除外)
- [X] T020 [US2] `pipeline.py` に `scrape_odds(session, race_id|date, fetcher)` + ingestion_jobs 監査、`cli.py` に `scrape-odds`

**Checkpoint**: US1+US2 = 出馬表 + 前売りオッズ(最終オッズ保護)が機能

---

## Phase 5: User Story 3 - 結果を backfill(JRA-VAN を壊さない) (Priority: P1)

**Goal**: netkeiba 結果を race_results に insert-only で取り込み、欠損のみ補完。

**Independent Test**: 既存 JRA-VAN race_results 行を持つレースに netkeiba 結果を取り込んでも既存行が不変(insert-only)。
結果の無いレースには新規行が作られる。

### Tests for User Story 3 ⚠️

- [X] T021 [P] [US3] ユニット: `parse_results` が結果 HTML フィクスチャから (着順/結果状態/タイム) を抽出、状態を finished/stopped/disqualified に対応、同着は finish_order 共有、非出走は結果行なし — `scrape/tests/unit/test_parse_results.py`
- [X] T022 [P] [US3] 統合(最重要): `scrape_results` が race_results へ **insert-only**(既存 JRA-VAN 行を一切変更しない)、結果の無いレースに新規行、非出走に行を作らない、idempotent — `scrape/tests/integration/test_results.py`

### Implementation for User Story 3

- [X] T023 [US3] `scrape/src/horseracing_scrape/parse/results.py`: `parse_results(html) -> list[ScrapedResult]`(状態対応、fail-close)
- [X] T024 [US3] `upsert.py` に `backfill_results`(`INSERT ... ON CONFLICT (race_id,horse_id) DO NOTHING`、非出走除外、同着 finish_order 共有)
- [X] T025 [US3] `pipeline.py` に `scrape_results(session, race_id|date, fetcher)` + ingestion_jobs 監査、`cli.py` に `scrape-results`

**Checkpoint**: US1+US2+US3 = 出馬表 + オッズ + 結果 backfill が完成

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T026 [P] `scrape/README.md` を作成(概要・CLI・テスト・礼儀(robots/レート/キャッシュ/ToS)・ID マッピング/代替 ID・偽 ID 不作成・insert-only・odds 保護・監査)
- [X] T027 ruff クリーン + 全テスト green を確認(`scrape/`: `uv run ruff check`, `uv run pytest`)
- [X] T028 (ローカル・任意) 取り込んだ未来レース(または合成)に対し Feature 006 serving が予測を生成できることを確認(SC-008 e2e)。実 netkeiba は手動 CLI のみ(テストはフィクスチャ)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし
- **Foundational (Phase 2)**: Setup 完了に依存。全ストーリーを BLOCK(venues/idmap/fetch/conftest)
- **US1 (Phase 3)**: Foundational 後。出馬表 + ID 対応付け(MVP)
- **US2 (Phase 4)**: Foundational 後。odds(US1 と概ね独立、upsert.py を共有編集)
- **US3 (Phase 5)**: Foundational 後。results(US1/US2 と概ね独立、upsert.py/pipeline.py を共有編集)
- **Polish (Phase 6)**: 望むストーリー完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後。core upsert + id_mappings
- **US2 (P1)**: Foundational 後。upsert.py に update_odds 追加(US1 と並行可だが同ファイル編集は順次)
- **US3 (P1)**: Foundational 後。upsert.py に backfill_results 追加

### Within Each User Story

- テストを先に書き FAIL を確認 → 実装
- **ID 解決/race_id 構成(T009)・未マッピング debut(T011)・odds 保護(T017)・結果 insert-only(T022)を最優先で固定**
- venues/idmap/fetch(基盤)→ parse → upsert → pipeline → cli の順

### Parallel Opportunities

- Setup の T003、各ストーリーの test タスク [P] は並列可
- US1/US2/US3 の parse は別ファイルで並行可。upsert.py/pipeline.py の追記は順次
- Polish の T026 は並列可

---

## Implementation Strategy

### MVP First (US1 = P1 MVP)

1. Setup → Foundational(venues/idmap/fetch/conftest)
2. US1: 出馬表取り込み + ID 安全対応付け(canonical / `nk:{id}` / UNMAPPED キュー)+ 偽 ID 不作成 + debut leak-safe
3. ここで serving が未来レースを予測可能になる(MVP の到達点)

### Incremental Delivery

1. Setup + Foundational
2. US1 → 出馬表 + ID マッピング(MVP)
3. US2 → 前売りオッズ(最終オッズ保護)
4. US3 → 結果 backfill(insert-only)
5. Polish → README・serving e2e スモーク

---

## Notes

- [P] = 異なるファイル・依存なし
- **codex 5 BLOCKER が本 feature の核**: ①ID 名前空間(canonical/`nk:`)②偽 race_id 不作成 ③結果 insert-only
  ④odds は結果未確定のみ ⑤idempotent+監査。最優先テスト = T009/T011/T017/T022
- **推測結合禁止**(憲法 I)。未マッピングは debut/Unknown で leak-safe(憲法 II)。オッズはモデル特徴に使わない
- **礼儀**: robots/レート/キャッシュ/UA/バックオフ。テストはネットワーク非依存(HTML フィクスチャ + モック fetcher)
- スキーマ変更なし。複勝/馬連/三連複・推定オッズ・地方/海外・id_mappings 自動解決は対象外
