# Implementation Plan: win 的中/回収バックテスト表示 (049)

**Stack**: FastAPI (`api/`, read-only, deps=db+probability のみ) + React/Vite/TS (`front/`). スキーマ変更なし・migration なし。

## アーキテクチャ
read-only 表示。DB は既存 `recommendations`(win 行)と `race_results`(finish_order/result_status)を read するのみ。的中/回収は **api 内の純関数**で read 時計算し、レスポンスに nullable フィールドを追加。永続化しない。

## Touch points
1. **api 新純モジュール** `api/src/horseracing_api/backtest.py`(betting 非 import): `WinRealized` dataclass + `win_realized(selection, market_odds_used, *, finish_map, n_winners) -> WinRealized`。
   - settled=bool(finish_map)。win 選択馬 horse_id を finish_map で引く: 不在=void(hit=None)/finish_order==1&finished=的中(return=odds, roi=odds−1, dead_heat=n_winners>1)/他=不的中(return=0, roi=−1)。
2. **api/queries.py** `race_finish_map(session, race_id) -> (dict[horse_id,(finish_order,result_status)], n_winners)` — RaceResult を 1 回ロード、n_winners=finish_order==1&finished 数。
3. **api/schemas.py** `RecommendationRow` に nullable 追加: `settled: bool=False`, `hit: bool|None=None`, `dead_heat: bool=False`, `realized_return: float|None=None`, `realized_roi: float|None=None`。
4. **api/routers/recommendations.py**: レース毎に finish_map を 1 回取得、各 rec が bet_type=='win' なら win_realized を計算(生 `r.selection` dict の horse_id 使用)しフィールド設定。非 win は既定 null。
5. **OpenAPI 同期**: API 起動 → front/openapi.json 再生成(key-sort)→ schema.d.ts 再生成 → drift-check 緑。
6. **front RecommendationPanel.tsx**: win 行の settled 時に「結果」列グループ(的中/不的中/void バッジ + 実現回収 ×odds + realized_roi)を pseudo 列と**分離**表示。同着注記。realized_* は real=**PseudoValue を通さない**(実績マーカー `<ResultBadge>` で明示)。US2: 表示中の win 行から過去実績サマリ(n_settled/n_hit/hit_rate/mean roi/recovery_rate)を front で集計、retrospective ラベル・損益色なし・ソートなし。

## 憲法
- II: realized_* は表示専用・feature 非流入(read 時計算・非永続・feature_snapshots 不変)。既存 leak-guard で担保。
- III: 確率モデル変更でない=事前登録ゲート不要。021 表示規律(損益色/利益語/ソート/将来射影の禁止)を US2 に適用。
- V: pseudo(pseudo_odds/pseudo_roi)は PseudoBadge 維持・"no pseudo without badge" 不変テスト緑。realized(real)はバッジ無しだが実績ラベル下で pseudo と分離。
- VI: schema/migration なし。OpenAPI 契約先行 + drift-check。api は betting 非 import(境界テスト)。

## テスト
- api unit: win_realized の 的中/不的中/void/同着/DNF・非 win null(pure、DB 不要)。
- api integration: recommendations エンドポイントが settled レースで realized フィールドを返す・未 settled で null・全 path GET・betting 非 import。
- front: 結果列描画・pseudo-label 不変(realized real 値は data-pseudo 無し)・US2 サマリのラベル/非損益色。
- drift-check・openapi read-only test。
