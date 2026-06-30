# Implementation Plan: 低履歴×血統適性 交互作用 + 種牡馬デビュー戦適性 (032)

**Branch**: `032-debut-pedigree-interaction` | **Date**: 2026-06-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/032-debut-pedigree-interaction/spec.md`

## Summary

§3 中コスト第2弾。市場が弱いデビュー/低履歴馬に効く血統シグナルを強化。新モジュール `debut_pedigree_features.py` が 5 列を生成: (a) **新情報 `sire_debut_win_rate`** = 同種牡馬の **他産駒のデビュー戦(各馬の初出走)勝率**(026 の `_other_offspring` 自馬除外機構を debut-runs サブセットに適用、strictly-before・同日除外)、(b) ゲーティング交互作用 4 列 = is_debut/is_low_history(history)× sire_win_rate/sire_dist_band_win_rate(026)の積。codex の「単純積は GBM 冗長」指摘を受け **新情報(a)を主役**、(b)は副次で bundle OOS が採否を決める(030 前例)。025 単一 as-of 源に結線、bit パリティ維持、新ソース列なし(sire_name は 026 で既にロード&fingerprint 包含)で source_fingerprint 無改修。FEATURE_VERSION 009→010。

## Technical Context

**Language/Version**: Python 3.12 (features package, uv)
**Primary Dependencies**: pandas, numpy。新規依存なし。
**Storage**: PostgreSQL 16(read-only、新規読取列なし)。parquet feature store(025)。
**Testing**: pytest(features/tests/unit)。correctness(デビュー戦集約・ゲーティング積・NaN・float64)・leak-guard・materialize parity。
**Project Type**: 単一 Python パッケージ拡張(features/)。スキーマ変更なし。
**Performance Goals**: 生成は 1 回(025 に相乗り)。debut-runs サブセットの cumsum 集計は 026 と同オーダー(O(n))。serving は単一レース fallback。
**Constraints**: bit パリティ非交渉(materialize==in-memory, float64)。リーク境界を新設しない(026 自馬除外機構を厳密踏襲)。
**Scale/Scope**: 約 62k races / 883k entries。新規列 5(debut_pedigree group 1 つ)。

## Constitution Check

Constitution v1.0.0 ゲート:

- [x] **I. データ契約**: raceId 12桁・2007年以降・ID は既存 loader 経由。sire_name は 026 の集計キー(名前)。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: sire_debut_win_rate は同種牡馬他産駒の strictly-before デビュー戦のみ(026 `_other_offspring` = sire 累積−自馬累積、同日除外)。ゲーティングは既存 as-of 列(is_debut/is_low_history/sire_*)の積のみ。今走 result/odds 非参照。利用可能タイミング=PRE_ENTRY(血統・出走歴は出馬表時点既知)、欠損=NULL(0埋め禁止)。leak-guard test(自馬今走・同日他産駒・未来 不変 + grep)。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: bundle 事前登録 walk-forward OOS(baseline=features-009 vs candidate=features-010)。primary=平均 win LogLoss 改善 かつ ECE 非悪化 + fold ガード(strict majority・worst-fold ECE 2e-3・worst-fold dLogLoss 5e-3)。ablation/セグメント診断は SECONDARY。**PASS**
- [x] **IV. 確率整合性**: 特徴追加のみ。win→joint(009) 不変。Unknown=NaN。**PASS**
- [x] **V. 再現性・監査**: parquet は DB から決定論再生成。FEATURE_VERSION 010。新ソース列なしで source_fingerprint 無改修。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更なし(migration head 0006 不変)。features 内拡張のみ。**PASS**
- [x] **品質ゲート**: codex second opinion 取得済(単純積=GBM 冗長→新情報の条件付き集約を主役に・dist hinge 等は 033 へ・採用確率を正直に低く見積もり)。差分と採用根拠を research.md に記録。**PASS**

**Gate 結果**: 全 PASS。違反なし。

## Project Structure

```text
specs/032-debut-pedigree-interaction/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/debut-pedigree-features.md
├── checklists/requirements.md（PASS 済）
└── tasks.md（/speckit-tasks 出力）

features/src/horseracing_features/
├── pedigree_features.py        # 026（既存・helper を 032 が再利用、必要なら _debut_runs を追加）
├── debut_pedigree_features.py  # 032（新規）— sire_debut_win_rate + ゲーティング交互作用
├── registry.py                 # 改修: debut_pedigree group + FEATURE_VERSION features-010
├── materialize.py              # 改修: build_asof_features に debut_pedigree ブロック結線
└── history_features / extra_features  # is_debut/is_low_history 供給（無改修）

features/tests/unit/
├── test_debut_pedigree_features.py   # 新規: デビュー戦集約・ゲーティング積・NaN・float64
├── test_debut_pedigree_leak.py       # 新規: 自馬今走/同日他産駒/未来 不変 + grep
├── test_materialize_core.py          # 改修: features-009→010、debut_pedigree in materialized_columns
└── test_feature023_leak_guard.py     # 改修: FEATURE_VERSION リテラル 009→010

training/src/horseracing_training/cli.py  # 改修: feature-eval 既定 --drop-groups を debut_pedigree に
```

**Structure Decision**: features 内の純追加。`debut_pedigree_features.py` は 026 の `_other_offspring`/`_normalize_name`/`_runs` を再利用(`build_debut_pedigree_features(frames, *, history=None, pedigree=None)` で materialize から既算出の history/pedigree を渡し二重計算回避、031 の `pace=` パターン同型)。リーク面・パリティ面のリスクを 026 の as-of 機構に閉じ込める。

## 実装アプローチ（要点）

1. **デビュー戦の特定**: 各 horse の最初の STARTED 出走を debut run とマーク(026 `_runs` に is_started があり、horse_id 単位で race_date 最小の started 行)。
2. **sire_debut_win_rate**: debut-runs サブセットに 026 `_other_offspring`(sire 累積−自馬累積、strictly-before・同日除外)を適用 → 他産駒デビュー戦の o_wins/o_cnt → `o_cnt>=min_starts ? o_wins/o_cnt : NaN`。
3. **ゲーティング交互作用**: history(is_debut/is_low_history)× pedigree(sire_win_rate/sire_dist_band_win_rate)を per-row 積。片側 NaN→NaN。
4. **NaN 規律**: 0 埋めなし。全列 float64 cast。
5. **結線**: materialize の build_asof_features に debut_pedigree ブロックを追加(history/pedigree を渡す)。registry に group 登録・FEATURE_VERSION 010。

詳細は [contracts/debut-pedigree-features.md](contracts/debut-pedigree-features.md) と [data-model.md](data-model.md)。

## Complexity Tracking

違反なし(全ゲート PASS)。記載不要。
