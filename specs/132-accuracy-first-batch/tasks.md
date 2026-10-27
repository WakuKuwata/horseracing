# Tasks: 精度改善の第一バッチ

**Input**: spec.md / plan.md / research.md / data-model.md / contracts/experiments.md
**Tests**: 時点・欠損・確率・来歴の必須検査を実施する。

## Phase 1: Setup

- [x] T001 3つの独立設計意見を specs/132-accuracy-first-batch/research.md に統合する。
- [x] T002 9/6上限と比較案を specs/132-accuracy-first-batch/experiment.json に固定する。

## Phase 2: Foundational

- [x] T003 cutoff/SHA/集合/指標のテストを scripts/tests/test_accuracy_batch_common.py に追加する。
- [x] T004 固定入力・混合再構成・共通指標を scripts/accuracy_batch_common.py に実装し旧NLLを再現する。

## Phase 3: User Story 1 — 脚質Unknown

独立検証: 全未知でcoverage0、全既知で一致、8列以外不変、固定2年の同条件比較。

- [x] T005 [P] [US1] 欠損窓・同日/未来不変・取消・全既知parityを scripts/tests/test_pace_style_unknown_features.py に追加する。
- [x] T006 [US1] 8列overlayを scripts/pace_style_unknown_features.py に実装する。
- [x] T007 [US1] 素材監査と全非変更列一致を scripts/accuracy_style_recheck.py で実測し artifacts/132-accuracy-first-batch/style/ に保存する。
- [x] T008 [US1] 候補matrixを全OOFへ渡すこと、cache来歴、cutoffを scripts/tests/test_accuracy_style_recheck.py で確認する。
- [x] T009 [US1] 固定2024/2026の比較（影響なしならその証拠）を scripts/accuracy_style_recheck.py で実行する。

## Phase 4: User Story 2 — 上位着順確率

独立検証: win不変、2020warmup、校正year<Y、started-all採点。

- [x] T010 [P] [US2] 校正境界/fallback/確率のテストを scripts/tests/test_accuracy_topk_recheck.py に追加する。
- [x] T011 [US2] A/B/C比較を scripts/accuracy_topk_recheck.py に実装する。
- [x] T012 [US2] 全情報/公表前の保存再採点を scripts/accuracy_topk_recheck.py で実行し artifacts/132-accuracy-first-batch/topk/ に保存する。

## Phase 5: User Story 3 — 列サンプル混合

独立検証: 125/126基準再現、年別係数、全4案full-only、追加fit0。

- [x] T013 [P] [US3] cache/係数移送/重み/不変性を scripts/tests/test_accuracy_colsample_recheck.py に追加する。
- [x] T014 [US3] 4固定案を scripts/accuracy_colsample_recheck.py に実装する。
- [x] T015 [US3] 全4案を scripts/accuracy_colsample_recheck.py で実測し artifacts/132-accuracy-first-batch/colsample/ に保存する。

## Phase 6: Polish

- [x] T016 独立再計算と新規テストを確認し specs/132-accuracy-first-batch/evidence/ に記録する。
- [x] T017 全結果・実期間・費用・次の候補を specs/132-accuracy-first-batch/result-review.md にまとめる。

## Dependencies and Parallel Work

T001/T002→T003/T004。US2とUS3の実測は共通再構成に依存。US1のoverlay/監査は共通採点と並行可。学習はメモリを確認して親が起動調整する。各agentは担当する新規scripts/testsだけを編集する。

## Implementation Strategy

まず監査でUnknown修正の影響を確定し、保存予測で低費用比較を完了する。style学習は構造smoke後に固定年を実行。結果の良否による年・混合比の追加なし。本番昇格なし。
