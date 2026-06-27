# Contract: デプロイ構成（compose / nginx / health）

リポジトリ初のインフラ層。`deploy/` に集約。アプリ変更は `/api/v1/health` の read-only 拡張のみ。

## compose サービス契約

```
db        postgres:16        healthcheck pg_isready          named volume   (内部のみ)
migrate   <api image>        depends_on db:service_healthy   restart:"no"   owner role
          command: alembic -c db/alembic.ini upgrade head  &&  psql -f deploy/scripts/grant_ro.sql
api       <api image>        depends_on migrate:service_completed_successfully  health /api/v1/health  app_ro
nginx     <front image>      depends_on api:service_healthy  health /healthz  ports 8080:80 (公開はここのみ)
```

- env: `.env`（非コミット、`.env.example` から）。`DATABASE_URL`(app_ro) / `DATABASE_URL_OWNER`(owner) /
  `POSTGRES_*`。
- fail-closed: migrate が exit≠0 なら api は起動しない。

## nginx routing 契約（`deploy/nginx.conf`）

```
location ^~ /api/v1/ {            # SPA fallback より優先・パス保持
    proxy_pass http://api:8000;   # 末尾スラッシュ無し → /api/v1/... を保持
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location = /healthz { return 200; }           # nginx healthcheck
location / { try_files $uri $uri/ /index.html; }   # SPA history fallback（/ 配下のみ）
# 静的 asset に cache-control
```

不変条件:
- `/api/v1/health` → API（200/503）。`/api/v1/races` → API JSON。未知 `/api/...` → API 404（index.html に化けない）。
- `/` と `/races/<id>`（deep link）→ index.html。

## DB ロール契約（`deploy/scripts/grant_ro.sql`、冪等）

```
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='app_ro') THEN
  CREATE ROLE app_ro LOGIN PASSWORD :'ro_pw'; END IF; END $$;
GRANT CONNECT ON DATABASE ... TO app_ro;
GRANT USAGE ON SCHEMA public TO app_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO app_ro;
```

不変条件: app_ro で write（INSERT/UPDATE/DELETE/DDL）は権限エラー。

## health 契約（read-only 拡張）

`GET /api/v1/health` → `{status, db, alembic_current, alembic_head, schema_in_sync}`。
`schema_in_sync=false` or db 不可 → **HTTP 503**。SELECT のみ（write 経路を追加しない）。

## 受け入れ（CI なし再現）

`docker compose config` → `docker compose build` → `docker compose up --wait` → migrate exit 0 →
`curl :8080/api/v1/health`=200 / `:8080/`=200 / deep link / 未知 `/api/...`=404 / app_ro write 拒否 /
live `/openapi.json` == `front/openapi.json`。
