# Tasks: 本番デプロイ構成（コンテナ化 + single-origin reverse-proxy）

**Input**: Design documents from `specs/018-production-deploy/`
**Prerequisites**: plan.md, spec.md, research.md (R1–R7), data-model.md, contracts/deploy_compose.md, quickstart.md

**Tests**: 含む（health の read-only head 検証は unit、read-only 境界は既存 guard 拡張、compose は config 検証 + up smoke）

**Organization**: User story 単位（P1 US1 compose 一発起動 → P1 US2 single-origin routing → P2 US3 再現性・read-only・権限分離）。MVP=US1。

## パス規約

リポジトリ初のインフラ層 = 新規 `deploy/`。アプリ変更は `api/src/horseracing_api/routers/races.py` の
`/health` read-only 拡張のみ（write 経路追加なし）。**スキーマ変更なし**（head=0006）。API イメージ依存閉包 =
api/db/probability/eval（context=repo root）。確認済み: health は races.py:38、read-only guard は
`api/tests/unit/test_no_write_boundary.py`。

---

## Phase 1: Setup（deploy 雛形・ignore・env）

- [X] T001 [P] `deploy/` を作成し `.env.example`（DATABASE_URL=app_ro / DATABASE_URL_OWNER=owner / POSTGRES_USER/PASSWORD/DB / RO パスワード）を置く
- [X] T002 [P] リポジトリルート `.dockerignore`（`.git`/`.venv`/`**/node_modules`/`**/__pycache__`/`**/dist`/`specs`/`.env*`/`artifacts`/`raw_data` を除外）と `front/.dockerignore`（node_modules/dist/.env*）を作成

**Checkpoint**: deploy 雛形・ビルドコンテキスト除外・env テンプレが揃う。

---

## Phase 2: Foundational（イメージ・nginx・health — 全 US 前提）

**⚠️ Dockerfile 群と nginx.conf、health の head 検証を確定。US1/US2/US3 全てが依存。**

- [X] T003 `deploy/api.Dockerfile` を作成: マルチステージ（builder=python:3.12-slim+uv、context=repo root で `api/ db/ probability/ eval/` をコピーし `cd api && uv sync --frozen --no-dev`）、runtime=python:3.12-slim 非 root、`uvicorn horseracing_api.app:app --host 0.0.0.0 --port 8000`。db/migrations 同梱（migrate も同イメージ）（R1, FR-001）
- [X] T004 `deploy/front.Dockerfile` を作成: マルチステージ（builder=node:22-slim+corepack pnpm、context=front/ で `pnpm install --frozen-lockfile` → `pnpm build`）、runtime=nginx:alpine に `dist/` と `nginx.conf` を配置（R2, FR-002）
- [X] T005 `deploy/nginx.conf` を作成: `location ^~ /api/v1/ { proxy_pass http://api:8000; }`（SPA fallback より優先・**パス保持**・`Host`/`X-Real-IP`/`X-Forwarded-For`/`X-Forwarded-Proto` 付与）、`location = /healthz { return 200; }`、`location / { try_files $uri $uri/ /index.html; }`、静的 asset に cache-control（R3, FR-003）
- [X] T006 `api/src/horseracing_api/routers/races.py` の `/health` を read-only 拡張: `SELECT 1` + `alembic_version.version_num` を取得し、バンドル db/migrations の ScriptDirectory head と比較。`{status, db, alembic_current, alembic_head, schema_in_sync}` を返し、未接続/未同期は HTTP 503。SELECT のみ（write 追加なし）（R5, FR-006, SC-005）
- [X] T007 [P] `api/tests/unit/test_health_schema_check.py` を作成: schema_in_sync=true で 200、alembic_current が head と不一致なら 503、全て read-only（write 呼び出し無し）を検証（SC-005）

**Checkpoint**: イメージ・routing・health が確定（compose 前提が揃う）。

---

## Phase 3: User Story 1 - compose 一発起動（Priority: P1）🎯 MVP

**Goal**: `docker compose up` 一回で postgres→migrate→api→nginx が順序保証で起動し、単一オリジンで front/API にアクセス可能。

**Independent Test**: クリーン環境で `compose up --wait` → 全 healthy/完了、nginx 経由で front と /api/v1/health 応答。

### 実装

- [X] T008 [US1] `deploy/docker-compose.yml` を作成: `db`(postgres:16 pin, pg_isready healthcheck, named volume) / `migrate`(api image, `depends_on db:service_healthy`, restart:"no", owner DATABASE_URL) / `api`(api image, `depends_on migrate:service_completed_successfully`, healthcheck /api/v1/health, app_ro DATABASE_URL) / `nginx`(front image, `depends_on api:service_healthy`, ports 8080:80, healthcheck /healthz)。公開は nginx のみ（R4, FR-004/FR-009）
- [X] T009 [US1] `deploy/scripts/migrate.py`（API イメージの venv で実行、psql 不要）を作成: owner で `alembic upgrade head` → read-only ロールを冪等付与（成功で exit 0、失敗で非 0 = fail-closed）。compose の migrate サービスが `python /app/deploy/scripts/migrate.py` で実行（scripts を volume mount）。psql 等価形は `grant_ro.sql`（R4/R6, FR-005）

### US1 テスト

- [X] T010 [P] [US1] `deploy/scripts/smoke.sh` を作成（quickstart 受け入れ）: `docker compose config` → `build` → `up --wait` → migrate exit 0 確認 → `curl :8080/api/v1/health`=200 → `curl :8080/api/v1/races`=データ を検証（SC-001/SC-002）

**Checkpoint**: US1 単独で起動・基本疎通（MVP）。

---

## Phase 4: User Story 2 - single-origin routing（front/API 境界）（Priority: P1）

**Goal**: 単一オリジンで front 描画・SPA deep link 動作・未知 /api は API 404（index.html に化けない）。

**Independent Test**: nginx 経由で `/`・deep link が index.html、`/api/v1/races` が JSON、未知 `/api/...` が 404。

### 実装

- [X] T011 [US2] `deploy/nginx.conf` の routing 境界を確定（T005 を検証可能な形に）: `/api/v1/*` パス保持の proxy、`/` 配下のみ history fallback、未知 `/api/...` は fallback 対象外（API の 404 を返す）。`deploy/README.md` に routing 表を記載（FR-003）

### US2 テスト

- [X] T012 [P] [US2] `deploy/scripts/verify_routing.sh` を作成: nginx 経由で (a) `/` と `/races/<id>` deep link が index.html を返す、(b) `/api/v1/races` が JSON、(c) 未知 `/api/v1/does-not-exist` が **404**（index.html でない）、(d) `/api/v1/health` が 200 を検証（SC-003）

**Checkpoint**: US2 単独で routing 境界が正しい。

---

## Phase 5: User Story 3 - 再現性・read-only・権限分離（Priority: P2）

**Goal**: serving=app_ro(SELECT のみ)・migration=owner、宣言的構成で再現、live OpenAPI と snapshot 一致。

**Independent Test**: app_ro で write 拒否、owner で migration 成功、再ビルドで同一構成、OpenAPI 一致。

### 実装

- [X] T013 [US3] `deploy/scripts/grant_ro.sql` を作成: `app_ro` を冪等に作成（`IF NOT EXISTS`）し CONNECT + USAGE(public) + SELECT(全テーブル) + ALTER DEFAULT PRIVILEGES GRANT SELECT を付与（R6, FR-008, SC-006）
- [X] T014 [US3] `deploy/api.Dockerfile`/`front.Dockerfile` の再現性を固める: ベースイメージ tag pin（release は digest 固定をコメント明記）、`uv sync --frozen`/`pnpm install --frozen-lockfile`、`LABEL org.opencontainers.image.revision=<git SHA>`、非 root 実行を確認（R7, FR-011）

### US3 テスト

- [X] T015 [P] [US3] `deploy/scripts/verify_readonly.sh` を作成: app_ro で `INSERT` が権限エラー、owner で migration 成功、`diff` で live `/openapi.json`（sort-keys）== `front/openapi.json` を検証（SC-006/SC-007）
- [X] T016 [P] [US3] `api/tests/unit/test_no_write_boundary.py` を確認/拡張: 014 の read-only 境界（write エンドポイント非露出）が維持されていることを assert（FR-010, SC-008）

**Checkpoint**: 全 P1+P2 完了。再現性・read-only・権限分離が検証可能。

---

## Phase 6: Polish & Cross-Cutting

- [X] T017 [P] `deploy/README.md` を作成（日本語）: up/down、migrate one-shot、ログ確認、env 設定、本番は外部マネージド DB 推奨、compose postgres は local/staging、deferred 一覧（k8s/TLS/CI/観測/backup）（FR-014, FR-012）
- [X] T018 `specs/018-production-deploy/quickstart.md` を実行（実環境スモーク）: `docker compose up --wait` → smoke.sh / verify_routing.sh / verify_readonly.sh を通し SC-001〜008 を確認
- [X] T019 [P] `CLAUDE.md` に 018 の 1 行サマリを追記（011–017 と同形式: deploy 層・single-origin nginx・fail-closed 起動順序・health head 検証・DB 権限分離・依存閉包・スキーマ変更なしを要約）

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 先頭。T001/T002[P]。
- **Phase 2 (Foundational)**: Setup 後。T003/T004/T005 → T006 → T007[P]。**全 US をブロック**（イメージ/routing/health）。
- **Phase 3 (US1, MVP)**: Foundational 後。T008→T009、テスト T010[P]。
- **Phase 4 (US2)**: Foundational(nginx)後、US1 と並行可。T011、テスト T012[P]。
- **Phase 5 (US3)**: US1(migrate)後。T013→T014、テスト T015/T016[P]。
- **Phase 6 (Polish)**: 全実装後。T017/T019[P]、T018。

### User Story 独立性

- US1 は compose 起動で独立（MVP）。US2 は routing 境界（foundational の nginx.conf を検証）。US3 は権限分離・再現性（US1 の migrate に role 付与を足す）。

## Parallel 実行例

- Setup: T001/T002 並走。Foundational test T007。
- US3: T015/T016 並走。Polish: T017/T019 並走。

## 実装戦略

1. **MVP first**: Phase 1→2→3（US1）で「compose up 一発起動 + 基本疎通」を最短達成。
2. **routing**: US2 で single-origin 境界（deep link / 未知 404）を確定・検証。
3. **本番品質**: US3 で read-only 権限分離・再現性・OpenAPI 同期。
4. 各 Checkpoint で独立検証。憲法 II（read-only 維持・migration は起動前 one-shot の write 例外）/ V（pin・frozen・宣言的 compose・health head 検証）/ VI（契約確定後・スキーマ変更なし）を維持。
