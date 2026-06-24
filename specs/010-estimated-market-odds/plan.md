# Implementation Plan: 推定市場オッズ変換

**Branch**: `010-estimated-market-odds` | **Date**: 2026-06-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/010-estimated-market-odds/spec.md`

## Summary

既存パッケージ `probability/`(`horseracing-probability`)を拡張し、単勝オッズから各券種の**推定市場オッズ**を
導出する変換規則を実装する。`odds_i → 市場含意 win 確率 q_i=(1/odds_i)/Σ(1/odds_j) → Feature 009 の結合確率
エンジン(PL)→ 市場含意 exotic 確率 → 券種別控除率 → 推定オッズ=(1−takeout)/P_market`。**モデル確率 p とは別物の
市場由来 q** を用い、推定オッズは「推定/疑似」として明示する(憲法 V)。評価先行として単勝オッズ復元と q 校正を検証。
**EV/推奨・永続化は対象外**(将来)。スキーマ変更なし。

codex の市場モデルレビュー(q=投票シェアで真の勝率/モデル p ではない、復元条件 R·S=1、PL 外挿の乖離=疑似明示、
p/q 分離、控除率の時点依存+設定可能、推定確率 0 近傍は派生オッズを cap)を本 plan で機構解消する。

## Technical Context

**Language/Version**: Python 3.12（パッケージ/実行は `uv`）

**Primary Dependencies**: `horseracing-db` / `horseracing-eval`(既存、`probability/` の依存)。`horseracing_probability.engine`
(009)を再利用。numpy、SQLAlchemy 2.0(検証時に race_horses.odds / race_results を読む)

**Storage**: PostgreSQL 16(読: race_horses.odds / race_results、検証のみ)。MVP は推定オッズを永続化しない。

**Testing**: pytest(+ 検証 harness は testcontainers)。人工オッズの**単勝復元 golden**(odds=R/s → q=s → 復元=odds)+
`q` 入力での 009 整合性 + 控除率適用 + 端点 cap + 決定論を合成データで検証。過去データで復元誤差・q 校正。

**Target Platform**: Linux / macOS の手動 CLI 実行・ライブラリ呼び出し

**Project Type**: 既存 `probability/` パッケージへのモジュール追加(market_odds / market_calibration / cli 拡張)

**Performance Goals**: 009 と同じ O(N^3)、N≤18 で trivial。

**Constraints**: 入力は市場オッズのみ(モデル p 非参照)。q は投票シェア(真の勝率でない)。推定確率 0 近傍は派生オッズを
cap(確率本体は cap しない)。控除率は設定可能 + logic_version。決定論。推定オッズは「推定」明示。

**Scale/Scope**: 7 券種の推定オッズ。控除率 JRA 既定(時点依存・設定可能)。exotic EV/推奨・永続化・実 exotic オッズ取得は将来。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: 既存 race_id / race_horses.odds / BetType / 控除率(日本語券種)を使う。新 ID なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 変換の入力は**市場オッズのみ**で、予測モデル確率 p を一切使わない。p と q を
  別オブジェクト/列で分離。オッズは予測モデルの特徴ではない(005/006 で担保、本フィーチャーは市場側)。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 単勝オッズ復元誤差 + 市場含意 q の校正(NLL/Brier)を過去データで評価する
  harness を実装。全出力を**疑似評価**として明示。**PASS(本原則の実装)**
- [x] **IV. 確率整合性**: オッズ欠損/0/負・取消・除外を q 母集団から除外して再正規化してから 009 に入力。009 の整合性
  (Σ=1・無順序=順序和・wide=Σ_k trio)を満たす。推定確率 0 近傍は**派生オッズを cap、確率本体は cap しない**。**PASS**
- [x] **V. 再現性・監査**: 決定論。推定オッズは**「推定(is_estimated_odds)」として明示**し実オッズと区別。控除率を
  logic_version に含める。MVP は永続化なし。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更なし。exotic EV/推奨・推定オッズ永続化・実 exotic オッズ取得は将来に明示分離。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion(市場変換式・p/q 分離・控除率・cap)を取得・記録(下表)。**PASS**

### Second Opinion 記録(codex:codex-rescue — spec/plan 段階)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| q の意味 | パリミュチュエルで `q_i=(1/odds_i)/Σ(1/odds)=投票シェア s_i`。**真の勝率/モデル p ではない**、favorite-longshot bias を含む | q を「市場含意=投票シェア」と命名・分離(R1, FR-001) |
| 単勝復元 | `hat_odds_i=R·S·odds_i`、復元条件は `R·S=1`(`S=Σ1/odds=1/R`) | 復元性を golden + 過去データで検証(R2, FR-006/SC-001) |
| PL 外挿の妥当性 | 標準近似だが exotic 実価格の定理ではない。実 exotic は独立プール。**推定/疑似明示が必須** | 推定オッズを「推定」明示、疑似評価(R3, FR-007) |
| p/q 分離 | 命名・保存先・メタ(is_estimated_odds)を分離。EV は将来 `p_b·est_odds_q,b−1` | p 非参照、q は別型/列(R4, FR-008/SC-005) |
| 控除率 | JRA 公式(平26.6.7〜): 単複20/馬連ワイド22.5/馬単三連複25/三連単27.5%。**時点依存→設定可能+logic_version**。複勝は粗い近似 | 既定 + 設定可能 + logic_version、複勝近似を明示(R5, FR-003) |
| 数値 | odds>0・S>0、欠損/0/負は除外、取消・除外で再正規化。`P→0` で `est→∞`→**派生オッズ cap、確率は cap しない** | 採用(R6, FR-004/005) |
| 評価 | 単勝復元はレース単位(`|log(R·S_r)|` 等、全馬同率誤差)。q 校正は NLL/Brier。実 exotic 無→結果校正のみ、ROI は疑似 | 採用(R7, FR-009) |

最重要リスク TOP3: ①q をモデル p/真の勝率と同一視 ②PL 外挿の推定を実 exotic 価格扱い ③控除率 hard-code/時点無視。
①は p/q 分離、②は推定/疑似明示、③は設定可能+logic_version で対応。

## Project Structure

### Documentation (this feature)

```text
specs/010-estimated-market-odds/
├── plan.md
├── research.md          # 市場含意 q・単勝復元・PL 外挿の疑似性・控除率・cap・評価
├── data-model.md        # WinOdds・q・EstimatedOdds・不変条件・検証レポート
├── quickstart.md        # 推定・復元検証・q 校正・CLI 手順
├── contracts/
│   ├── market_odds.md   # estimate_market_odds(odds) / 控除率の契約
│   └── validation.md    # 単勝復元 + q 校正 harness の契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
probability/                               # 既存 horseracing-probability を拡張
├── src/horseracing_probability/
│   ├── engine.py                          # (009 既存) joint_probabilities — q を入力に再利用
│   ├── market_odds.py                     # 追加: odds→q→(engine)→P_market→推定オッズ + 控除率/cap
│   ├── market_calibration.py              # 追加: 単勝復元誤差 + q 校正(NLL/Brier)、疑似明示
│   └── cli.py                             # 拡張: estimate-odds / validate-odds サブコマンド
└── tests/
    ├── unit/                              # 単勝復元 golden・q 整合性・控除率・端点 cap・決定論・p/q 分離
    └── integration/                       # 実 DB で odds→推定オッズ、復元誤差・q 校正
```

**Structure Decision**: 推定市場オッズ変換は 009 の結合確率エンジンを `q` 入力で再利用する「確率/オッズ」ドメインの
拡張であり、過剰分割を避けて既存 `probability/` にモジュール追加する(db/eval 依存は既存)。`market_odds`(変換)と
`market_calibration`(検証)を分離し、CLI に `estimate-odds`/`validate-odds` を足す。p(モデル)は一切参照しない。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
