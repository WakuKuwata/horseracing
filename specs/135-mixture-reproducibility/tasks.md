# Tasks: 追加混合の再現性と直近確認

## Phase 1 — Setup

- [x] T001 3担当の独立意見を specs/135-mixture-reproducibility/research.md に統合する。
- [x] T002 上限・順序・seed・1/7・直近手順を specs/135-mixture-reproducibility/experiment.json に固定する。

## Phase 2 — Foundation

- [x] T003 時点/SHA/母集団/metadata-only境界を新規3CLIの入力検査と tests に定義する。
- [x] T004 DB read-only metadataで当時入力・既存参照を調べ specs/135-mixture-reproducibility/evidence/recent-input-inventory.json に記録する。

## Phase 3 — US1 本番計算の比較

- [x] T005 [US1] 等価性/未来校正拒否/native変換/学習禁止を scripts/tests/test_calibration_serving_recheck135.py に追加する。
- [x] T006 [US1] 保存Cと現行131純演算/材料変換比較を scripts/calibration_serving_recheck135.py に実装する。
- [x] T007 [US1] 追加booster0の実測を artifacts/135-mixture-reproducibility/calibration/ に保存し、独立確認する。

## Phase 4 — US2 追加混合の再現性

- [x] T008 [P] [US2] seed/recipe/OOF/保存parity/同λ/seed平均損失を scripts/tests/test_mixture_reproducibility.py に追加する。
- [x] T009 [US2] 固定6job・本体保存・採点を scripts/mixture_reproducibility.py に実装しprepare/smokeを検証する。
- [x] T010 [US2] Step1完了後、6job/48boosterを実行し artifacts/135-mixture-reproducibility/repro/ に保存する。
- [x] T011 [US2] seed42既存126/132parity、全保存roundtrip、全条件・seed損失平均を採点し独立再計算する。

## Phase 5 — US3 直近確認

- [x] T012 [P] [US3] 時点snapshotのTE元ID/必要列/日付/出走集合の復元規約を scripts/mixture_recent_check_135.py とtestsへ実装する。
- [x] T013 [US3] 直近候補・学習末日・校正・採点規則を specs/135-mixture-reproducibility/evidence/recent-lock.json に固定する。
- [x] T014 [US3] Step2後、8/29〜9/6入力を取得し、129基準と同期間候補1outer/8boosterで予測・採点する。
- [x] T015 [US3] 直近のcoverage/入力時点/参照状況/損失/CIを artifacts/135-mixture-reproducibility/recent/ に保存し独立確認する。

## Phase 6 — Completion

- [x] T016 新規tests・入力/source整合・結果再計算・全fit終了を specs/135-mixture-reproducibility/evidence/ に記録する。
- [x] T017 全結果と次の判断を specs/135-mixture-reproducibility/result-review.md にまとめる。

## Dependencies and Parallel Work

実測はT007→T010/T011→T014/T015。T008/T012のコードとmetadata-only調査は前段と並行可。新規ファイルを担当で分け、既存source・他featureへ書かない。max2学習workerを親が調整する。Step3の成績はT013およびStep2完了前に読まない。

## Implementation Strategy

保存予測比較を最初に完了し、再現可能な基準で6学習を回す。直近期間は候補を選び直さず最後に採点する。改善なしや時点再現不能も隠さず報告し、本番昇格を行わない。
