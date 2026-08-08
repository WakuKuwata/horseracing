# Research: 買い目推奨の金額主役カード表示 (087)

Phase 0 — spec の前提を実コード・実スキーマで裏取りし、実装方式を確定する。

## D1: 必要データは既存 API 応答で全て賄えるか

**Decision**: 賄える。API 変更ゼロが成立する。

**Rationale**(実スキーマ確認 2026-08-07):

- `RecommendationRow`(`front/src/api/schema.d.ts`): `stake_fraction`(nullable)・`selection: number[]`(馬番)・`settled`・`hit`・`dead_heat`・`counterfactual_snapshot_*`・`pseudo_odds`・`pseudo_roi`・`is_estimated_odds`・`double_pseudo`・`market_odds_used`/`estimated_market_odds_used` — 円額換算・答え合わせ・根拠折りたたみに必要な全フィールドが存在
- `RecommendationResponse.win_policy_status` — 見送りカードの理由分岐に使用(現行 `WIN_POLICY_MESSAGE` の 3 値)
- `HorseEntry`(**レース詳細応答 `RaceDetail.horses`** — schema.d.ts:1168): `horse_number`・`horse_name`(nullable)・`frame`(nullable) — 馬名併記・枠色チップの照合元。**推奨応答自体に馬名はない**ため、同一画面の出馬表データとの馬番照合で解決する
- **【codex C1 訂正 2026-08-08】** 当初この節は予測応答(`usePredictions`)の horses を照合元としていたが誤り。`PredictionResponse.horses` は `HorsePrediction[]`(horse_number のみ・horse_name/frame なし)。正は `RaceDetailPage` の `raceQuery`(`useRace`)が返す `RaceDetail.horses: HorseEntry[]`(現行の出馬表テーブルも同じソースを使用)

**照合の受け渡し方式**: `RecommendationPanel` に optional prop `entries?: HorseEntry[]` を追加し、`RaceDetailPage` から **`raceQuery.data?.horses`** を渡す。prop 未提供・照合不能時は馬番のみ表示(FR-012)。パネル内で追加 query を呼ばない(テストの独立性維持)。

**Alternatives considered**: (a) パネル内で `usePredictions` を再呼び出し — react-query キャッシュで通信は増えないが、モデル選択 state をパネルに持ち込む結合が生じ却下。(b) API に馬名を追加 — 契約変更ゼロの制約に反し却下。

## D2: 円額換算の正確な仕様

**Decision**: `amount = Math.floor(stake_fraction * budget / 100) * 100`(円)。`amount < 100` は「少額のため見送り」表示でサマリ非算入。`stake_fraction == null` は換算せず「—(参考表示)」。

**Rationale**: JRA 投票の最小単位 100 円。切り捨ては「保存された比率を超える金額を提示しない」方向の安全側で、按分・切り上げのような表示層の独自判断(FR-043 禁止)を含まない。

**【codex D2 改訂 2026-08-08】** 当初の「epsilon 補正なしの素朴 floor」は撤回。`0.036 × 25000 = 899.9999999999999`(Node 実測)のように浮動小数点誤差が **1 単位(100 円)丸ごと**の損失になる実在ケースがある(¥900 が ¥800 になる)。是正: `raw = f×b` に対し `|raw − round(raw/100)×100| < max(1,|raw|) × Number.EPSILON × 64` のとき最近傍 100 円に吸着してから floor(境界スナップ)。正当な 899.9 円(diff=0.1)は吸着されず floor される。テストは precomputed 値でなく**実演算オペランド**(0.036×25000・0.35×22000)を使う。

**合計が予算を超えるケース**: Σ`stake_fraction` はレース・券種横断で 1 を超えうる(016 の cap は bet 単位)。縮小・按分は金額の捏造にあたるため行わず、サマリに中立文言「合計が予算を超えています」を表示(spec Edge Case)。

**Alternatives considered**: 四捨五入(比率超過方向に丸まるケースがあり却下)・予算内への按分正規化(表示層が Kelly の相対比を破壊し FR-043 違反で却下)。

## D3: 円額に pseudo バッジを付けるか

**Decision**(**codex CHANGE 採用で当初判断を反転 2026-08-08**): **`double_pseudo=true` の行は主表示の円額(「少額のため見送り」含む)を `<PseudoValue kind="double_pseudo">` で包む**(展開前から常時可視)。実オッズ由来の行の円額は素のまま。根拠折りたたみ内の疑似 ROI・疑似オッズ・推定オッズ・Kelly 比率は現行と同一の `PseudoValue` 経路・kind を維持する。

**Rationale**(codex 指摘): 現行実装が `stake_fraction` を常にバッジ付きで表示しているのは**由来(provenance)が重要だから**であり、D3 当初案はその由来情報を「行動を決める主表示値」からだけ剥がすことになる。推定オッズ由来の Kelly から導いた金額は推定オッズに実質依存しており、折りたたみ内のバッジと一般開示では行単位の由来が伝わらない=FR-040/SC-003 の事実上の回避。憲法 V「推定オッズを用いた評価は疑似と明示」とも整合するのは全行バッジでなく **double_pseudo 行のみバッジ**(実オッズ行の円額は利用者自身の予算との算術で、誤読リスクの主因が無い)。テストは展開前の `toBeVisible()` で常時可視を検証する。

## D4: 強弱 3 段階(厚め/標準/抑え)の定義

**Decision**: 表示中レースの `stake_fraction` 非 null 行の最大値(**レース全体で単一計算・券種グループ別の再計算禁止**=codex H7)を基準に、`ratio = f / f_max` で 3 段階。**【codex D4 改訂 2026-08-08】境界比較は ε 許容**: `ratio > 2/3 + 1e-9 → 厚め`・`ratio > 1/3 + 1e-9 → 標準`・以外 → 抑え(素朴比較だと Node 実測で `0.1/0.3 = 0.33333333333333337 > 1/3`・`0.2/0.3 = 0.6666666666666667 > 2/3` となり境界ちょうどの意図した下側分類が壊れる)。バー幅は `clamp(ratio×100, 0, 100)%`。閾値は固定定数とし、結果・成績を見て調整しない(憲法 III の精神)。**縮退ケース**: `f == null`・負値・非有限 → 強弱なし。`f_max <= 0` → 全行なし。`f == 0` かつ `f_max > 0` → 抑え。**全行同値(単一行含む)→ 全行「厚め」は仕様として明示的に受け入れテストする**。

**Rationale**: レース内相対値なのは、絶対値の解釈(0.5% が厚いか)が予算・レース構成に依存し一般化できないため。3 等分は最も説明が単純で、事前固定できる。

**Alternatives considered**: 絶対閾値(レース間で意味が揺れる)・分位点(行数が少ないレースで不安定)— いずれも却下。

## D5: 枠色チップの実装

**Decision**: `HorseEntry.frame`(1..8)→ 固定 CSS クラス `frame-chip--1 .. frame-chip--8` の対応表(`lib/frameColors.ts`)。JRA 慣行色: 1=白(黒文字・境界線)・2=黒(白文字)・3=赤(白)・4=青(白)・5=黄(黒文字)・6=緑(白)・7=橙(白)・8=桃(黒文字)。`frame` null・照合不能は中立チップ(枠色なし・馬番のみ)。

**Rationale**: 枠番は出馬表応答に既に存在するため、頭数から枠を推算する必要がない(推算ロジックは 8 頭超の配分規則を持ち込む複雑さと誤りリスクがあり不要)。文字色は各背景に対する可読色を固定で持つ。

**Alternatives considered**: 馬番+頭数から枠を計算 — データがあるのに再導出するのは誤りの導入でしかなく却下。

## D6: 予算の保存と未設定フォールバック

**Decision**: 単一キー `horseracing.race_budget.v1` で localStorage に整数円を保存する hook `useBudget()`。全レース共通(spec Assumption)。**【codex D6/H3 改訂 2026-08-08】**: (a) hook-local `useState` フォールバックでは unmount→remount(タブ移動・レース遷移)で「セッション内保持」が破れるため、**フォールバックは module-level memory store** に持つ。(b) **所有権は `RecommendationPanel` が hook を 1 回だけ呼ぶ**構成に固定し、`BudgetInput` は controlled・`BetSlip` は同じ値を prop で受け取る(独立 hook 呼び出しは同一タブ内の即時同期が壊れるため禁止)。(c) 読み書き両方を try/catch。復元値は `validateBudget`(正整数・safe integer・100 以上)を通し、壊れた保存値は無視。(d) render/lazy initializer 内で書き込まず StrictMode の二重評価に安全。(e) **複数タブの live sync(`storage` イベント購読)は非要件と明記**(`useSyncExternalStore` はこの要件が入るまで不要)。未設定(null)時は予算入力の促し+カード UI 上の比率表示(Kelly 比率を `PseudoValue` 付きで表示)にフォールバック(FR-005)。**受理規則の正本は contracts §1**: 正の整数・100 未満拒否・100 の倍数の強制なし(UI の step=100 は入力ヒントであって validation ではない)。

**Rationale**: front 初の localStorage 利用(既存使用箇所なしを確認済み)だが、read-only 境界(サーバへ送らない)を破らない最小の永続化。バージョン付きキーで将来の形式変更に備える。

**Alternatives considered**: URL パラメータ(共有時に予算が漏れる・憲法外だがプライバシー配慮で却下)・サーバ保存(API 変更ゼロ制約に違反し却下)。

## D7: 状態切替(買い目 ⇔ 答え合わせ)の判定と実装

**Decision**: `hasSettled = items.some(r => r.settled)`。初期表示は `hasSettled ? "答え合わせ" : "買い目"`、`hasSettled` のときのみ切替トグルを表示(FR-020/021)。**【codex D7/H8 改訂 2026-08-08】** `useState(hasSettled ? …)` は非同期応答前(items=[])に "slip" で固定され settled 応答後も results に移らない・raceId 変更で前レースの手動 view が残る。是正: state は `viewOverride: "slip"|"results"|null` のみ持ち、実効 view を `viewOverride ?? (hasSettled ? "results" : "slip")` で毎 render 導出。raceId 変更時に override を reset(レースを離れると手動切替は持ち越さない)。答え合わせビューは現行の `HitCell`・反実仮想列・`WinBacktestSummary`・ベースライン表・オッズ帯別表を**内容不変で再配置**する(FR-022 — 計算ロジックの変更・削除をしない)。

**Rationale**: `settled` は現状 win 行にのみ立つため、単勝推奨のないレースでは切替が出ないが、そのとき答え合わせに出せる内容も存在しない(hit/回収は win-only)ので仕様として一貫(spec US3-4・Assumption)。レース跨ぎで view state を持ち越すと「確定レースを見た直後に未確定レースで答え合わせタブが残る」混乱を生むためローカル state とする。

**Alternatives considered**: レース結果 API の有無で判定(front の予測応答に結果フィールドがなく、新 API が必要になるため却下)・localStorage に view を保存(混乱の温床で却下)。

## D8: 現行テーブル前提テストの移行方針

**Decision**: `RecommendationPanel.test.tsx` を全面更新。検証の**意味**を維持しつつセレクタをカード UI に合わせる: (a) pseudo バッジ coverage(`assertPseudoLabelCoverage`)は根拠折りたたみを**展開した状態**で走らせる(折りたたみで DOM から消えるとテストが素通りするため、展開後の DOM を検査)、(b) 中立開示文の常時表示、(c) 利益語・損益色の不在。新規テスト: 円額換算純関数(境界値: ちょうど 100 円・99 円・null・0)・強弱バンド境界・枠色対応・状態切替(settled 有無)・見送りカード・サマリ合計一致(SC-007)・localStorage 例外時のフォールバック。

**Rationale**: 「テストを新 UI に合わせて弱める」事故(バッジ検証が折りたたみで空回りする等)が最大のリスクであり、展開後検査を明示的に規定する。

## D9: 変更しないものの機械的担保

**Decision**: (a) OpenAPI スナップショット(`front/openapi.json`)・生成型(`schema.d.ts`)に diff を出さない(既存 drift-check テストが担保)、(b) `front/src/api/` 配下は無変更、(c) API・DB・betting・serving パッケージは触らない。SC-005 の検証は `git diff --stat` で front/src の表示層ファイルのみであることを確認する。

## 参照した実コード(2026-08-07 時点)

- `front/src/components/RecommendationPanel.tsx` — 現行 8 列テーブル・`WIN_POLICY_MESSAGE`・`WinBacktestSummary`・`ODDS_BANDS`
- `front/src/components/PseudoValue.tsx` — 単一バッジ経路(`data-pseudo` 契約)
- `front/src/tests/pseudo.ts` — `assertPseudoLabelCoverage`
- `front/src/api/schema.d.ts` — `RecommendationRow` / `RecommendationResponse` / `HorseEntry`
- `front/src/pages/RaceDetailPage.tsx` — `usePredictions` で horses 取得済み・`tab === "recs"` でパネル描画
- localStorage 使用箇所: front 内ゼロ(本 feature が初)
