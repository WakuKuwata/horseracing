# Data Model: 本番デプロイ構成 (018)

**DB スキーマ変更なし**（head は 0006 のまま）。本 feature の「モデル」はデプロイ構成・イメージ・DB ロール。
唯一のアプリ変更は `/api/v1/health` の read-only head 検証（write なし）。

---

## 1. DB スキーマ

変更なし。新規テーブル・列・マイグレーションを追加しない（FR-010）。migration head = `0006_stake_fraction`。

---

## 2. DB ロール（権限分離、migrate one-shot が冪等に用意）

| ロール | 用途 | 権限 |
|---|---|---|
| owner（既存/POSTGRES_USER 由来） | マイグレーション（DDL） | スキーマ所有・全権限 |
| `app_ro`（新規、migrate が作成） | API serving | CONNECT + USAGE(public) + SELECT(全テーブル + default privileges) |

- 冪等: `DO $$ ... IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='app_ro') ...`。
- API は `DATABASE_URL`(app_ro) で接続 → write は権限エラー（read-only を DB で担保、FR-008/SC-006）。
- migrate は `DATABASE_URL_OWNER`(owner) で `alembic upgrade head` + `grant_ro.sql`。

---

## 3. compose サービス（宣言的構成、再現キー）

| サービス | イメージ | depends_on (condition) | healthcheck | 役割 |
|---|---|---|---|---|
| `db` | postgres:16 (pin) | — | pg_isready | データ（named volume 永続化） |
| `migrate` | api image | db: service_healthy | —（one-shot, restart:no） | alembic upgrade head + grant_ro |
| `api` | api image | migrate: service_completed_successfully | GET /api/v1/health | read-only serving（app_ro） |
| `nginx` | front image | api: service_healthy | GET /healthz | 静的 front + /api/v1/* proxy |

- env_file=`.env`（非コミット）。ポート公開は nginx のみ（api/db は内部ネットワーク）。
- 再現性: 各 image は tag/digest + git SHA label、`uv sync --frozen` / `pnpm --frozen-lockfile`。

---

## 4. イメージ（成果物）

- **api image**（`deploy/api.Dockerfile`, context=repo root）: 依存閉包 api/db/probability/eval、非 root、
  uvicorn。migrate も同一イメージ（alembic + db/migrations 同梱）。
- **front image**（`deploy/front.Dockerfile`, context=front/）: vite 静的ビルド + nginx + nginx.conf。

---

## 5. `/api/v1/health` レスポンス拡張（read-only、唯一のアプリ変更）

| フィールド | 意味 |
|---|---|
| status | ok / unhealthy |
| db | 接続可否（SELECT 1） |
| alembic_current | DB の alembic_version.version_num |
| alembic_head | バンドル migrations の ScriptDirectory head |
| schema_in_sync | alembic_current == alembic_head |

- `schema_in_sync=false` or db 不可 → HTTP 503（healthcheck 失敗 → 起動順序が守られる）。
- 全て read-only（SELECT のみ）。write 経路を追加しない（FR-006/FR-010）。

---

## 6. 不変条件 / 境界

- read-only: API は app_ro（SELECT のみ）。migration は owner の起動前 one-shot（read-only の明示的例外）。
- スキーマ変更 0。新規 write エンドポイント 0。
- single-origin: front は相対 `/api/v1/*`、nginx が同一オリジンで proxy（CORS 不要）。
- 再現性: pin イメージ + frozen lockfile + 宣言的 compose。シークレットは repo/イメージに非格納。
- OpenAPI 同期: live `/openapi.json` == front committed snapshot（受け入れで確認）。
