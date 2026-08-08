# Display Contract: 買い目推奨カード表示 (087)

API/OpenAPI/DB 契約は不変。本書は front 表示層の**振る舞い契約**を固定する(テストで機械検証する項目)。2026-08-08 codex レビュー採用分を反映(採否の正本は plan.md 記録欄)。

## 1. 円額換算(FR-002/003/004/043)

```
入力: stake_fraction (number | null), budget (number | null)
出力:
  budget == null            → パネル全体が比率フォールバック(円額を一切表示しない)
  stake_fraction == null    → "—" + 参考表示ラベル(金額を生成しない)
  換算額 < 100              → 「少額のため見送り」(行は表示・サマリ非算入)
  それ以外                   → 換算額(100 の倍数のみ)
```

- **換算(境界スナップ付き floor — codex D2)**: `raw = f × b`。`|raw − round(raw/100)×100| < max(1, |raw|) × Number.EPSILON × 64` のとき最近傍 100 円に吸着、そうでなければ `floor(raw/100)×100`。テストは実演算オペランド(`0.036×25000 → ¥900`・`0.35×22000 → ¥7,700`)を必須とする
- 表示層は上記丸め以外の金額加工(按分・縮小・切り上げ・均等割り)をしてはならない
- 合計 > 予算のときも金額は変えず、中立文言のみ追加する
- **予算の受理規則(正本)**: `validateBudget` — 正の safe integer の円額のみ受理・100 未満拒否・100 の倍数の強制はしない(550 円可)・小数/NaN/Infinity/空文字は保存しない。validation(hook/入力側)と算術(computeAmount)は分離し、computeAmount は validation 済み budget のみを入力契約とする(codex M1)
- **金額表記は `formatYen`(ja-JP)の単一経路**(¥表記・桁区切りの混在禁止)

## 2. サマリ整合(SC-007)

- `合計 = Σ(表示中の kind="amount" 円額)` が常に成立(テストで検証)
- 点数 = kind="amount" の行数。予算比 = `Math.round(合計 / 予算 × 100)`%(0% 表示可)
- 全行が見送り/参考のとき「合計 ¥0・0 点」+ 明示文言(空白禁止)
- サマリは予算設定時のみ表示(FR-006 — フォールバック時は出さない)

## 3. 誠実表示(FR-040/041/042 — 非交渉)

- 疑似・推定値(疑似オッズ / 疑似 ROI / 推定オッズ / Kelly 比率)は必ず `PseudoValue` 経由で描画する。Kelly 比率は現行どおり **常時** `pseudoRoiKind`(pseudo または double_pseudo)の kind でバッジ付与。根拠折りたたみ内でも同様
- **主表示金額のバッジ(codex D3 採用)**: `double_pseudo=true` の行は主表示の円額(「少額のため見送り」含む)を `<PseudoValue kind="double_pseudo">` で包み、**展開前から常時可視**(テストは `toBeVisible()`)。実オッズ行の円額は pseudo ノードにしない
- **pseudo coverage テストの実行規約(codex H1)**: native `<details>` は closed でも子要素が DOM に残るため、`assertPseudoLabelCoverage` の呼び出し前に各 summary を `userEvent.click` で展開し、「クリック前 open なし+内部 pseudo node が `not.toBeVisible()`」「クリック後 open+visible」を確認する。fixture 内の**全 pseudo 値を期待値に列挙**し、`data-pseudo-kind` を行単位で検証する(helper は列挙値しか見ないため)
- 禁止: 利益示唆語(妙味・edge・儲かる 等)・損益による色分け・期待値/乖離によるソート。既存の禁止語 regex(`/儲か|勝てる|稼げる/` 等)は維持・拡張(`妙味|edge`)
- 中立開示文(064 FR-007)は予算設定有無・view(slip/results)にかかわらず常時表示
- 確定後に買い目表示へ切り替えたとき「現在の予算による換算・購入履歴ではありません」を常時可視で表示(FR-023)
- 「推奨は採用モデルの予測 run に基づき、モデル切替の影響を受けない」中立注記を表示(FR-024)

## 4. カード表示(FR-010..015)

- グループ化: `bet_type` ごと。グループ順=API 応答での初出順・グループ内=応答順(ソート導入禁止・**DOM 順序テストで機械検証**=codex M2)
- 枠色チップ: `frame` 1..8 → 白/黒/赤/青/黄/緑/橙/桃(固定対応・文字色は可読固定)。`frame` null → 中立チップ
- 馬名: **`RaceDetail.horses: HorseEntry[]`**(prop 経由)と `horse_number` で照合。`selection` の**全馬番**を照合し、各馬で horse_name と frame を**独立に縮退**(codex H6): name null+frame あり=枠色維持・馬名なし/frame null+name あり=中立チップ・馬名維持/照合不能・prop なし=中立チップ・馬番のみ。クラッシュ・行非表示禁止
- 強弱: `ratio = f / f_max`。**f_max はレース全体(全券種・非 null 行)で単一計算**(券種グループ別の再計算禁止=codex H7)。境界は ε 許容 `> 2/3 + 1e-9 厚め / > 1/3 + 1e-9 標準 / 以外 抑え`(codex D4: `0.1/0.3`・`0.2/0.3` の浮動小数点誤分類を防ぐ)。`f` null/負値/非有限 → 非表示。`f_max <= 0` → 全行非表示。バー幅 clamp [0,100]%・バーは装飾(`aria-hidden`・ラベル文字が本体)。全行同値 → 全行「厚め」(明示仕様)

## 5. 状態切替(FR-020/021/022/023)

- `hasSettled = items.some(r => r.settled)`
- state は `viewOverride: "slip"|"results"|null` のみ。実効 view = `viewOverride ?? (hasSettled ? "results" : "slip")` を毎 render 導出(codex D7/H8: 非同期応答前の `useState` 固定・raceId 跨ぎの残留を禁止)。raceId 変更で override reset
- トグルは `hasSettled` のときのみ描画(「買い目を見る」⇔「答え合わせを見る」・ボタン意味論)
- results ビューの数値(的中/無効/同着・反実仮想回収・事後集計・ベースライン・オッズ帯別)は現行実装と同値(計算変更禁止)。実績ノード `[data-result]` は `[data-pseudo]` 配下に置かない(逆方向不変条件)
- **実装順序の契約(codex H4)**: 答え合わせの受け皿(`RecommendationResults`)を先に抽出してからテーブルを置き換える。results view と回帰テストが green になるまで出荷可能と扱わない

## 6. 見送り・空状態(FR-030/031)

- 成功応答の `items=[]` は `QueryStateView` の empty で短絡させず BetSlip に渡す(codex H5)。no_run/not_generated/no_win_selected → 対応する中立カード、generated/未知 status → 既存「この条件の推奨はありません」fallback。loading/error の扱いは維持
- win 行なし+exotic 行あり → 「単勝は見送り」カードと exotic カードを同時表示
- 推奨 0 件で空白を表示しない

## 7. 変更禁止面(SC-005)

- `front/openapi.json`・`front/src/api/**`(クライアント・生成型)・API/DB/betting/serving パッケージ: diff ゼロ
- 回帰コマンドは `pnpm test && pnpm exec tsc -b && pnpm exec eslint . && pnpm build && pnpm check:openapi`(codex L1: drift-check の明示実行)
- 予算・view 状態をサーバへ送信しない(read-only 境界)
