# Data Model: read-only 予測配信 API

スキーマ変更なし。既存 ORM を読み取り、pydantic レスポンス（= OpenAPI 契約）に射影する。以下は pydantic スキーマ・選択規則・
エラー/ページング・不変条件。永続エンティティではなく**応答モデル**。

## 1. 共通: エラー / ページング

| スキーマ | フィールド |
|---|---|
| `ErrorBody` | status:int, code:str, detail:str |
| `Page[T]` | items:list[T], page:int, page_size:int, total:int, has_next:bool |

- エラー: レース無し=404、不正形式=422、使用可能確率/オッズ無しの算出=409/422、サーバ内部のみ 500（純粋ヘルパ例外は捕捉して変換）。
- ページング: **安定全順序** `race_date DESC NULLS LAST, venue_code NULLS LAST, race_number NULLS LAST, race_id`（nullable 列 + race_id
  タイブレーク）。`total`/`has_next` は**フィルタ適用後**の COUNT で算出。`page_size` は最大上限（既定 200）。

## 2. RaceSummary / RaceDetail（US1）

| スキーマ | フィールド |
|---|---|
| `RaceSummary` | race_id, race_date, venue_code, race_number, race_class?, distance?, track_type? |
| `HorseEntry` | horse_number?, horse_id, entry_status, age?, sex? |
| `RaceDetail` | RaceSummary + horses:list[HorseEntry] |

- `GET /api/v1/races?date=&venue=&page=&page_size=` → `Page[RaceSummary]`。`GET /api/v1/races/{race_id}` → `RaceDetail`（404 if 無し）。

## 3. PredictionResponse（US2）

| スキーマ | フィールド |
|---|---|
| `HorsePrediction` | horse_number?, horse_id, **win:float\|null, top2:float\|null, top3:float\|null**（ソース nullable） |
| `RunAudit` | prediction_run_id, model_version, logic_version, computed_at |
| `JointEntry` | selection:list[int], prob, (bet_type) |
| `PredictionResponse` | race_id, run:RunAudit, horses:list[HorsePrediction], joint?:list[JointEntry], joint_logic_version?, joint_bet_type? |

- **run 選択（決定論）**: `PredictionRun` を `model_versions` に **JOIN**（PredictionRun に adoption_status 列は無い）→ `active` 優先 →
  `computed_at DESC` → `prediction_run_id DESC`。選んだ run を `run` に。
- **canonical 母集団**: 取消・除外を除外 + 残存再正規化（009/011 規律）。
- **結合確率**: `?bet_type=&top=K` 指定時のみ `joint`。**確率降順 `(-prob, selection_key)` の決定論順で上位 K**。無指定では返さない
  （大グリッド抑制）。`JointEntry.selection` は **`db.canonical_selection(bet_type, 馬番)`** で 011/012 と同一正準配列（009 の tuple/
  frozenset キー → 馬番配列）。`joint_logic_version` 付与。
- 予測無し=200 + `run=null`/`horses=[]`（型付き空）。使用可能確率無しの joint=409/422。

## 4. OddsResponse（US3）

| スキーマ | フィールド |
|---|---|
| `WinOddsRow` | horse_number?, horse_id, odds:float\|null, odds_source="real", **is_estimated=false**, updated_at |
| `EstimatedOddsRow` | bet_type, selection:list[int], odds:float\|null, odds_source="estimated", is_estimated=true, pseudo=true, **as_of** |
| `RealExoticOddsRow` | bet_type, selection:list[int], odds:float\|null, odds_source="real", **is_estimated=false**, coverage_scope, updated_at |
| `OddsResponse` | race_id, win:list[WinOddsRow], estimated:list[EstimatedOddsRow], real_exotic:list[RealExoticOddsRow] |

- **実/推定を別フィールド**（win/estimated/real_exotic）で返し混在させない。推定は `is_estimated`/`pseudo`、exotic は coverage/updated_at。
- 推定市場オッズは 010 `estimate_market_odds`（純粋）を **canonical 母集団の win オッズ + canonical field_size** に適用して算出（書込
  なし）。selection は `db.canonical_selection`、bet_type 指定で大グリッド抑制。
- **注記（front 誤認防止）**: `/odds` の estimated は「**現時点の再計算**」、`/recommendations` の `estimated_market_odds_used` は「**推奨時
  スナップショット**」で**別物**（同じ券種でも値が異なりうる）。両者を等値としない。
- **契約注記**: これらの値はモデル特徴に還流しない（II）。オッズ欠損=200 空。

## 5. RecommendationResponse（US4）— 永続 SELECT のみ

| スキーマ | フィールド |
|---|---|
| `RecommendationRow` | bet_type, selection:list[int], market_odds_used:float\|null, estimated_market_odds_used:float\|null, is_estimated_odds, pseudo_odds:float\|null, pseudo_roi:float\|null, double_pseudo, logic_version, computed_at, prediction_run_id |
| `RecommendationResponse` | race_id, items:list[RecommendationRow] |

- **永続 `recommendations` を SELECT のみ・exotic 6 券種限定**（win は selection が dict のため除外）。`double_pseudo` = `is_estimated_odds`。
- 推奨無し=200 + `items=[]`。

## 6. 不変条件まとめ

1. 全ハンドラ SELECT のみ・commit しない（書込禁止）。`api/` は betting 非依存（R1）。
2. prediction_run 選択は決定論（active→computed_at→run_id）、run_id を応答に（R2、V）。
3. 結合確率は bet_type+上位 K、canonical 母集団（R3、IV）。
4. 実/推定/疑似/二重疑似を判別スキーマ + ラベルで区別、混在禁止（R4、V）。
5. 応答値をモデル特徴に還流しない（R4、II）。
6. 欠損は 404/200空/409-422 の型付き、500 回避（R6）。
7. 安定順序ページング・`/api/v1` 版付け・OpenAPI 自動生成（R7、VI）。
8. per-request 読み取り専用セッション（rollback/close）（R8、V）。
