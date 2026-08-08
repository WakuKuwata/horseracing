# Implementation Plan: 買い目推奨の金額主役カード表示 (Betting Slip Redesign)

**Branch**: `087-betting-slip-redesign` | **Date**: 2026-08-07 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/087-betting-slip-redesign/spec.md`

## Summary

front の買い目推奨表示(`RecommendationPanel`)を、8 列テーブルから「金額主役」の馬券カード UI に置き換える。利用者がブラウザローカルに保持するレース予算 × 永続化済み `stake_fraction` を 100 円単位切り捨ての円額として主表示し、専門数値(Kelly 比率・疑似オッズ・疑似 ROI・オッズ出所)は「根拠を見る」折りたたみへ格下げする。レース状態(settled 行の有無)で買い目/答え合わせの前面を自動切替する。**変更は front パッケージのみ**: API・OpenAPI スナップショット・DB・推奨生成/精算ロジックはバイト不変。新しい計算は「表示専用の算術(掛け算と丸め)」だけで、サーバ側の値を一切変えない。

## Technical Context

**Language/Version**: TypeScript 5.x (front パッケージ既存構成)

**Primary Dependencies**: React 18 + Vite、@tanstack/react-query(既存)、新規依存なし

**Storage**: なし(レース予算のみ browser localStorage。サーバ送信しない。DB/migration なし)

**Testing**: Vitest + React Testing Library + MSW(front 既存スタック)。`assertPseudoLabelCoverage`(`front/src/tests/pseudo.ts`)による pseudo バッジ不変テストを維持

**Target Platform**: front SPA(read-only、014 API を Vite dev proxy / 018 nginx 経由で消費)

**Project Type**: Web front(表示層のみ・単一パッケージ変更)

**Performance Goals**: 予算変更時の再計算は純粋な同期算術(数十行 × 掛け算)で体感遅延なし。追加 API 呼び出しゼロ

**Constraints**: API/OpenAPI/DB バイト不変・read-only 境界不変・pseudo バッジ単一経路(`PseudoValue`)維持・利益示唆語/損益色/EV ソート禁止・064 中立開示常時表示

**Scale/Scope**: 変更ファイルは front のみ(`RecommendationPanel` 書き換え+新規コンポーネント 4〜5 個+lib 純関数 3 個+テスト)。既存 front テストは検証意味を等価維持して green(`RecommendationPanel` 系は全面更新のため件数は増減する — 弱体化の禁止は contracts §3・research D8 が規定)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: PASS(N/A 寄り)— `raceId` は既存 API 応答のキーとして透過利用のみ。ID 結合の新設なし。馬名・枠番の照合は同一レース応答内の `horse_number` キーで行い、ID 推測結合をしない
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS — 本 feature は表示層のみで特徴量・学習・予測に触れない。新たに生まれる表示派生値(予算・円額・強弱 3 段階)はブラウザ内で閉じ、サーバへ送信されず、モデル特徴・推奨生成に還流しない(構造的に保証: front は read-only で書き込み経路を持たない)
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS(N/A)— モデル・特徴量・推奨ロジック変更なし。walk-forward 評価対象が存在しない。強弱 3 段階の閾値は表示専用の固定値とし、結果を見た調整をしない(research D4)
- [x] **IV. 確率整合性**: PASS(N/A)— 確率値の計算・正規化に触れない。表示する金額は `floor(stake_fraction × 予算 / 100) × 100` の算術のみ(research D2)
- [x] **V. 再現性・監査**: PASS — 監査表示(prediction_run_id / logic_version / computed_at / 使用オッズ / 出所バッジ)は現行情報を維持(FR-022・折りたたみ内含む)。疑似・推定値の単一バッジ経路(`PseudoValue`)と「バッジ無し pseudo 表示ゼロ」不変テストを維持(FR-040)。円額のバッジ扱いは research D3 で確定
- [x] **VI. feature 分割規律**: PASS — UI 変更は確定済み API 契約(OpenAPI スナップショット)の範囲内でのみ行い、契約変更ゼロ。必要データ(stake_fraction / settled / win_policy_status / horse_name / frame)が既存応答に存在することを実データ・スキーマで確認済み(research D1)
- [x] **品質ゲート(codex second opinion)**: 実施中 — 提案段階(2026-08-07)で `codex:codex-rescue` を起動済み(設計方針・丸め・flat stake・情報アーキ・正直表示との親和性を諮問)。**結果は未着**。本 plan は暫定であり、codex 結果の到着後に差分(採用/不採用と理由)を本節下の「Codex second opinion」に追記してから implement に入る。到着前に implement を開始しない

### Codex second opinion(記録欄)

- **経緯**: 2026-08-07 提案段階の起動はセッション終了で消失 → 2026-08-08 に 2 レンズ(設計判断 D2-D7 / tasks・テスト戦略)で再起動。各レンズが主レビュー+独立検証パスの計 4 セッションを実行。全指摘を実コード・生成型・Node 実測で裏取り済み(`PredictionResponse.horses`=`HorsePrediction[]`・`RaceDetail.horses`=`HorseEntry[]`・`0.036*25000=899.9999…`・`0.1/0.3>1/3`・`pnpm check:openapi` 実在)。

**設計レビュー(全 D 判定 = CHANGE・全採用)**:

| 判断 | codex 判定 | 採否と反映 |
|---|---|---|
| D3 円額の pseudo バッジ | CHANGE: double_pseudo 行の主表示金額(「少額のため見送り」含む)は `PseudoValue kind="double_pseudo"` 必須・実オッズ行は素のまま。折りたたみ内バッジ+一般開示では行単位の由来が伝わらず、FR-040 の事実上の回避 | **採用**(spec Edge Case/FR-040・research D3・contracts §3 を改訂。展開前 `toBeVisible()` 検証も採用) |
| D2 丸め | CHANGE: 素朴 floor は `0.036×25000=899.99…→¥800`(1 単位丸ごと損失)。ULP 境界スナップ(±ε 内なら最近傍 100 に吸着)後に floor | **採用**(computeAmount に境界スナップ・実演算オペランドのテスト必須化) |
| D4 強弱境界 | CHANGE: `0.1/0.3>1/3`・`0.2/0.3>2/3` で境界誤分類。ε 許容比較・非有限/負値は非表示・バー幅 clamp・全行同値=全行「厚め」を明文化 | **採用**(strength.ts 仕様化・境界実演算テスト) |
| D6 予算 state | CHANGE: hook-local state では unmount/remount で「セッション内保持」が破れる。module-level store+単一所有(パネルが 1 回だけ hook)+復元値 validation。複数タブ live sync は非要件と明記 | **採用** |
| D7 view 切替 | CHANGE: `useState(hasSettled?…)` は非同期応答前に slip 固定・raceId 変更で前レース view 残留。`viewOverride ?? derived` 方式+raceId で reset | **採用** |

**見落とし指摘(設計レンズ)**: entries 注入元の誤り=**採用**(下記 C1)/モデルセレクタと推奨の run 不一致の明示=**採用**(中立注記)/pseudo helper が列挙値しか見ない=**採用**(fixture 全値列挙+kind 検証)/`QueryStateView` empty 短絡=**採用**/Phase 3 出荷ゲート矛盾=**採用**(results 抽出を先行)/確定後表示の「現在の予算による換算・購入履歴ではない」注記=**採用**/JPY 表記統一(ja-JP formatter)=**採用**/a11y=**部分採用**(details 基線+切替のボタン意味論+バー aria-hidden。全面的な a11y 契約化は本 feature 外)/中立開示文の「closing・in-sample」日本語化=**不採用**(064 出荷済み文言=FR-042 の維持対象。文言改訂は別 feature 候補として保留)

**tasks レビュー(C1 + H1-H8 + M1-M4 + L1 = 全採用)**: C1 entries 注入元(`predQuery`→**`raceQuery.data?.horses`**=`RaceDetail.horses: HorseEntry[]`。predQuery は `HorsePrediction[]` で horse_name/frame を持たない=research D1 の誤認を訂正)/H1 `<details>` は closed でも DOM に残り coverage が素通り→userEvent 展開+visibility+全値列挙/H2 現行 8 テストの意味等価リスト化/H3 budget 所有権・StrictMode・例外モック/H4 Phase 3 完了ゲート(results view green まで出荷可と扱わない・`RecommendationResults` 先行抽出)/H5 empty 短絡の除去と generated fallback 維持/H6 馬名・frame 独立縮退+全馬番照合/H7 fMax はレース全体で単一計算(券種別再計算禁止)/H8 view state 非同期初期化/M1 validation と算術の分離/M2 API 応答順 DOM テスト/M3 results 分離(`RecommendationResults.tsx`)/M4 T003/T004 の [P] 撤回・summarizeAmounts 純関数化/L1 `pnpm check:openapi` を回帰コマンドへ追加

**反映**: spec(D3 反転・FR-016/023 追加)・research(D1-D7 改訂)・data-model・contracts・tasks.md 全面改訂(2026-08-08)。**T002 消化済み — Phase 2 以降の実装ゲート解除**。

## Project Structure

### Documentation (this feature)

```text
specs/087-betting-slip-redesign/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── display.md       # 表示契約(入力フィールド・導出規則・不変条件)
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks — 作成済み・25 タスク)
```

### Source Code (repository root)

```text
front/src/
├── components/
│   ├── RecommendationPanel.tsx        # 書き換え: 状態切替の親 + カード/答え合わせの出し分け
│   ├── RecommendationPanel.test.tsx   # 全面更新(カード UI の等価テスト + 新シナリオ)
│   ├── BetSlip.tsx                    # 新規: 購入サマリ + 券種グループ + カード群 + 見送りカード
│   ├── BetSlipCard.tsx                # 新規: 買い目 1 点カード(枠色チップ・馬名・金額・強弱・根拠折りたたみ)
│   ├── BudgetInput.tsx                # 新規: 予算入力(未設定時の促し表示を含む)
│   └── PseudoValue.tsx                # 変更なし(単一バッジ経路として再利用)
├── lib/
│   ├── budget.ts                      # 新規: localStorage 予算 hook + 円額換算純関数(丸め・少額判定)
│   ├── frameColors.ts                 # 新規: 枠番→JRA 枠色クラスの固定対応表
│   └── strength.ts                    # 新規: stake_fraction 相対値→厚め/標準/抑え(固定閾値)
├── tests/
│   └── pseudo.ts                      # 変更なし(assertPseudoLabelCoverage を新テストでも使用)
└── styles.css                         # カード・チップ・バーのスタイル追加(実在パス)
```

**Structure Decision**: front 単一パッケージ内で完結。`RecommendationPanel` は「状態判定と出し分けの親」に痩せさせ、買い目カード群(`BetSlip`)と答え合わせ(現行 `WinBacktestSummary` 系の再配置)を子に分離する。円額換算・枠色・強弱は UI から独立した純関数(`lib/`)にして単体テストを直接当てる。API クライアント(`api/`)・OpenAPI スナップショット・型生成物は不変(drift-check が機械的に担保)。

## Complexity Tracking

違反なし(スキーマ変更ゼロ・新規依存ゼロ・単一パッケージ変更)。
