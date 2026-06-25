# Quickstart: read-only 予測配信 API の検証

実装後に「API 起動 → 各エンドポイント取得 → OpenAPI/docs 確認」が動くことを確認する手順。

## 前提

- Feature 001–013 が適用済み（DB に レース/予測/オッズ/推奨が永続）。
- 新規 `api/` パッケージ（FastAPI + uvicorn + pydantic）。`db`/`probability` に依存（**betting 非依存**）。

## セットアップ / 起動

```bash
cd api && uv sync
export DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing
uv run uvicorn horseracing_api.app:app --reload --port 8000
```

## エンドポイント検証（read-only）

```bash
curl localhost:8000/api/v1/health
curl "localhost:8000/api/v1/races?date=2008-06-01&page=1&page_size=20"
curl localhost:8000/api/v1/races/200806010101
curl "localhost:8000/api/v1/races/200806010101/predictions?bet_type=exacta&top=10"
curl localhost:8000/api/v1/races/200806010101/odds
curl localhost:8000/api/v1/races/200806010101/recommendations
open localhost:8000/docs      # OpenAPI UI（front 契約）
```

期待:
- `/health` 200。`/races` は安定順序・ページング。`/races/{id}` は出走表（404 if 無し）。
- `/predictions` は決定論選択 run（active→computed_at→run_id）の win/top2/top3 + 監査、bet_type 指定時のみ joint 上位 K。
- `/odds` は win(real)/estimated(疑似)/real_exotic(coverage) を別フィールドで区別。
- `/recommendations` は永続行のみ（生成しない）、二重疑似ラベル付き。
- `/docs`・`/openapi.json` が全エンドポイントを網羅。

## テスト

```bash
cd api
uv run pytest tests/unit         # スキーマ・run 選択・canonical・エラー変換・ページング（TestClient）
uv run pytest -m integration     # 実 DB で各エンドポイント・404/空・決定論 run・実/推定区別・書込非発生
```

検証する受け入れ基準:

- **SC-001**: /health・/races（絞込・ページング）・/races/{id} が動作、欠損は 404/型付き空で一貫。
- **SC-002**: 予測が決定論 run（active→computed_at→run_id）で返り監査情報が全付与。
- **SC-003**: 結合確率は bet_type+上位 K 限定、無指定で大グリッドを返さない、canonical 母集団。
- **SC-004**: オッズが実/推定を判別スキーマ（source/estimated/coverage/updated_at）で返り混在しない。
- **SC-005**: 推奨が永続行 SELECT のみで返り書込しない（テストで `recommendations` 行数不変を assert）、二重疑似ラベル。
- **SC-006**: 全エンドポイントが /api/v1 で版付け、OpenAPI/docs 自動生成、エラーモデル一貫。
- **SC-007**: 応答値をモデル特徴に還流しない。読み取り専用・スキーマ変更なし。

## 核心の考え方（書込禁止 / 契約先行）

API は**既存の永続データとモデル出力を配信するのみ**。特に推奨は**永続行を SELECT するだけ**で、書込ジェネレータ
（`generate_exotic_recommendations`）を呼ばない（`api/` は betting に依存しない）。pydantic スキーマが OpenAPI を生成し、これが
front(React/Vite, 015) の契約になる（憲法 VI: 画面の前に契約を確定）。実/推定/疑似/二重疑似を判別ラベルで区別し、front の誤認を防ぐ。
