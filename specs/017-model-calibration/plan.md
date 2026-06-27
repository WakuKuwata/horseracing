# Implementation Plan: モデル確率校正と edge haircut による Kelly 過大賭け抑制

**Branch**: `017-model-calibration` | **Date**: 2026-06-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/017-model-calibration/spec.md`

## Summary

`probability/` を拡張してモデル win 確率 p の校正器（power/temperature `p'∝p^γ`、γ を normalized
winner-NLL の golden-section MLE で walk-forward 学習）を実装し、`betting/`(016) に **edge haircut** と
**校正適用 Kelly / 比較ハーネス**を追加する。校正済み p' は 011 canonical field 上で再正規化し 009 結合確率
エンジンに通して P_model' を得る。013 の市場 q 校正と同型の機構を **モデル p 側**に転用（p≠q 厳守、p を
market odds 推定に戻さない）。採用ゲートは生 p vs p' の NLL/Brier(主)+ECE/reliability(補助)、**必須ガード
として 009 後 joint reliability 非悪化 + Kelly リスク非悪化**。スキーマ変更なし（校正情報は logic_version）。

codex の top-3（①joint 非保証→009 後 reliability を採用条件に、②選択も fold 窓内+小データ fallback、
③校正/haircut 役割分離+2×2 で二重補正検出）を本 plan で機構解消する。

## Technical Context

**Language/Version**: Python 3.12（uv）

**Primary Dependencies**: `horseracing-probability`(009 engine + 013 fl_bias/_golden_min/_engine_normalize
転用) / `horseracing-db` / `horseracing-betting`(016 Kelly)。numpy、SQLAlchemy 2.0。新規 ML 依存なし
（temperature/power は自前 MLE、beta/isotonic は scikit-learn 既存依存があれば利用、無ければ自前）。

**Storage**: PostgreSQL 16。読: race_predictions / race_results / race_horses / exotic_odds。
書: recommendations（016 と同一、**スキーマ変更なし**、logic_version に校正情報追記）。eval/比較はレポート返却。

**Testing**: pytest + testcontainers。合成データで 校正 fit/apply・walk-forward 選択リーク・joint
reliability・haircut・2×2・決定論・リーク・小データ fallback。実 DB スモーク。

**Target Platform**: Linux / macOS 手動 CLI

**Project Type**: probability 拡張（model_calibration）+ betting 拡張（haircut / 校正適用 Kelly / 比較）

**Performance Goals**: 校正 fit は 1-D MLE（golden-section、O(レース数)）。009 伝播は既存 O(N^3)。比較は
期間レース数 × mode 数。

**Constraints**: 確率は P_model（009 on p）、p' は p 系統のみ（p≠q、market 側に戻さない）。校正器は結果
非参照・walk-forward・選択も窓内。p'/haircut/edge_adj/fraction はモデル特徴に戻さない。決定論。

**Scale/Scope**: marginal p 校正（power MVP）+ haircut + 比較。joint 直接校正・オンライン/条件別校正・
不確実性連動 Kelly は deferred。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: 既存 race_id / race_predictions / recommendations を使用。新 ID なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 校正器は train-only/walk-forward（race_before 厳密前）、**方式・
  ハイパラ選択も fold 窓内**。校正器は対象レース結果を読まない。p'/haircut/edge_adj/Kelly fraction は
  features/training に出現させない（leak-guard test）。p≠q（p 校正を market odds 推定に戻さない）。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 校正評価ハーネスを先に用意。採用ゲート = NLL/Brier(主)+
  ECE/reliability(補助) + 009 後 joint 非悪化 + Kelly リスク非悪化（必須ガード）。baseline=生 p。**PASS**
- [x] **IV. 確率整合性**: p' は canonical field でレース内再正規化 + engine-consistent clip（Σ=1、009 入力一致）。
  取消・除外は除外して再正規化。**PASS**
- [x] **V. 再現性・監査**: logic_version に 校正方式・γ・校正窓・選択方式・haircut・base model_version を記録。
  stake=fraction×bankroll（016）に校正情報を追加。スキーマ変更なし。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更なし。opt-in 統合で後方互換。joint 直接校正・条件別校正・
  不確実性連動 Kelly を将来に明示分離。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` second opinion を取得・記録（下表）。035/036 校正ミス前例を踏まえ
  選択リーク・joint 非保証を機構解消。**PASS**

### Second Opinion 記録（codex:codex-rescue — 設計レビュー）

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **A. 校正手法** | overconfidence には temperature/beta、isotonic は小データで段差・ranking 破壊。ranking 保存必須 | power/temperature を MVP（ranking 保存）、beta 候補、isotonic は ranking 検査+最小サンプルで gated（R1） |
| **B. marginal→joint** | marginal 校正は PL 非線形で joint 校正を保証しない | 009 後の券種別 reliability 非悪化を採用条件に（R2/FR-005） |
| **C. 校正 vs haircut** | 二重で過剰保守。役割分離。edge−h は低 edge を一律に殺す | 役割分離（校正=系統誤差/haircut=残差）、relative 既定、独立 on/off、2×2 で過縮小検出（R4/R6） |
| **D. リーク境界** | 入力非リークでも方式/ハイパラ選択でリーク（035/036 前例）。小データ過学習 | 選択も fold 窓内、min_races/min_wins/per-band → temperature/identity fallback（R3/FR-003/FR-007） |
| **E. eval ゲート** | ECE はビン依存、Kelly と逆転しうる | NLL/Brier 主・ECE 補助・**joint/Kelly 非悪化を必須ガード**、overconfidence 指標追加（R5/FR-010/FR-012） |
| **F. 013 併用** | 両側校正が同じ結果を教師に二重吸収、edge 過縮小 | 2×2(p×q) 評価、順序 q→O_est→p、p を market 側に戻さない（R6/FR-013） |

最重要リスク TOP3: ①marginal 校正で joint 改善を誤前提 ②選択リーク/小データ過学習 ③校正+haircut 過剰
保守・p×q 二重補正。①=009 後 reliability ゲート、②=窓内選択+fallback、③=役割分離+2×2+Kelly 非悪化ガード。

## Project Structure

### Documentation (this feature)

```text
specs/017-model-calibration/
├── plan.md          # 本ファイル
├── research.md      # R1-R7（校正手法/joint/リーク/haircut/eval/2×2/スキーマ）
├── data-model.md    # PCalibrator / レポート / KellyConfig 拡張（スキーマ変更なし）
├── contracts/       # calibrate_eval.md / calibrated_kelly.md（CLI）
├── quickstart.md    # end-to-end 検証
├── checklists/      # requirements.md（16/16 PASS）
└── tasks.md         # /speckit-tasks で生成
```

### Source Code (repository root)

```text
probability/
└── src/horseracing_probability/
    ├── model_calibration.py   # PCalibrator(power/temperature fit/apply) + p vs p' eval + joint reliability（R1/R2/R3/R5）
    └── cli.py                 # calibrate-eval 追加

betting/
└── src/horseracing_betting/
    ├── kelly_types.py         # KellyConfig に haircut_type/haircut 追加（R4）
    ├── kelly_sizing.py        # single_kelly に edge haircut 適用（R4）
    ├── kelly_recommend.py     # p_calibrator opt-in（field p→p'）, logic_version 拡張（R7）
    ├── kelly_backtest.py      # p_calibrator opt-in（伝播）
    ├── calibration_eval.py    # 比較（raw/cal/cal+haircut）+ 2×2(p×q)（R6）
    └── cli.py                 # kelly-calibration-compare 追加 + kelly-recommend に校正/haircut フラグ
```

**Structure Decision**: probability に p 校正（013 と同居の calibration ファミリ）、betting に haircut +
Kelly 統合 + 比較。**スキーマ変更なし**（migration 追加しない）。新サービス・新フロントなし。

## Complexity Tracking

> Constitution 違反なし（スキーマ変更なし、既存原則の枠内）。記入不要。
