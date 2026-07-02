---
description: "Task list — PL top-k 目的関数 (042)"
---

# Tasks: Plackett-Luce top-k (listwise) 目的関数

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [quickstart.md](quickstart.md)

**Tests**: リーク(rank=label のみ)・後方互換・stage 勾配の正しさ がテスト中核。

## Phase 1: Foundational
- [X] T001 `training/src/horseracing_training/cond_logit.py`: `STAGE_WEIGHTS=(1.0,0.5,0.25)` + `pl_topk_objective(group_sizes, ranks)`(stage1 非一意=group 中立化、stage j 非一意 or remaining<2=break、grad/hess w_j 加重加算、hess floor、sample_weight 適用)
- [X] T002 [P] `training/tests/unit/test_pl_topk.py`(新): stage 勾配手計算一致(1 group, rank 1/2/3/0)・中断規則(同着 stage2 → stage1 のみ)・stage1 同着=中立化・remaining<2 break・weight 適用・group_sizes 整合

## Phase 2: US2 - rank ラベル
- [X] T003 [US2] `training/src/horseracing_training/dataset.py`: `RANK_LABEL="finish_rank"` 追加(race_results finished finish_order≤3 → 1..3、他 0。win と同一機構)。feature_cols 不変
- [X] T004 [P] [US2] dataset テスト(既存 or 新規): finish_rank が feature_cols/model_input_features 外・値の正しさ

## Phase 3: US1/US3 - 結線
- [X] T005 [US1] `training/src/horseracing_training/win_model.py`: objective="pl_topk" 分岐(fit: group_ids+ranks 必須・stable sort 同期→pl_topk_objective、predict: cond_logit と同一 softmax 分岐=objective 集合判定)
- [X] T006 [US1] `training/src/horseracing_training/predictor.py`: pl_topk 時 model_df[RANK_LABEL] を ranks で WinModel.fit に、calib/predict_race は cond_logit 経路共有、fit_info_ postprocess=group_softmax、HPO ガード拡張
- [X] T007 [P] [US1] `training/tests/unit/test_win_model_objective.py`(拡張): pl_topk fit→predict Σ=1・上位馬高確率・ranks 無しエラー・binary/cond_logit 後方互換
- [X] T008 [US3] `serving/src/horseracing_serving/model_loader.py`: raw_predict の softmax 分岐を `objective in ("cond_logit","pl_topk")` に。`serving/tests/unit/test_cond_logit_serving.py` 拡張(pl_topk softmax)
- [X] T009 [US3] `training/src/horseracing_training/cli.py`: --objective choices に pl_topk(model-eval/train-evaluate)

## Phase 4: US4 - 採用判定
- [X] T010 [US4] 実 DB 18-fold OOS(inline スクリプト、039 同型): baseline=cond_logit+TE+isotonic vs candidate=pl_topk+TE+{isotonic,none} 両測。PRIMARY+fold ガード機械適用、結果を research に記録
- [X] T011 [US4] 採否反映: 採用なら train-evaluate lgbm-042 → active・lgbm-041 retired・serving 確認。不採用ならブランチ保全

## Phase 5: Polish
- [X] T012 [P] lint(ruff)+ training/serving/eval 全テスト緑・leak-guard(今走結果変更で pl_topk 予測不変)
- [X] T013 [P] CLAUDE.md 042 サマリ更新・memory 記録
- [X] T014 codex second opinion 反映確認(research R6 に記録)

## 注意
- STAGE_WEIGHTS 固定(OOS 後調整禁止)。rank は label のみ。予測経路は増やさない(集合分岐のみ)。
- binary/cond_logit は bit 後方互換。スキーマ・FEATURE_VERSION(features-012)不変。
