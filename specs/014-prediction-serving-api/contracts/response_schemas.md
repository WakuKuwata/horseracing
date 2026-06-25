# Contract: レスポンススキーマ（pydantic → OpenAPI）

front(015) が型生成して消費する契約。data-model.md §1–5 と整合。すべて read-only 射影。

## エラー / ページング
- `ErrorBody{status:int, code:str, detail:str}` — 全エラー応答本体。
- `Page[T]{items:list[T], page:int, page_size:int, total:int, has_next:bool}`。

## レース
- `RaceSummary{race_id, race_date, venue_code, race_number, race_class?, distance?, track_type?}`
- `HorseEntry{horse_number?, horse_id, entry_status, age?, sex?}`
- `RaceDetail = RaceSummary + horses:list[HorseEntry]`

## 予測
- `RunAudit{prediction_run_id, model_version, logic_version, computed_at}`
- `HorsePrediction{horse_number?, horse_id, win:float|null, top2:float|null, top3:float|null}`（ソース nullable）
- `JointEntry{selection:list[int], prob:float}` — selection は `db.canonical_selection`、`(-prob, selection_key)` 決定論順上位 K
- `PredictionResponse{race_id, run:RunAudit|null, horses:list[HorsePrediction], joint:list[JointEntry]|null, joint_bet_type?, joint_logic_version?}`
  - run=null/horses=[] は「予測無し」（型付き空）。joint は bet_type 指定時のみ。

## オッズ（実/推定 判別）
- `WinOddsRow{horse_number?, horse_id, odds:float|null, odds_source:"real", is_estimated:false, updated_at}`
- `EstimatedOddsRow{bet_type, selection:list[int], odds:float|null, odds_source:"estimated", is_estimated:true, pseudo:true, as_of}`
- `RealExoticOddsRow{bet_type, selection:list[int], odds:float|null, odds_source:"real", is_estimated:false, coverage_scope, updated_at}`
- `OddsResponse{race_id, win:list[WinOddsRow], estimated:list[EstimatedOddsRow], real_exotic:list[RealExoticOddsRow]}`
  - **全行に `odds_source`+`is_estimated`**（real=false/estimated=true）。estimated=現時点再計算(`as_of`)、real=DB 最新(`updated_at`)。
  - 実/推定を別フィールドで分離（混在禁止）。値はモデル特徴に還流しない（契約注記）。odds が nullable なソースは `float|null`。

## 推奨（永続 SELECT のみ・exotic 6 券種限定）
- `RecommendationRow{bet_type, selection:list[int], market_odds_used:float|null, estimated_market_odds_used:float|null, is_estimated_odds:bool, pseudo_odds:float|null, pseudo_roi:float|null, double_pseudo:bool, logic_version, computed_at, prediction_run_id}`
- `RecommendationResponse{race_id, items:list[RecommendationRow]}`
  - `double_pseudo = is_estimated_odds`。生成しない（書込禁止）。**exotic 6 券種に限定**（win 推奨は `selection` が dict のため本エンドポイントの
    `list[int]` 契約外＝除外。将来 win 用に別スキーマ/エンドポイント）。

## 不変
- Decimal は JSON number（float）に射影、None は省略 or null（型で明示）。
- selection は馬番（int）配列のまま（011/012 と同一正準形）。馬メタ（名前等）は将来別フィールド。
- 全スキーマ frozen/immutable（読み取り射影）。OpenAPI に網羅。
