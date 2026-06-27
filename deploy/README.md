# deploy/ — 本番デプロイ構成 (Feature 018)

014 (read-only API) と 015 (read-only front SPA) をコンテナ化し、**nginx single-origin reverse-proxy** +
**docker compose** で `compose up` 一発起動する。スキーマ変更なし。serving は read-only（DB 権限で担保）。

## 構成

| サービス | イメージ | 役割 |
|---|---|---|
| db | postgres:16 | データ（named volume `pgdata` で永続化） |
| migrate | horseracing-api (api.Dockerfile) | `alembic upgrade head` + read-only ロール作成（one-shot, owner） |
| api | horseracing-api | read-only serving（app_ro, uvicorn） |
| nginx | horseracing-front (front.Dockerfile) | 静的 front + `/api/v1/*` proxy（公開は :8080 のみ） |

起動順序（fail-closed）: `db(healthy) → migrate(exit 0) → api(healthy) → nginx`。migrate が失敗すると
API は起動しない（`depends_on: migrate condition: service_completed_successfully`）。`/api/v1/health` は
DB 接続 + **alembic head 一致**を read-only 検証し、未同期なら 503。

## routing（deploy/nginx.conf, 単一オリジン）

| パス | 行き先 |
|---|---|
| `/api/v1/*`, `/openapi.json`, `/docs` | API（パス保持 proxy、`X-Forwarded-*` 付与） |
| `/assets/*` | 静的アセット（長期キャッシュ） |
| `/`, `/races/<id>` 等 | `index.html`（SPA history fallback、`/api/*` は対象外） |

未知 `/api/v1/*` は API の 404 を返す（index.html に化けない）。

## 使い方

```sh
cd deploy
cp .env.example .env          # 値を編集（owner/app_ro パスワード等）。.env はコミットしない
docker compose build
docker compose up --wait -d   # 全サービスが healthy/完了するまで待機
# 受け入れ
bash scripts/smoke.sh
bash scripts/verify_routing.sh
bash scripts/verify_readonly.sh
bash scripts/verify_failclosed.sh   # migration 失敗 → API 不起動（fail-closed、最後に teardown）
# 停止
docker compose down            # コンテナ削除（volume 保持）
docker compose down -v         # volume も削除（DB 初期化）
```

## DB 権限分離（read-only を DB で担保）

- `migrate.py`（owner）が `alembic upgrade head` 後に `app_ro` ロールを冪等作成し **SELECT のみ**付与
  （`ALTER DEFAULT PRIVILEGES` で将来テーブルも SELECT 可、write は REVOKE）。等価な psql 版は
  `scripts/grant_ro.sql`。
- API は `DATABASE_URL`(app_ro) で接続 → write は権限エラー。migration のみ `DATABASE_URL_OWNER`。

## 再現性

- ベースイメージは tag 固定（release は digest 固定推奨）。`uv sync --frozen` / `pnpm install --frozen-lockfile`。
- API イメージ依存閉包 = api/db/probability/eval（probability→eval）。
- live `/openapi.json` == `front/openapi.json`（015 型同期）を `verify_readonly.sh` で確認。

## 本番運用の注意 / deferred

- compose 内 postgres は **local/staging**。本番は外部マネージド DB を `DATABASE_URL` / `DATABASE_URL_OWNER`
  で指定し、`db` サービスは使わない構成を推奨。
- `.env`・シークレットは repo / イメージに含めない（`.dockerignore` で除外）。
- **deferred**: Kubernetes/Helm、クラウド固有、TLS 証明書の自動化、オートスケール、CI/CD、シークレット
  マネージャ、観測スタック（metrics/tracing/集約ログ）、DB backup 自動化、ライブ serving（未来レース）。
