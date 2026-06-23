# Implementation Plan: 単勝 EV 推奨と疑似ROIバックテスト

**Branch**: `007-win-bet-recommendation` | **Date**: 2026-06-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-win-bet-recommendation/spec.md`

## Summary

新パッケージ `betting/`(`horseracing-betting`、db/features/eval/serving 依存)に、Feature 006 の予測
(win 確率)から単勝の期待値 `EV=win_prob×odds` を計算して買い目を選び `recommendations` に保存する推奨生成と、
確定オッズを使う**疑似ROIバックテスト**(回収率/的中率/見送り率/最大DD/最大連敗)を ROI 専用 baseline
(人気1番常時単勝・全頭均等)と同一条件で比較する評価ハーネスを実装する。スキーマ変更なし。

codex second opinion の最重要リスク=**closing-oracle backtest(確定オッズを EV 入力と払戻に二重利用)を疑似評価と
明示しないこと**を、全レポート/監査に「pseudo evaluation」を明示することで回避する。取消・除外は母集団から除外し
残存馬で再正規化(憲法 IV)、買い目選択は結果(着順)を参照しない(リーク境界)。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `horseracing-db` / `horseracing-features` / `horseracing-eval` /
`horseracing-serving`(パス依存)、numpy、pandas、SQLAlchemy 2.0

**Storage**: PostgreSQL 16(読: race_predictions / race_horses.odds / race_results、書: recommendations)。
バックテストはレポートを返す(大量の recommendations は永続化しない)。

**Testing**: pytest + testcontainers。合成データで EV 選択(除外・再正規化・null odds)・疑似ROI 採点
(勝ち/負け/DNF/取消/同着)・baseline 比較・append-only/監査・決定論を検証。

**Target Platform**: Linux / macOS の手動 CLI 実行

**Project Type**: 単一の betting パッケージ(`horseracing-betting`)

**Performance Goals**: 期間(数百〜数千レース)のバックテストを秒〜分。予測は serving の純部品で in-memory 算出。

**Constraints**: 買い目選択は win_prob×odds のみ(結果非参照)。確定オッズ使用は**疑似評価**。取消・除外を除外して
再正規化。append-only。決定論(同一入力・同一 logic_version で同一)。

**Scale/Scope**: 単勝(win)のみ。ROI 専用 baseline 2 種。複勝・馬連・三連複(結合確率)・推定オッズは将来。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: 既存 `race_id`・`recommendations`(`bet_type='win'`)・`prediction_runs` を使う。新 ID なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: モデルは 005/006 で既にオッズ非参照。本フィーチャーの**買い目選択は
  win_prob×odds のみで、レース結果(着順)を一切参照しない**(結果は疑似ROI 採点のみ)。確定オッズの使用は
  betting 戦略の入力であり、モデル特徴ではない。closing-oracle 簡略化は疑似評価として明示(下記 V)。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 疑似ROIバックテスト harness を実装し、回収率/的中率/見送り率/最大DD/
  最大連敗を ROI baseline(人気1番/均等)と**同一条件**で比較。推奨ロジックの採否を評価で判断。**PASS(本原則の実装)**
- [x] **IV. 確率整合性**: 取消・除外を母集団・正規化から除外し、残存馬の win 確率を**再正規化**してから EV を計算。
  win_prob=0/odds<=0 を除外。**PASS**
- [x] **V. 再現性・監査**: `recommendations` に model_version(prediction_run 経由)・logic_version・
  market_odds_used・pseudo_odds・pseudo_roi・computed_at を保存。**確定オッズ使用を疑似評価として明示**、推定オッズは
  将来。オッズは最新値上書き方針。**PASS**
- [x] **VI. feature 分割規律**: UI なし。`recommendations` 契約は Feature 001 確定済み。スキーマ変更なし。結合確率
  (複勝/馬連/三連複)・推定オッズ変換は P0 として将来に明示分離。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion を取得・記録(下表)。**PASS**

### Second Opinion 記録(codex:codex-rescue — spec/plan 段階)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| リーク境界/オッズ二重利用 | **最重要**: 確定オッズを EV 入力兼払戻に使う closing-oracle を「ROI」と呼ぶと楽観バイアス | 全評価を**疑似評価**明示(FR-011/SC-006, R1) |
| 母集団/DNS・DNF | 取消・除外は race_results 無し→負けに数えず除外+再正規化。DNF は負け。同着 1 着は的中 | 採用(R3, FR-002/007) |
| baseline | 確率品質 baseline(003)は ROI 不可。ROI 専用 Favorite/Uniform を新設し同一スライス比較 | 採用(R4) |
| 成功基準 | 控除率下で ROI>0 は不適。baseline 超えを必要条件、回収率>1.0 は参考バー | 採用(SC-004 必須 / SC-007 参考) |
| 確率の扱い | win_prob は正規化済みだが、生成時除外が判明したら 0 にして再正規化 | 採用(R5, FR-013) |
| odds 可用性 | odds は取込済み(col43)。null/<=0 はスキップ、micro-fill しない | 採用(R6, FR-006) |
| recommendations 契約 | selection={horse_id,horse_number}, pseudo_odds=1/win_prob, pseudo_roi=EV-1, logic_version に式/閾値/除外方針 | 採用(R7, FR-005/010) |
| 全 EV 行 vs 1点 | 既存 operational は 1 レース最高 EV 1 頭。本 feature は EV>=閾値 全行保存と整合させる | 全 EV>=閾値 を保存(spec 明示)、operational と差異を記録 |

最重要リスク TOP3: ①疑似評価の不明示(楽観バイアス)②結果のリーク(選択に着順混入)③母集団/再正規化漏れ。
①は pseudo 明示、②は選択ロジックで結果非参照を検査、③は除外+再正規化テストで対応。

## Project Structure

### Documentation (this feature)

```text
specs/007-win-bet-recommendation/
├── plan.md
├── research.md          # 疑似評価・母集団/再正規化・ROI 採点・baseline・成功基準・selection 契約
├── data-model.md        # EV/推奨生成・疑似ROI 指標・baseline・recommendations 書き込み
├── quickstart.md        # 推奨生成 → バックテスト → 監査・疑似評価確認手順
├── contracts/
│   ├── recommend.md     # EV 選択・generate_recommendations の契約
│   └── backtest.md      # 戦略 / ROI baseline / 疑似ROI 指標・比較の契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
betting/                                   # 新パッケージ horseracing-betting
├── pyproject.toml                         # db/features/eval/serving (path) + numpy + pandas
├── src/horseracing_betting/
│   ├── __init__.py
│   ├── ev.py                              # 母集団除外→再正規化→EV=win_prob×odds→EV>=閾値 選択 (純)
│   ├── strategies.py                      # EVStrategy / FavoriteROIBaseline / UniformROIBaseline
│   ├── roi.py                             # 疑似ROI 採点 (払戻/的中/回収率/見送り率/最大DD/最大連敗)
│   ├── recommend.py                       # generate_recommendations: race_predictions → recommendations (append-only)
│   ├── backtest.py                        # 期間バックテスト: 予測(serving 純部品)→戦略→採点→レポート比較
│   └── cli.py                             # recommend --race-id/--prediction-run, backtest --from/--to
└── tests/
    ├── unit/                              # EV 選択・除外/再正規化・疑似ROI 採点・baseline・決定論 (合成)
    └── integration/                       # 実 DB で推奨生成→保存→監査、バックテスト→baseline 比較
```

**Structure Decision**: betting は予測の「行動化(買い目)」と運用評価で責務が異なるため新パッケージ
`betting/` を作り、db/features/eval/serving に依存。予測は serving の純部品(`load_serving_model` /
`predict_race`)で in-memory 算出して大量の prediction_runs 永続化を避ける。推奨生成(US1)は既存 prediction_run の
race_predictions を読んで recommendations に保存。ROI 採点・指標は eval のパターンに倣う(別 baseline)。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
