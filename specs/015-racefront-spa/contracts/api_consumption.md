# Contract: 014 API の消費 / 型生成 / proxy

フロントは 014（read-only）を相対パスで消費。型は OpenAPI スナップショットから生成。

## 消費するエンドポイント（GET, `/api/v1`）
- `GET /api/v1/races?date&venue&page&page_size` → `Page<RaceSummary>`（一覧・ページング）。
- `GET /api/v1/races/{race_id}` → `RaceDetail`（404/422 あり）。
- `GET /api/v1/races/{race_id}/predictions[?bet_type&top]` → `PredictionResponse`（run=null で空、409/422 あり）。
- `GET /api/v1/races/{race_id}/odds[?bet_type&top]` → `OddsResponse`（win/estimated/real_exotic 別フィールド）。
- `GET /api/v1/races/{race_id}/recommendations` → `RecommendationResponse`（exotic 永続のみ）。

## 型生成 / ドリフト検知
- `front/openapi.json`: 014 `/openapi.json` のコミット済みスナップショット（契約の固定点）。
- `src/api/schema.d.ts`: `openapi-typescript front/openapi.json` 生成型（コミット）。
- `scripts/gen-types.sh`: 起動中 API（`$VITE_API_BASE` 既定 `http://localhost:8000`）の `/openapi.json` を取得 → `front/openapi.json`
  更新 → 型再生成。**手動実行**（API 起動が要るため）。
- **ドリフト検知テスト**: `front/openapi.json` から生成した型が `src/api/schema.d.ts` と一致することを vitest で検査。差分=失敗。

## クライアント / proxy
- `src/api/client.ts`: `openapi-fetch` クライアント、baseUrl は相対 `''`（パスは `/api/v1/...`）。
- `vite.config.ts`: `server.proxy = { '/api': { target: VITE_API_BASE or 'http://localhost:8000', changeOrigin: true } }`。
- **API（014）は無改変**（CORS 追加しない）。本番は静的ビルド + リバースプロキシ/CORS（将来）。

## エラー/空
- 空: 200 typed-empty（run=null / 配列空）→ Empty 状態。
- エラー: `{status, code, detail}`（404/409/422）→ Error 状態（防御的パース）。
- null 数値: `number | null` を `formatNum` で安全表示。
