# Implementation Plan: Kelly 賭け金最適化と bankroll backtest

**Branch**: `016-kelly-staking` | **Date**: 2026-06-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/016-kelly-staking/spec.md`

## Summary

既存 `betting/`(`horseracing-betting`) を拡張し、`probability/`(009/010) と 011/012 の canonical field /
to_selection / recommendations 規約に依存して、exotic 買い目の **Kelly 最適賭け金**を算出する。各買い目 c で
edge=P_model(c)·O(c)−1、f*=edge/(O−1)、実効 fraction=clip(λ·f*,0,cap_bet)。同一(race,bet_type)は**相互排他**
のため期待対数成長 G(f)=Σ P_model·log(1−S+O·f)+(1−ΣP_model)·log(1−S) を制約下で最大化（canonical 配分）し、
簡易 heuristic も併設して近似誤差を backtest で明示。確率は **P_model のみ**、オッズは実(012)優先・無ければ
推定(010、二重疑似)。Kelly fraction は recommendations の新 nullable 列 **`stake_fraction`**(migration 0006)に
保存し、設定は logic_version にエンコード。bankroll backtest は walk-forward 逐次更新 + block bootstrap で
破産確率を推定し、flat(011/012) と同一条件比較（success=リスク調整後成長で優位）。

codex の top-3 是正（①相互排他の同時 Kelly、②推定オッズ Kelly の安全抑制、③fraction/config の監査可能化）を
本 plan で機構解消する。

## Technical Context

**Language/Version**: Python 3.12（パッケージ/実行は `uv`）

**Primary Dependencies**: `horseracing-db` / `horseracing-probability`(009 joint_probabilities + 010
estimate_market_odds) / 011/012 の betting モジュール（canonical_field / to_selection / exotic 採点）。
numpy、SQLAlchemy 2.0、Alembic（0006）。最適化は numpy ベースの決定論的凸最適化（外部ソルバ依存を避ける）。

**Storage**: PostgreSQL 16。読: race_predictions / race_horses.odds / exotic_odds / race_results。
書: recommendations（**+ stake_fraction 列**）。backtest はレポートを返す（非永続）。

**Testing**: pytest + testcontainers。合成データで Kelly 式・相互排他配分(exact vs heuristic)・推定抑制・
破産確率(block bootstrap)・実/二重疑似分離・決定論・リーク・確率整合性を検証。実 DB スモーク。

**Target Platform**: Linux / macOS の手動 CLI 実行

**Project Type**: 既存 `betting/` へのモジュール追加（kelly_sizing / kelly_allocation / kelly_recommend /
bankroll_backtest / cli 拡張）+ db migration 0006

**Performance Goals**: 009/010 と同じ O(N^3) + 配分最適化は買い目数（≤K）に対し低次。backtest は期間レース数 ×
（推奨生成 + 採点）+ bootstrap B 経路。

**Constraints**: 確率は P_model のみ(p≠q)。買い目決定は結果非参照。推定オッズは二重疑似ラベル + 保守 λ。
stake_fraction/オッズ/q はモデル特徴に戻さない。Σstake_fraction ≤ cap_total。決定論。append-only。

**Scale/Scope**: exotic 6 券種 + 単勝。Kelly fraction + bankroll backtest。多券種同時最適化・券種間相関・
実資金運用・モデル過信補正は将来。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: 既存 race_id(12桁) / recommendations(BetType) / prediction_runs を使用。新 ID なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 確率は **P_model のみ**（009 on モデル p、odds/results 非参照）、O は
  市場由来だが**確率に q を使わない**。買い目決定は結果非参照（採点のみ結果使用）。stake_fraction/fraction/
  オッズ/q はモデル特徴・学習入力に出現させない（leak-guard test）。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: bankroll backtest harness を実装し flat と同一条件比較。運用指標
  （回収率/的中率/最大DD/最大連敗/見送り率）+ 対数成長率/破産確率/分散。success=リスク調整後成長で優位
  （ROI>1 単独不可）。実/二重疑似分離。**PASS（本原則の実装）**
- [x] **IV. 確率整合性**: P_model は 009 の canonical field（取消・除外を除外し再正規化）。同一 canonical 母集団で
  P_model と O を計算（011/012 踏襲）。**PASS**
- [x] **V. 再現性・監査**: recommendations に model_version(prediction_run)・logic_version(λ/cap/O_min/bankroll/
  配分方式/odds 源/009/010 版)・**stake_fraction**・pseudo_odds・pseudo_roi・computed_at。is_estimated_odds=
  二重疑似明示。stake=fraction×bankroll で再現。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更は **stake_fraction 列 1 本（0006）** に限定し本項で正当化（下記
  Complexity Tracking）。多券種同時 Kelly・実資金運用・過信補正は将来に明示分離。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion を取得・記録（下表）。top-3 是正を機構解消。**PASS**

### Second Opinion 記録（codex:codex-rescue — 設計レビュー）

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **A. 相互排他配分** | 個別 Kelly+合計正規化は相互排他(同一券種1点的中=他全損)と不整合・過大賭け。期待対数成長の同時最大化が正 | **canonical=G(f) 最大化**（concave・一意・決定論）。heuristic は近似誤差を backtest で計測する格下げ対象（R2/FR-004） |
| **B. 推定オッズ Kelly** | 分母 O−1 が小さく推定誤差に敏感。λ 縮小/cap/フィルタ/無効化を | λ_est<λ_real・min_edge_est・O_min・cap、enable_estimated で無効化可（R3/FR-006/US3） |
| **C. 破産確率** | 単純 bootstrap は順序・相関・regime を壊し楽観化。walk-forward 主 + block bootstrap | walk-forward 実経路 + block bootstrap（順序保持）、i.i.d. シャッフル禁止（R6/FR-014） |
| **D. p≠q** | 確率は P_model で整合だが過信は過大賭け。校正/shrink/edge haircut を | p≠q 厳守（R5）。過信補正は **deferred**（Assumptions） |
| **E. スキーマ/再現性** | stake 額だけでは fraction/config 復元困難。kelly_fraction 列 + run config 監査 | **stake_fraction 列追加(0006)** + logic_version に config。stake=fraction×bankroll（R7/FR-011/SC-009） |
| **F. 追加** | まず実オッズ単一券種に限定、推定/券種横断は deferred | 実オッズ US1=MVP、推定=US3、券種横断は deferred（spec Assumptions） |

最重要リスク TOP3: ①相互排他配分の過大賭け ②推定オッズ Kelly の誤差爆発 ③fraction/config 非再現。
①=期待対数成長最大化、②=保守 λ+多層フィルタ、③=stake_fraction 列+logic_version で対応。

## Project Structure

### Documentation (this feature)

```text
specs/016-kelly-staking/
├── plan.md          # 本ファイル
├── research.md      # R1-R7（Kelly 式 / 相互排他配分 / 推定抑制 / 破産確率 / p≠q / スキーマ）
├── data-model.md    # recommendations+stake_fraction(0006) / Kelly config / backtest レポート
├── contracts/       # kelly_recommend.md / kelly_backtest.md（CLI 契約）
├── quickstart.md    # end-to-end 検証
├── checklists/      # requirements.md（16/16 PASS）
└── tasks.md         # /speckit-tasks で生成
```

### Source Code (repository root)

```text
betting/
├── src/horseracing_betting/
│   ├── kelly_sizing.py        # 単一買い目 f*=(P_model·O−1)/(O−1)、λ·cap、O_min（R1/R3）
│   ├── kelly_allocation.py    # 同一券種 期待対数成長最大化(exact) + heuristic（R2）
│   ├── kelly_recommend.py     # canonical field → P_model/O → 配分 → recommendations 保存（contracts）
│   ├── bankroll_backtest.py   # walk-forward 逐次更新 + block bootstrap 破産確率（R6）
│   └── cli.py                 # kelly-recommend / kelly-backtest 追加
└── tests/                     # Kelly 式/配分/推定抑制/破産/分離/決定論/リーク/整合性

db/
└── migrations/versions/0006_recommendations_stake_fraction.py   # nullable stake_fraction 追加
```

**Structure Decision**: 既存 `betting/` パッケージへのモジュール追加（011/012 と同じ構成）。スキーマは
db migration 0006 で nullable 列 1 本のみ追加。新サービス・新フロントは無し。

## Complexity Tracking

> Constitution VI（スキーマ変更最小）に対する逸脱の正当化

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| `recommendations.stake_fraction` 列追加（migration 0006） | Kelly の中核出力は per-row の賭け金 fraction。recommendations に stake/fraction 列が存在せず（011 flat は per-unit 暗黙・未保存）、保存先が無い。憲法 V（監査・再現）には fraction の永続化が必須 | **完全無改変**（既存 nullable 列に fraction を詰める）は pseudo_odds=1/P_model・pseudo_roi=EV−1 と意味衝突し監査性を損なう。**新テーブル**は recommendations と二重管理・読取契約分裂。nullable 列 1 本が最小・後方互換（011/012 行は NULL 維持）。012 の新テーブル(0005)と同様 VI 下で正当化 |
