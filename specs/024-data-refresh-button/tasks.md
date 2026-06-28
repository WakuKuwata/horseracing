---
description: "Task list for feature 024 — netkeiba データ更新ボタン"
---

# Tasks: netkeiba データ更新ボタン

**Input**: Design documents from `/specs/024-data-refresh-button/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ops-api.yaml, quickstart.md

**Tests**: 含める。憲法 III（評価先行）＋ quickstart の「不変条件テスト（必須）」により、leak-guard / readonly 不変 / dedup 競合 / 種別判定 / 契約 / front 状態 のテストを生成する。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可（別ファイル・依存なし）
- **[Story]**: US1/US2/US3（spec.md のユーザーストーリー）
- パスは plan.md の構造（新 `ops/` パッケージ＋ `front/` 加筆＋ `deploy/`）に準拠

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 新 `ops/` パッケージの足場と接続

- [X] T001 `ops/pyproject.toml` と `ops/src/horseracing_ops/__init__.py` を作成し、ルートの uv workspace members に `ops` を登録（install/test で拾われるように）。依存は `horseracing_db` / `horseracing_scrape` / `horseracing_live`（read 用に `horseracing_api` は依存しない）。`horseracing_training`/`horseracing_eval`/`horseracing_features` は依存に含めない（leak 境界）。
- [X] T002 `ops/src/horseracing_ops/deps.py` に owner ロールの engine/session を実装（`DATABASE_URL_OWNER` を使用、read+write、per-request rollback/close）。
- [X] T003 [P] `front/vite.config.ts` の dev proxy に `/ops` → `http://localhost:8001` を追加。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 全ストーリーが依存する中核（enqueue / worker / 実行ロジック / 契約スキーマ / 境界テスト）

**⚠️ CRITICAL**: このフェーズ完了まで US1/US2/US3 は着手不可

- [X] T004 `ops/src/horseracing_ops/enqueue.py` に `enqueue_race(session, race_id, *, force=False)` を実装：`pg_advisory_xact_lock(hashtext('refresh:race:'||race_id))` 配下で active(queued/running) ジョブ再利用、無ければ `ingestion_jobs`(source=netkeiba, job_type=refresh_race, scope=race, scope_value=race_id, status=queued) を INSERT。戻り値に `reused` を含む（鮮度・force は US3 で拡張）。
- [X] T005 `ops/src/horseracing_ops/runner.py` に `run_one(session, job)` を実装：実行直前に `race_results` の有無で種別再判定（pending=entries+odds／確定後=results）→ `scrape/urls.py` で URL 導出 → 既存 `HttpFetcher`（robots/rate-limit/backoff/cache を流用＝FR-014）で `scrape_entries`+`scrape_odds` または `scrape_results` 実行 → 取得対象が未公開・無しなら `skipped` → `status`(succeeded/partial/failed/skipped)・`processed_rows`/`skipped_rows`/`error_count`・`summary`(kind/parser_version/written)・`started_at`/`completed_at` を更新。
- [X] T006 `ops/src/horseracing_ops/worker.py` に常駐ループを実装：`SELECT … FOR UPDATE SKIP LOCKED` で queued を取得し `run_one` 実行。起動時に stale `running`（しきい時間超過）を `retry_count+1` で `queued` 復帰、`max_retry` 超過は `failed`。`python -m horseracing_ops.worker` で起動可能に。
- [X] T007 [P] `ops/src/horseracing_ops/schemas.py` に pydantic 契約（JobAccepted / BatchAccepted / Job / Batch / Error / JobStatus）を `contracts/ops-api.yaml` どおり定義。
- [X] T008 `ops/src/horseracing_ops/app.py` に FastAPI アプリ（lifespan で owner engine、`/ops/v1` prefix、typed error ハンドラで 404/422 を `{status,code,detail}` に正規化）を実装。
- [X] T009 [P] `ops/tests/integration/test_leak_guard.py`：ops/worker パッケージが training/eval/features を import しないこと、取り込んだ odds/results が特徴量経路に渡らないこと（import グラフ検査、憲法 II / FR-020）。
- [X] T010 [P] `ops/tests/integration/test_readonly_unaffected.py`：014 (`api/`) が無変更で全 GET・行数不変のままであること（既存 `test_readonly_invariant.py` を 024 ブランチで実行し回帰しない）を確認。

**Checkpoint**: enqueue→worker→DB 反映の土台と境界テストが揃い、各ストーリー着手可能

---

## Phase 3: User Story 1 - 詳細ページで 1 レースを更新 (Priority: P1) 🎯 MVP

**Goal**: 詳細ページの「このレースを更新」で 1 レースを非同期取得し、完了後に表示を最新化

**Independent Test**: 任意レースの詳細でボタン押下→202 即応→polling で終端→成功時に出走馬/オッズ(または結果)が更新後の値に変わる

### Tests for User Story 1

- [X] T011 [P] [US1] `ops/tests/integration/test_refresh_race_contract.py`：`POST /ops/v1/races/{id}/refresh` が 202(JobAccepted)/404(未存在)/422(非12桁)、`GET /ops/v1/jobs/{id}` が 200/404 を返す（contracts/ops-api.yaml 準拠）。
- [X] T012 [P] [US1] `ops/tests/integration/test_refresh_race_flow.py`：enqueue→worker(スタブ fetcher＋保存フィクスチャ)→`succeeded`。pending は entries+odds、確定後は results。確定済み結果が上書き破壊されない（FR-018, SC-005）。
- [X] T013 [P] [US1] `ops/tests/integration/test_dedup_concurrent.py`：同一 race への同時 enqueue で `queued` 行が 1 件のみ（advisory lock、FR-015 の一部・SC-004）。

### Implementation for User Story 1

- [X] T014 [US1] `ops/src/horseracing_ops/routers/refresh.py`：`POST /ops/v1/races/{race_id}/refresh` を実装（`is_valid_race_id`＋DB 存在確認→`enqueue_race`→202、未存在 404／非12桁 422）。
- [X] T015 [US1] `ops/src/horseracing_ops/routers/jobs.py`：`GET /ops/v1/jobs/{job_id}` を実装（ingestion_jobs を Job スキーマで返す、未存在 404）。
- [X] T016 [US1] ops のライブ OpenAPI から `front/ops-openapi.json` スナップショットを生成・コミットし、`front/src/api/ops-schema.d.ts` を `openapi-typescript` で生成。drift-check スクリプト（015 の check-openapi 同様）を追加。
- [X] T017 [P] [US1] `front/src/api/opsClient.ts`：ops API クライアント（`/ops/v1` への POST/GET、生成型 ops-schema.d.ts を使用）。
- [X] T018 [US1] `front/src/components/RefreshButton.tsx`：受付/取得中/成功/一部成功/失敗/対象なし(skipped) の状態表示＋job polling（1〜2秒間隔、終端 succeeded/partial/failed/skipped で停止）。失敗・対象なしでも既存表示を壊さない（FR-008, FR-012, FR-013）。
- [X] T019 [US1] `front/src/pages/RaceDetailPage.tsx` に RefreshButton を設置し、終端(succeeded/partial)で 014 race detail の react-query を invalidate→再取得（FR-011）。表示は 014 経由のままで、推定/疑似値の既存 PseudoBadge ラベル経路を変更しない（FR-022）。
- [X] T020 [P] [US1] `front/src/components/RefreshButton.test.tsx`（Vitest+MSW）：状態遷移（skipped 終端含む）・polling・完了後 invalidation を検証し、更新後も既存の PseudoBadge 等の疑似値ラベルが維持される（FR-022）ことを確認。

**Checkpoint**: US1 単独で「詳細ボタンで 1 レース最新化」が成立（MVP）

---

## Phase 4: User Story 2 - 一覧でその日の全レースを更新 (Priority: P2)

**Goal**: 一覧の「この日を更新」で日次バッチを起動し、レース別進捗と失敗分の再実行を提供

**Independent Test**: 開催日の一覧で押下→各レースに子ジョブ起動→レース別 status が見える→失敗レースのみ再実行できる

### Tests for User Story 2

- [X] T021 [P] [US2] `ops/tests/integration/test_refresh_day_contract.py`：`POST /ops/v1/days/{date}/refresh` が 202(BatchAccepted)/404(レース無)/422、`GET /ops/v1/batches/{trace_id}` が 200/404。
- [X] T022 [P] [US2] `ops/tests/integration/test_refresh_day_flow.py`：日次 enqueue で race ごとに子ジョブ生成（共通 trace_id）、一部失敗で batch=partial、失敗 race の単体 refresh 再実行が成立（FR-009/010）。

### Implementation for User Story 2

- [X] T023 [US2] `ops/src/horseracing_ops/enqueue.py` に `enqueue_day(session, date, *, force=False)` を追加：その日の全 race_id 列挙（DB read、`is_valid_race_id` 通過のみ）→親 `refresh_day`＋子 `refresh_race` を共通 `trace_id` で INSERT。
- [X] T024 [US2] `ops/src/horseracing_ops/routers/refresh.py` に `POST /ops/v1/days/{date}/refresh` を追加。
- [X] T025 [US2] `ops/src/horseracing_ops/routers/jobs.py` に `GET /ops/v1/batches/{trace_id}`（子 status 集約：total/succeeded/failed/running＋children）を追加。
- [X] T026 [US2] `ops/src/horseracing_ops/worker.py` に同時実行数の上限（並列度キャップ）を実装し、既存 `HttpFetcher` の domain rate-limit/robots/backoff（FR-014）と二重に netkeiba 負荷を抑制（FR-016）。
- [X] T027 [US2] `front/src/components/DayRefreshButton.tsx`：日次バッチ起動＋batch polling＋レース別進捗表示＋失敗レースの再実行ボタン。
- [X] T028 [US2] `front/src/pages/RaceListPage.tsx` に DayRefreshButton を設置（開催日単位）。
- [X] T029 [P] [US2] `front/src/components/DayRefreshButton.test.tsx`（Vitest+MSW）：バッチ進捗・レース別表示・失敗分再実行を検証。

**Checkpoint**: US1・US2 が各々独立に機能

---

## Phase 5: User Story 3 - 重複起動の抑制と更新履歴の可視化 (Priority: P3)

**Goal**: 鮮度判定による不要な再取得抑制・force 強制再取得・監査記録の確認

**Independent Test**: 同一レース連打で reused=true、直近成功は再取得せず、force=true で再取得、ingestion_jobs に履歴が残る

### Tests for User Story 3

- [X] T030 [P] [US3] `ops/tests/integration/test_freshness_force.py`：直近 N 分 succeeded は `reused=true`（再取得しない）、`force=true` で再取得、active ジョブは force でも再利用（FR-015, SC-004）。
- [X] T031 [P] [US3] `ops/tests/integration/test_audit_recorded.py`：各更新の job_type/scope_value/status/件数/時刻/summary が `ingestion_jobs` に記録される（FR-017, SC-007）。

### Implementation for User Story 3

- [X] T032 [US3] `ops/src/horseracing_ops/enqueue.py` に鮮度判定（直近 N 分以内 succeeded の再利用）と `force` フラグ処理を追加（advisory lock 配下、データモデル D3 準拠）。
- [X] T033 [US3] `ops/src/horseracing_ops/config.py`（または settings）に鮮度しきい値 N と worker 並列度・stale-running しきい時間を集約（既定値を定義、調整可能）。
- [X] T034 [P] [US3] front：reused/鮮度の結果をユーザーに軽く提示（「最新の取得を再利用」等）。利益語・損益色は使わない（憲法 V・製品方針）。

**Checkpoint**: 全ストーリーが独立に機能

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T035 `deploy/docker-compose.yml` に `ops`(owner, DATABASE_URL_OWNER) と `worker`(owner) サービスを追加し、`nginx` で `/ops/` を ops へリバースプロキシ（014 read-only サービスは無変更、fail-closed/migrate 依存は既存踏襲）。
- [X] T036 [P] `front/ops-openapi.json` と生成型の drift-check を CI/テストに組み込み（ライブ ops OpenAPI とスナップショット一致を検証）。
- [X] T037 [P] `ops/tests/unit/` に enqueue/runner のエッジケース単体テスト（種別判定境界・netkeiba 未公開→skipped・retry 上限→failed）。
- [X] T038 `quickstart.md` の end-to-end を実 DB（horseracing@15432）でスモーク実行し、US1→US2→US3 を確認。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 即着手可
- **Foundational (Phase 2)**: Setup 完了に依存。**全ユーザーストーリーをブロック**
- **User Stories (Phase 3+)**: Foundational 完了後。US1→US2→US3 の優先順（または並列）
- **Polish (Phase 6)**: 対象ストーリー完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後に着手、他ストーリー非依存（MVP）
- **US2 (P2)**: Foundational 後。enqueue/worker を US1 と共有するが独立テスト可。`enqueue_day` は `enqueue_race` の上に積む
- **US3 (P3)**: Foundational 後。enqueue の鮮度/force 拡張。US1/US2 の dedup を強化する位置づけ

### Within Each User Story

- テストを先に書き、FAIL を確認してから実装
- enqueue/runner（サービス）→ ルーター（エンドポイント）→ front（UI）の順
- ストーリー完了→次優先へ

### Parallel Opportunities

- Setup の [P]（T003）は他と並行可
- Foundational の [P]（T007 schemas, T009 leak-guard, T010 readonly）は並行可
- 各ストーリーのテスト [P] は並行可。front 型/コンポーネント（T017, T020, T029）は別ファイルで並行可
- Foundational 完了後は US1/US2/US3 を別担当で並行可能

---

## Parallel Example: User Story 1

```bash
# US1 のテストをまとめて起動:
Task: "T011 contract test in ops/tests/integration/test_refresh_race_contract.py"
Task: "T012 flow test in ops/tests/integration/test_refresh_race_flow.py"
Task: "T013 dedup test in ops/tests/integration/test_dedup_concurrent.py"

# US1 の独立ファイル実装を並行:
Task: "T017 ops client in front/src/api/opsClient.ts"
Task: "T020 button test in front/src/components/RefreshButton.test.tsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup → 2. Phase 2 Foundational（境界テスト含む、CRITICAL）→ 3. Phase 3 US1 → **STOP & VALIDATE**（詳細ボタンで 1 レース最新化）→ デモ可

### Incremental Delivery

1. Setup + Foundational → 土台
2. US1（詳細 1 レース）→ 独立検証 → MVP デモ
3. US2（日次バッチ）→ 独立検証 → デモ
4. US3（dedup 鮮度・監査）→ 独立検証 → デモ
5. Polish（compose に ops/worker 追加・drift-check・実 DB スモーク）

---

## Notes

- 新 DB スキーマ変更なし（`ingestion_jobs` の既存カラム＋advisory lock）
- ops/worker は owner ロール、014 は app_ro のまま（read/write 物理分離）
- 取り込んだ odds/results はモデル特徴量に流さない（leak-guard T009）
- ネットワーク非依存テスト＝保存フィクスチャ＋スタブ fetcher（008/022 踏襲）
- 各タスク/論理単位ごとにコミット。チェックポイントでストーリー独立検証
- 設計の非自明点は codex second opinion 済み（research.md D1〜D7）。実装中に新たな分岐が出たら再度 second opinion を取る
