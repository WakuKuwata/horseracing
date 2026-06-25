# Contract: API エンドポイント（read-only, /api/v1）

全エンドポイント GET・読み取り専用・`/api/v1/` 前置。pydantic スキーマから OpenAPI 自動生成。型付きエラー `{status, code, detail}`。

## GET /api/v1/health
- 200 `{status:"ok", schema_version, api_version:"v1"}`。DB 接続確認（軽量 SELECT 1）。

## GET /api/v1/races
- query: `date?`（YYYY-MM-DD）, `venue?`, `page=1`, `page_size=50`(最大 200)。
- 200 `Page[RaceSummary]`（安定順序 race_date DESC, venue_code, race_number、total/has_next）。
- 422 不正な date/venue/page 形式。

## GET /api/v1/races/{race_id}
- 200 `RaceDetail`（属性 + 出走馬 horse_number/horse_id/entry_status）。
- 404 race 無し、422 race_id 形式不正（^[0-9]{12}$）。

## GET /api/v1/races/{race_id}/predictions
- query: `bet_type?`（place/quinella/exacta/wide/trio/trifecta）, `top=20`。
- 200 `PredictionResponse`: 決定論選択 run（active→computed_at DESC→run_id）の per-horse win/top2/top3 + `run`(監査)。
  `bet_type` 指定時のみ canonical 母集団に 009 を適用した上位 K の `joint` + `joint_logic_version`。
- 200 + 空（run=null/horses=[]）: レースはあるが予測無し。
- 404 race 無し。409/422: 使用可能確率が無い joint 算出。

## GET /api/v1/races/{race_id}/odds
- query: `bet_type?`, `top=20`（estimated の大グリッド抑制）。
- 200 `OddsResponse`: `win`(real, updated_at) / `estimated`(010, is_estimated/pseudo) / `real_exotic`(012, coverage_scope/updated_at)
  を**別フィールド**で。実/推定混在なし。
- 200 + 空: オッズ無し（500 にしない）。404 race 無し。

## GET /api/v1/races/{race_id}/recommendations
- 200 `RecommendationResponse`: **永続 `recommendations` を SELECT のみ・exotic 6 券種限定**（win は selection が dict のため除外）。各行
  bet_type/selection/market_odds_used/estimated_market_odds_used/is_estimated_odds/pseudo_odds/pseudo_roi/`double_pseudo`/logic_version/
  computed_at/prediction_run_id。
- **生成（書込）をしない**（`generate_exotic_recommendations` 非呼出）。200 + 空: 推奨無し。404 race 無し。

## /docs, /openapi.json
- OpenAPI UI と JSON。全スキーマ・全エンドポイントを網羅（front 015 の契約）。

## 横断契約
- **書込なし**: 全ハンドラ SELECT のみ、commit しない。
- **監査/疑似ラベル**: 予測=run/model/logic/時刻、オッズ=source/estimated/coverage/updated_at、推奨=疑似/二重疑似。
- **エラー**: 404（無し）/422（形式・算出不能）/409（状態不整合）/500 は内部のみ（純粋ヘルパ例外は捕捉して変換）。
- **版**: `/api/v1/`。破壊的変更は v2。
