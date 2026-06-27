# Research: 本番デプロイ構成 (018)

Phase 0。リポジトリ初のインフラ層。014/015 を無改変方針（health の read-only head 検証のみ追加）で
コンテナ化し、nginx single-origin + docker compose で運用可能にする。codex 指摘（fail-closed 起動順序 /
DB 権限分離 / 依存閉包 + OpenAPI 同期）を設計に反映。

---

## R1: API イメージ（マルチステージ + 依存閉包）

**Decision**: `deploy/api.Dockerfile`、**build context = リポジトリルート**（per-package uv.lock で editable
path 依存 ../db ../probability ../eval を解決するため）。builder: `python:3.12-slim` + uv、`api/ db/
probability/ eval/`（API 依存閉包、確認済み: api→{db,probability}, probability→{db,eval}, eval→{db}）を
コピーし `cd api && uv sync --frozen --no-dev`。runtime: `python:3.12-slim`、**非 root**、venv + source を
コピー、`uvicorn horseracing_api.app:app --host 0.0.0.0 --port 8000`。features/serving/training は不要なので
含めない。

**Rationale**: 単一 venv に editable path 依存を入れるには context にそれらのソースが必要（codex #C）。閉包を
最小（4 パッケージ）に絞りイメージを軽くする。

**Alternatives**: 各パッケージを wheel 化 → ビルド複雑、却下。features/serving/training も含める → 不要に肥大、却下。

---

## R2: front イメージ（静的ビルド + nginx）

**Decision**: `deploy/front.Dockerfile`、build context = `front/`。builder: `node:22-slim` + corepack pnpm、
`pnpm install --frozen-lockfile` → `pnpm build`（tsc -b + vite build → `dist/`）。runtime: `nginx:alpine`、
`dist/` を `/usr/share/nginx/html` へ、`deploy/nginx.conf` を配置。committed `front/openapi.json` で型同期
（API 不要でビルド可）。

**Rationale**: 静的配信が最軽量。015 の型は committed snapshot からビルドされるので決定論的。

**Alternatives**: SSR / Node ランタイム配信 → read-only SPA に不要、却下。

---

## R3: nginx single-origin routing

**Decision**: `deploy/nginx.conf`:
- `location ^~ /api/v1/ { proxy_pass http://api:8000; }` を `location /` より**優先**、**パス保持**（末尾
  スラッシュを付けず `/api/v1/...` をそのまま upstream へ）。`proxy_set_header Host/X-Real-IP/
  X-Forwarded-For/X-Forwarded-Proto` 付与。
- `location / { try_files $uri $uri/ /index.html; }`（SPA history fallback、`/` 配下のみ）。
- 静的 asset に cache ヘッダ、`/healthz` で静的 200（nginx healthcheck）。

**Rationale**: codex #B。`^~` で API を fallback より先に確定 → 未知 `/api/...` は API の 404 を返し index.html
に化けない。パス保持で `/api/v1/health` が upstream で `/health` に化けない。

**Alternatives**: API を別オリジン + CORS → 015 の deferred を解消しない、却下。

---

## R4: compose トポロジ + fail-closed 起動順序

**Decision**: `deploy/docker-compose.yml`、サービス:
- `db`: `postgres:16`、`healthcheck: pg_isready`、named volume 永続化、env `POSTGRES_*`。
- `migrate`: API イメージ（db/alembic を含む）、`command: alembic -c db/alembic.ini upgrade head` + read-only
  ロール作成 SQL、`depends_on: db: service_healthy`、`restart: "no"`。**owner ロール**の DATABASE_URL を使う。
- `api`: API イメージ、`depends_on: migrate: service_completed_successfully`、`healthcheck: /api/v1/health`、
  **read-only ロール**の DATABASE_URL。
- `nginx`: front イメージ、`depends_on: api: service_healthy`、`ports: 8080:80`、healthcheck `/healthz`。

**Rationale**: codex #A。`service_completed_successfully` で migrate 成功（exit 0）後にのみ API 起動 →
fail-closed（古いスキーマで silent 起動しない）。`restart:"no"` で migrate は一度きり、冪等（head 到達済み →
no-op）。

**Alternatives**: API 内でマイグレーション実行 → read-only/権限分離を壊す、却下。`depends_on` のみ（healthcheck
無し）→ 起動順序が保証されない、却下。

---

## R5: health の alembic head 検証（read-only）

**Decision**: 既存 `/api/v1/health` を拡張し、read-only で `SELECT 1` + `alembic_version.version_num` を取得、
**API イメージにバンドルされた db/migrations の ScriptDirectory head と一致**を検証。未一致/未接続は unhealthy
（503）。これは SELECT のみで read-only 不変を壊さない（014 の write 非露出を維持）。

**Rationale**: codex #A/#E。マイグレーション未適用での silent 起動を検出。head を env でなくバンドル migrations
から計算するので手動同期不要。

**Alternatives**: env `EXPECTED_ALEMBIC_HEAD` 手動指定 → 同期漏れリスク、却下。SELECT 1 のみ → 未適用検出不可、却下。

---

## R6: DB 権限分離（serving=read-only / migration=owner）

**Decision**: migrate one-shot が `alembic upgrade head` 後に read-only ロールを冪等に用意:
`CREATE ROLE app_ro LOGIN PASSWORD ...;`（存在すれば skip）→ `GRANT CONNECT ON DATABASE`、`GRANT USAGE ON
SCHEMA public`、`GRANT SELECT ON ALL TABLES IN SCHEMA public`、`ALTER DEFAULT PRIVILEGES ... GRANT SELECT`。
API は `DATABASE_URL`（app_ro）で接続、migrate は `DATABASE_URL_OWNER`（owner）。`deploy/scripts/grant_ro.sql`。

**Rationale**: codex #2/#E。serving の read-only を**DB 権限**で担保（アプリ実装依存にしない）。新規テーブルにも
default privileges で SELECT 付与。

**Alternatives**: 単一ロール → read-only がアプリ依存、却下。

---

## R7: 再現性・シークレット・hardening

**Decision**: ベースイメージは tag pin（release は digest 固定を明記）。`uv sync --frozen` / `pnpm install
--frozen-lockfile`。`.env.example` 提供、実 `.env` は `.gitignore`/非コミット、`docker compose --env-file`。
リポジトリルート `.dockerignore`（`.git`/`.venv`/`node_modules`/`__pycache__`/`dist`/`specs`/`.env*` 除外）+
`front/.dockerignore`。コンテナ非 root、named volume、`git SHA` を image label。compose 内 postgres は
local/staging（本番は外部マネージド DB 推奨、DATABASE_URL で接続）。

**Rationale**: codex #C/#D/#G。憲法 V（再現性）。シークレットを repo/イメージに焼かない。

**Alternatives**: シークレットを compose にハードコード → 漏洩、却下。

---

## 設計判断サマリ（codex second opinion 反映）

| 論点 | 採用 | codex |
|---|---|---|
| 起動順序 | migrate `service_completed_successfully` + health で alembic head 検証（fail-closed） | #A → R4/R5 |
| routing | `^~ /api/v1/` 優先・パス保持・X-Forwarded-*・`/` のみ fallback | #B → R3 |
| 依存閉包 | API イメージ = api/db/probability/eval、context=repo root、OpenAPI 同期確認 | #C → R1 + quickstart |
| 権限分離 | serving=SELECT のみ / migration=owner、default privileges | #2/#E → R6 |
| 永続化/本番DB | named volume、本番は外部マネージド DB 推奨 | #D → R4/R7 |
| 再現性/hardening | base pin・frozen lockfile・.dockerignore・非 root・git SHA label | #C/#G → R7 |
