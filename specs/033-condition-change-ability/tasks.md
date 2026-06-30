---
description: "Task list — 条件替わり×能力/時計 交互作用 (033)"
---

# Tasks: 条件替わり×能力/時計 交互作用 (Condition-change × Ability/Time)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [contracts/condition-change-features.md](contracts/condition-change-features.md)

**Organization**: MVP = US1(base 新情報) + US2(hinge×能力) + US3(リーク)。

## Phase 1: Setup
- [X] T001 前提確認: main(features-010/lgbm-032)・027 ブランチ helper(_runs/_prev_started/_surface/_GOING_ORD)・023 build_pace_features(rel_last3f_best/rel_time_avg)・025・head 0006。going カバレッジ確認
- [X] T002 [P] [contracts/condition-change-features.md](contracts/condition-change-features.md) の列契約(7列)・集計契約・NaN 規律・採用プロトコル・不変条件を確定

## Phase 2: Foundational
- [X] T003 `features/src/horseracing_features/condition_change_features.py`(新): 027 の `_surface`/`_GOING_ORD`/`_runs`/`_prev_started` を移植、`CONDITION_CHANGE_COLUMNS`(7) 定義、`build_condition_change_features(frames, *, pace=None)` 骨格。生今走 result/odds 非参照
- [X] T004 `registry.py`: 7 列を pedigree でなく source=races/derived・PRE_ENTRY・NULL で登録、group=`condition_change`、`FEATURE_VERSION="features-011"`。**波及**: `test_materialize_core.py`/`test_feature023_leak_guard.py` の 010→011

## Phase 3: US1 - 条件替わり base (P1, MVP)
- [X] T005 [US1] `condition_change_features.py`: dist_change/surface_switch/going_change を `_prev_started`(merge_asof allow_exact_matches=False)で算出。前走無し→NaN。float64
- [X] T006 [P] [US1] `features/tests/unit/test_condition_change_features.py`(新): INV-C1/C2(dist_change/hinge)・INV-C3(surface_switch/going_change)・INV-C5(デビュー→NaN)

## Phase 4: US2 - hinge × 能力 (P1, MVP)
- [X] T007 [US2] `condition_change_features.py`: dist_extension/dist_shortening(hinge)+ build_pace_features の rel_last3f_best/rel_time_avg を merge し dist_ext_x_closing=dist_extension×(−rel_last3f_best)・dist_short_x_speed=dist_shortening×(−rel_time_avg)。片側 NaN→NaN。最終 astype float64
- [X] T008 [P] [US2] `test_condition_change_features.py`(追記): INV-C4(能力交互作用)・INV-C6(能力 NaN→NaN)・INV-C7(float64)

## Phase 5: US3 - リーク (P1, MVP)
- [X] T009 [P] [US3] `features/tests/unit/test_condition_change_leak.py`(新): INV-L1(自馬今走変更で不変)・INV-L2(同日/未来変更で不変)・INV-L3(grep: finish_order/result_status/odds 非参照)

## Phase 6: US4 - パリティ (P2)
- [X] T010 [US4] `materialize.py`: build_asof_features に condition_change ブロック(pace 渡し)結線。going は races 既存ロードで source_fingerprint 無改修確認。serving 未来=単一レース fallback
- [X] T011 [P] [US4] `test_materialize_core.py`(拡張): INV-P1(parity, 7 列含む)・INV-P2(7 列 materialized・odds トークン無し)・INV-P3(FEATURE_VERSION=="features-011")

## Phase 7: US5 - 採用判定 (P1)
- [X] T012 [US5] `training/cli.py`: feature-eval 既定 `--drop-groups` を `condition_change` に
- [X] T013 [US5] 実 DB walk-forward OOS: `feature-eval --drop-groups condition_change` で AdoptionReport。事前登録基準を機械適用、research に記録。ablation/diagnostic(条件替わりセグメント)は SECONDARY

## Phase 8: Polish
- [X] T014 [P] `features` lint/test 緑、eval/training/serving 透過で緑
- [X] T015 実 DB materialize parity bit 一致(features-011)・7 列カバレッジ
- [X] T016 採否反映: 採用なら `train-evaluate --model-version lgbm-033 --baseline baseline-uniform-v1 --artifacts-dir ../artifacts`→active 昇格・lgbm-032 retired・serving 確認。不採用ならブランチ保全
- [X] T017 [P] `CLAUDE.md` 033 サマリを OOS 結果で更新
- [X] T018 codex 反映確認: 新 base 主役・hinge×能力・冗長積除外 に沿うことを最終確認

## 注意
- 今走 result/finish_order/odds は生参照しない。base は直前 started レースのみ。
- bundle 採用後の列削りは禁止(選択リーク)。market/セグメントは SECONDARY。
- 027 単独不発の前例 → hinge×能力で「効く形」に変換するのが本 feature の仮説。
