# Contract: display — honest decision-support (api + front)

## api(read-only・betting 非 import)

### `api/backtest.py::favorite_realized(odds_map, finish_map, *, n_winners) -> WinRealized`(純関数・新規)
- レースの最低オッズ started 馬(本命)の realized(hit/return/roi)。既存 `win_realized` と同型の純述語。results は採点のみ(II)。

### API 応答(OpenAPI 純追加)
- **policy skip status(codex 指摘)**: `items=[]` だけでは「全馬 cap 超 / EV 未達 / 未生成 / run 無し」を区別できない。→ recommendations 応答に **read-time の policy status**(例 `win_policy_status: generated | skipped_all_over_cap | skipped_ev_unmet | not_generated | no_run`)を純追加し、front が空配列を正しい skip 理由で表示できるようにする。
- **本命ベタ基準**: recommendations 応答 or 小 endpoint に**表示中レースの本命 realized 集計**を read-time 付加(per-race favorite hit/return)。重い walk-forward を API で再計算しない(021/049 read-only 規律)。
- 追加は純追加(削除ゼロ)・全 path GET・betting 非 import・front `openapi.json` snapshot 更新 + drift-check 緑(VI)。
- no-bet(×1.0)・odds帯別は front 派生のため API 追加不要。

## front(`RecommendationPanel.tsx` / `WinBacktestSummary`)

- **回収<1 の正直提示**: 単勝過去実績サマリに `no-bet 基準(資金を減らさない基準)×1.00` と `本命ベタ基準(市場ベースライン)×0.78 前後` を**併置**。文言規律(codex): 「儲かる戦略」でなく「資金を減らさない基準/市場ベースライン」と表示。利益語・緑赤色・ランキング・単発レース勝敗強調は禁止(021)。retrospective・in-sample・closing 楽観・将来利益でない旨の固定注記を強化。
- **odds帯別 realized 回収**: 表示中 settled win 行を odds 帯(<3/3-6/6-11/11-21/21-51/51+)で集計し n・回収を中立表示(大穴帯の出血が見える)。
- **中立注記(常時)**: 「このモデルは市場に対する再現可能な優位を持たず、買い目は損失を抑える判断材料であって将来の利益を示すものではありません」。
- **skip 理由**: 推奨ゼロのレースで空欄でなく「見送り(全馬が上限オッズ超 / EV<1 / オッズ欠損)」を表示。
- **疑似/実績分離**: 既存 PseudoValue/ResultBadge 規律維持(疑似を実績と誤読させない不変テスト緑)。

## テスト

- front: no-bet/本命ベタ基準が併置され損益色/利益語/ソートがない(RTL)。
- front: odds帯別集計が表示中行と一致(MSW)。
- front: 推奨ゼロで skip 理由が出る・中立注記が常時ある。
- front: 「no pseudo value without a label」不変テスト緑・realized は data-pseudo を持たない。
- api: `favorite_realized` の hit/void/miss/dead-heat・OpenAPI drift-check 緑・read-only(全 path GET)不変・betting 非 import 境界維持。
