# Quickstart: 本番デプロイ構成 (018)

CI なしで再現可能な受け入れ手順。詳細は [contracts/deploy_compose.md](contracts/deploy_compose.md)。

## 前提

- Docker + docker compose v2。スキーマ変更なし（既存 head 0006）。
- `deploy/.env` を `deploy/.env.example` から作成（DATABASE_URL=app_ro / DATABASE_URL_OWNER=owner /
  POSTGRES_* / ro パスワード）。実 `.env` はコミットしない。

## 起動

```
cd deploy
docker compose config            # 構成検証（YAML/参照の妥当性）
docker compose build             # api(context=repo root) + front(context=front/)
docker compose up --wait         # postgres→migrate→api→nginx が healthy/完了になるまで待機
```

期待: `migrate` が exit 0、`api` が healthy（/api/v1/health 200）、`nginx` が healthy。

## 受け入れ検証

```
# 1) health（alembic head 同期込み）
curl -s localhost:8080/api/v1/health         # {status:ok, schema_in_sync:true, ...}
# 2) API データ（≥1 行ある DB で）
curl -s 'localhost:8080/api/v1/races?page_size=2'
# 3) front 単一オリジン
curl -s localhost:8080/ | grep -q '<div id="root"' && echo front-ok
# 4) SPA deep link → index.html
curl -s localhost:8080/races/200805030401 | grep -q '<div id="root"' && echo deeplink-ok
# 5) 未知 API パスは API 404（index.html に化けない）
curl -s -o /dev/null -w '%{http_code}\n' localhost:8080/api/v1/does-not-exist   # 404
# 6) OpenAPI 同期（live == committed snapshot）
diff <(curl -s localhost:8080/openapi.json | python -m json.tool --sort-keys) \
     <(python -m json.tool --sort-keys < ../front/openapi.json) && echo openapi-in-sync
```

## read-only 権限確認（SC-006）

```
# app_ro で write を試行 → 権限エラー（read-only を DB で担保）
docker compose exec -e PGPASSWORD=$RO_PW db \
  psql -U app_ro -d horseracing -c "INSERT INTO races(race_id) VALUES ('x')" ; echo "exit=$?"  # 失敗
```

## fail-closed 確認（SC-004）

```
# DATABASE_URL_OWNER を不正にして起動 → migrate 失敗 → api は起動しない
# （compose ps で api が Up にならないこと）
```

## 停止

```
docker compose down            # コンテナ削除（volume は保持）
docker compose down -v         # volume も削除（DB 初期化）
```

## 受け入れ基準

- compose up が成功し SC-001〜008 を満たす（health/データ/deep link/未知404/OpenAPI 同期/read-only/スキーマ0）。
- 本番は外部マネージド DB（DATABASE_URL 差し替え）。compose postgres は local/staging。
