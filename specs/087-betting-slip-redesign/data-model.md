# Data Model: 買い目推奨の金額主役カード表示 (087)

DB スキーマ・API 契約の変更は**ゼロ**。本書は front 内の表示ビューモデル(全て導出値・非永続、予算のみ localStorage)を定義する。

## 入力(既存 API 応答・変更なし)

| ソース | フィールド | 用途 |
|---|---|---|
| `RecommendationRow` | `bet_type`, `selection: number[]` | 券種グループ・対象馬番 |
| | `stake_fraction: number \| null` | 円額換算・強弱(null = 参考表示) |
| | `settled`, `hit`, `dead_heat` | 状態切替・答え合わせ |
| | `counterfactual_snapshot_gross/net_return` | 答え合わせ(現行のまま) |
| | `pseudo_odds`, `pseudo_roi`, `is_estimated_odds`, `double_pseudo`, `market_odds_used`, `estimated_market_odds_used` | 根拠折りたたみ(現行バッジ経路のまま) |
| | `recommendation_id`, `prediction_run_id`, `logic_version`, `computed_at` | React key・監査(表示は現行どおり別コンポーネント `RunAudit` 側 — カードには出さない) |
| `RecommendationResponse` | `win_policy_status` | 見送りカード・空状態文言 |
| `HorseEntry`(**レース詳細応答 `RaceDetail.horses`**、`raceQuery.data?.horses` を prop 経由 — codex C1 訂正: 予測応答の horses は `HorsePrediction[]` で horse_name/frame を持たない) | `horse_number`, `horse_name`, `frame` | 馬名併記・枠色チップ |

## ビューモデル(front 内導出・非永続)

### RaceBudget(唯一の永続 UI 状態)

- `budget: number | null` — 円。全レース共通の単一値。localStorage キー `horseracing.race_budget.v1`
- 制約: contracts §1 が正本 — 正の整数・100 未満拒否・100 の倍数の強制なし(UI の step=100 はヒント)。localStorage 不可時はメモリのみ(セッション内)
- サーバへ送信しない・モデル/推奨生成に還流しない

### BetCardVM(買い目 1 点 = カード 1 枚)

- `betType`, `selectionNumbers: number[]`
- `selectionDisplay: { number, frameClass, horseName? }[]` — `entries` との馬番照合。照合不能/`frame` null → 中立チップ・馬番のみ
- `amount: AmountVM` — 下記
- `strength: { label: "厚め"|"標準"|"抑え", ratio: number } | null` — `stake_fraction` null なら null
- `evidence` — 根拠折りたたみ: 使用オッズ(実/推定+バッジ)・疑似オッズ・疑似 ROI・Kelly 比率(いずれも現行 `PseudoValue` kind を維持)

### AmountVM(状態は 3 値・判定順)

1. `{ kind: "reference" }` — `stake_fraction == null` → 「—(参考表示)」。サマリ非算入
2. `{ kind: "too_small" }` — 換算額 < 100 → 「少額のため見送り」。サマリ非算入
3. `{ kind: "amount", yen: number }` — 100 の倍数の円額。サマリ算入

換算は境界スナップ付き floor(research D2 改訂・contracts §1 が正本)。**表示時、`double_pseudo=true` の行は主表示(amount/too_small とも)を `PseudoValue kind="double_pseudo"` で包む**(codex D3 採用)。実オッズ行は素のまま。

予算未設定(`budget == null`)時は AmountVM を作らず、パネル全体が比率フォールバック表示(FR-005)。

### SlipSummaryVM(購入サマリ)

- `totalYen = Σ amount.yen`(kind="amount" のみ)
- `count`(kind="amount" 行数)、`budgetPct = totalYen / budget`
- 不変条件(SC-007): `totalYen` は表示中カードの円額の総和と常に一致・全額 100 の倍数
- `overBudget = totalYen > budget` → 中立文言表示(縮小・按分はしない)

### SkipCardVM(見送りカード)

- `reason`: `win_policy_status` 由来の中立文言(no_run / not_generated / no_win_selected — 現行 `WIN_POLICY_MESSAGE` を踏襲)

### PanelStateVM(状態切替)

- `hasSettled = items.some(r => r.settled)`
- `viewOverride: "slip" | "results" | null`(state はこれのみ)— 実効 view = `viewOverride ?? (hasSettled ? "results" : "slip")` を毎 render 導出(codex D7/H8: 非同期応答前の固定・raceId 跨ぎの残留を防ぐ)。raceId 変更で override reset。トグルは `hasSettled` のときのみ表示。非永続
- `results` ビューの内容は現行実装(HitCell / 反実仮想列 / WinBacktestSummary / ベースライン / オッズ帯別)の再配置で、計算式・値は不変

## 状態遷移

```
予算未設定 ──(予算入力)──> 予算設定済み(全レースに適用・変更で即時再計算)
レース表示: hasSettled=false → view=slip(トグルなし)
            hasSettled=true  → view=results ⇄ slip(トグル)
```

## 導出規則(正本)

- 円額: `floor(stake_fraction × budget / 100) × 100`(epsilon 補正なし・常に切り捨て)
- 強弱: `ratio = f / max(f_i)`、`>2/3 厚め / >1/3 標準 / それ以外 抑え`(固定閾値・結果を見て変更しない)。`f_max <= 0` は全行 null・`f=0` は抑え(contracts §4)
- 枠色: `frame`(1..8)→ 固定クラス対応(1白/2黒/3赤/4青/5黄/6緑/7橙/8桃)。null → 中立
