# Implementation Plan: 結合確率エンジン

**Branch**: `009-joint-probability-engine` | **Date**: 2026-06-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/009-joint-probability-engine/spec.md`

## Summary

新パッケージ `probability/`(`horseracing-probability`、db/eval 依存)に、各馬の単勝確率から Plackett-Luce で
JRA 全 7 券種の的中確率を導出する**結合確率エンジン**と、その校正を過去データで評価する harness を実装する。
エンジンは「取消・除外除去 → Σ=1 再正規化 → clip → PL 派生」の順を厳守し、整合性不変条件(Σ=1・無順序=順序和・
joint 周辺=`harville_topk`・包含∈[0,1]・単調・決定論)を機構保証する。**exotic オッズ/EV/推定オッズは対象外**。
スキーマ変更なし(計算ライブラリ + 評価、永続化は exotic オッズが入るまで保留)。

codex の確率レビュー(ワイド=trio の第3頭和、再正規化を分母計算より先、harville の分母 skip を継承しない、整合性
自己検査)を本 plan で機構解消する。

## Technical Context

**Language/Version**: Python 3.12（パッケージ/実行は `uv`、他パッケージと同様）

**Primary Dependencies**: `horseracing-db` / `horseracing-eval`(パス依存。`harville_topk` 再利用 + metrics で校正)、
numpy、SQLAlchemy 2.0(評価時に race_predictions/race_results を読む)

**Storage**: PostgreSQL 16(読: race_predictions / race_results、評価のみ)。MVP は exotic 確率を永続化しない。

**Testing**: pytest(+ 評価 harness は testcontainers)。**手計算 golden**(N=3/4・一様)+ 整合性不変条件 + 端点/
再正規化 + 決定論を合成データで検証。校正評価は過去/合成データで baseline 比較。

**Target Platform**: Linux / macOS の手動 CLI 実行・ライブラリ呼び出し

**Project Type**: 単一の確率ライブラリ(`horseracing-probability`)

**Performance Goals**: 三連単 O(N^3)、18 頭で約 4900 項=trivial。分母事前キャッシュで ワイド/三連複 を同一ループ集計。

**Constraints**: 再正規化を PL 分母計算より先。clip([eps,1-eps])。`harville_topk` の分母 skip を本計算に継承しない。
決定論。結果/オッズ非参照。N≤18。

**Scale/Scope**: 7 券種。Plackett-Luce 主モデル。独立積 baseline。exotic オッズ/EV/推定オッズ・同着確率モデルは将来。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: 既存 race_id/race_predictions/BetType(win/place/quinella/exacta/wide/trio/trifecta)を使う。
  新 ID なし。確率ラベルは日本語規約(券種名)。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 確率導出の入力は**単勝確率のみ**(006 で leak-safe)。レース結果・オッズを
  導出に使わない(評価採点のみ結果を使う、リーク境界)。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 結合確率の校正を過去データで評価する harness を実装し、独立積 baseline と
  同一レース集合・同一条件で NLL/Brier 比較。確率方式の採否を評価で判断。**PASS(本原則の実装)**
- [x] **IV. 確率整合性**: 取消・除外を母集団から除外し**残存馬を Σ=1 に再正規化してから**派生。clip で端点安定。
  Σ馬単=1・Σ三連単=1・無順序=順序和・joint 周辺=`harville_topk`・包含∈[0,1]・単調を機構保証。**PASS(核)**
- [x] **V. 再現性・監査**: 決定論(同一入力で同一出力)。MVP は永続化なし(計算ライブラリ)。**PASS(永続化は N/A)**
- [x] **VI. feature 分割規律**: スキーマ変更なし。exotic オッズ取得・推定オッズ変換・exotic EV/推奨は P0 として将来に
  明示分離。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion(確率方式・数値安定・整合性検査)を取得・記録(下表)。**PASS**

### Second Opinion 記録(codex:codex-rescue — spec/plan 段階)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| PL 式の正しさ | 馬単/三連単/馬連/三連複は正しい。**ワイドは ordered top-3 列挙の和**(独立積 `top3_i×top3_j` は禁止) | wide{i,j}=Σ_k trio{i,j,k}(trio の第3頭和)で導出(R1) |
| 周辺一致 | joint の周辺(top2/top3)が `harville_topk` と一致(非退化・分母 skip なし・N≥3) | place=harville top3/top2 を直接採用 + trifecta 周辺=harville top3 を**自己検査**(R1/R3) |
| 数値安定 | **再正規化を PL 分母計算より先**。`[eps,1-eps]` clip。harville の分母 skip を継承しない(質量欠損) | 除去→Σ=1 再正規化→clip→派生 の順を厳守(R2) |
| 整合性自己検査 | Σ馬単=1・Σ三連単=1・無順序=順序和・wide≥quinella・周辺=harville・包含∈[0,1]・単調 を必須 assert | 全て test 化(R3, SC-002) |
| 取消・除外 | 除去 → 残存 Σ=1 再正規化 → 派生(憲法 IV)。harville は正規化入力要求 | 採用(R2, FR-004) |
| 計算量 | O(N^3)、18 頭で trivial。分母キャッシュで wide/trio 同一ループ | 採用(R4) |
| 評価 | ordered-combination NLL/Brier。素朴独立積(∝p_i p_j 再正規化)baseline | 採用(R5, FR-009) |

最重要リスク TOP3: ①ワイドの独立積近似(整合性破壊)②再正規化順序の取り違え ③harville 分母 skip の継承。
①は trio 第3頭和、②は除去→再正規化→派生の固定順序、③は clip+再正規化で対応。

## Project Structure

### Documentation (this feature)

```text
specs/009-joint-probability-engine/
├── plan.md
├── research.md          # PL 式・ワイド/複勝・再正規化順序・数値安定・整合性自己検査・評価・計算量
├── data-model.md        # WinProbabilities・JointProbabilities・不変条件・評価レポート
├── quickstart.md        # 導出・整合性検査・校正評価・CLI 手順
├── contracts/
│   ├── engine.md        # joint_probabilities(win) の契約と不変条件
│   └── calibration.md   # 校正評価 / 独立積 baseline の契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
probability/                               # 新パッケージ horseracing-probability
├── pyproject.toml                         # db/eval (path) + numpy
├── src/horseracing_probability/
│   ├── __init__.py
│   ├── engine.py                          # 除外→Σ=1 再正規化→clip→PL 派生→JointProbabilities (純)
│   ├── consistency.py                     # 整合性不変条件の検査(Σ=1・無順序=順序和・周辺=harville・範囲・単調)
│   ├── calibration.py                     # 結合確率の校正評価 + 独立積 baseline(NLL/Brier、eval.metrics 再利用)
│   └── cli.py                             # show --prediction-run/--race-id: 券種別 上位 K 組み合わせ確率
└── tests/
    ├── unit/                              # golden(N=3/4)・整合性・端点/再正規化・決定論・複勝 N 依存
    └── integration/                       # 実 DB で prediction_run→確率→校正評価 baseline 比較
```

**Structure Decision**: 確率エンジンは予測の確率変換という独立の関心事で、将来の exotic 推奨が消費するため新パッケージ
`probability/` を作り、db/eval に依存。`harville_topk`(eval)を周辺と自己検査に再利用し、校正は eval の metrics を流用。
エンジン核は session 非依存の純関数(win 確率 → 結合確率)。評価のみ DB を読む。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
