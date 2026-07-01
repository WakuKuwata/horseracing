---
description: "Task list — Conditional-logit (race-softmax) 目的関数 (039)"
---

# Tasks: Conditional-logit (race-softmax) 目的関数

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/objective.md](contracts/objective.md) / [quickstart.md](quickstart.md)

**Tests**: リーク防止(憲法 II)・後方互換・確率整合 が核のため**テスト中核**。cond_logit 単体 / backward-compat / leak-guard / calibration 統合 を必須化。

**Organization**: MVP = US1(cond_logit 目的関数) + US2(TE+校正統合) + US3(リーク/確率不変)。US4(serving)/US5(採用)は上に乗る。

## Phase 1: Setup
- [X] T001 前提確認: main(features-011/lgbm-036 binary)・LightGBM 4.x custom objective API(`params["objective"]=callable`、旧 fobj 廃止)・既存 target_encoding(oof/fit/apply)・folds(chronological_race_folds)・calibration(fit_calibrator/isotonic)・split_train_by_time・head 0006。spike スクリプトの cond_logit 実装を参照点にする
- [X] T002 [P] [contracts/objective.md](contracts/objective.md) の objective 契約(cond_logit_objective/WinModel/LightGBMPredictor/serving/採用ゲート)・sum(y)=1 規律・group 必須・校正2経路 を確定(契約先行、codex 反映)

## Phase 2: Foundational（全 story の前提）
- [X] T003 `training/src/horseracing_training/cond_logit.py`(新): `race_softmax(scores, group_sizes)`(stable, max 減算)・`cond_logit_objective(group_sizes)`(fobj→grad=p−y/hess=max(p(1−p),1e-6)、**sum(y_g)!=1 の group は grad/hess=0 中立化**)・`group_sizes_from_race_ids(race_ids)`(stable sort 前提の連続 group サイズ)。生の今走 result/odds 非参照
- [X] T004 `training/src/horseracing_training/win_model.py`: `WinModel(objective="binary")` 追加。cond_logit 時 `fit(X,y,*,categorical_cols,group_ids)` は X/y/group_ids を race_id で stable sort → group sizes → `lgb.train(params={...,"objective":cond_logit_objective(gs)})`。`predict(X,*,group_ids)` は cond_logit=raw_score→group softmax(group_ids=None ならエラー)、binary=現行 predict_proba[:,1]。劣化(単一クラス/空)は一様 fallback。**binary 既定は現行と bit 一致**

## Phase 3: US1 - cond_logit 目的関数 (P1, MVP)
- [X] T005 [US1] `training/tests/unit/test_cond_logit.py`(新): INV-O1(race_softmax が group 内 Σ=1・数値安定)・INV-O2(grad=p−y, hess=max(p(1−p),eps))・INV-O3(sum(y)!=1 group で grad/hess=0)・INV-O4(group_sizes_from_race_ids の連続性)
- [X] T006 [US1] `training/tests/unit/test_win_model_objective.py`(新): 合成データ(数レース各1勝)で WinModel(objective=cond_logit).fit→predict がレース内 Σ=1 の妥当確率・勝ち馬に高確率。1頭立て/勝ち馬不在の劣化非例外。binary 既定は現行挙動(後方互換)

## Phase 4: US2 - TE + isotonic 校正統合 (P1, MVP)
- [X] T007 [US2] `training/src/horseracing_training/predictor.py`: `LightGBMPredictor(objective="binary")` 追加。fit で TE 適用後に X/y/race_id を stable sort 同期(row/race 同期、codex)。cond_logit は model 行 race_id を group_ids に。calib 行予測は **calib race_id で group(レース単位に区切って softmax、跨ぎ禁止)**。校正は calib softmax 確率に fit_calibrator。fit_info_ に objective/postprocess(group_softmax)/calibration 記録
- [X] T008 [US2] predict_race を objective 分岐: cond_logit=単一レース softmax→calibrator.transform→009(assemble_predictions Σ=1)。binary=現行。**baseline/candidate とも最終 postprocess 後の確率**
- [X] T009 [P] [US2] `training/tests/unit/test_predictor_cond_logit.py`(新): cond_logit+TE+isotonic の fit→predict_race が Σ=1・[eps,1−eps]クリップ。calib が race 単位 softmax(跨ぎ無し)。TE 列 OOF リーク安全(036 と同型)

## Phase 5: US3 - リーク安全・確率整合 (P1, MVP)
- [X] T010 [P] [US3] `training/tests/unit/test_cond_logit_leak.py`(新): INV-L1(今走 finish/result 変更で他馬 cond_logit 予測不変=as-of/TE 不変)・INV-L2(group は race_id のみ依存で finish_order 非参照)・INV-L3(grep: cond_logit.py/predictor 分岐が odds/finish_order/result_status を生参照しない)・INV-L4(予測確率/stake がモデル特徴に非流入)
- [X] T011 [P] [US3] `training/tests/unit/test_cond_logit_prob_consistency.py`(新): cond_logit 予測を 009 win→joint に渡し Σexacta=1/Σtrifecta=1/unordered=sum-of-orderings/joint==harville_topk が保たれる(IV 不変)

## Phase 6: US4 - serving 対応 (P2)
- [X] T012 [US4] `training/src/horseracing_training/artifacts.py`: objective/postprocess を metadata/preprocessor に記録(feature_hash=features-011 不変、objective は model_family/metadata で区別)。save_model_version が cond_logit artifacts(model+calibrator+encoders+objective)を保存
- [X] T013 [US4] `serving/src/horseracing_serving/model_loader.py`: ServingModel に objective フィールド + load 時に metadata から復元。`raw_predict(X)` を objective 分岐(cond_logit=softmax(booster.raw_score(X)) over X=1レース、binary=現行)
- [X] T014 [P] [US4] `serving/tests/` : lgbm-039 相当(cond_logit)をロード→predict_race が softmax→校正→009 で Σ=1。feature_hash=features-011 整合。全経路 postprocess 一致(eval と serving で同じ、codex 最大リスク対策)

## Phase 7: US5 - 採用判定 (P1)
- [X] T015 [US5] `training/src/horseracing_training/cli.py`: `model-eval`/`train-evaluate` に `--objective {binary,cond_logit}` 追加。model-eval は baseline=binary vs candidate=cond_logit を evaluate_feature_adoption(同一特徴/TE/fold)。`--calibration none` で softmax-only 経路も測れるように
- [X] T016 [US5] 実 DB 18-fold walk-forward OOS: **校正2経路(isotonic vs none)を両測**(quickstart 手順)。AdoptionReport(win LogLoss/ECE/fold + winner-NLL/top1/AUC 診断)。事前登録基準(PRIMARY win LogLoss 改善+ECE 非悪化 + strict majority + worst-fold ECE tol + worst-fold dLogLoss tol)を機械適用、良い校正経路を採る。結果を research/quickstart に記録

## Phase 8: Polish
- [X] T017 [P] `training`/`serving` lint(ruff)・pytest 緑、eval 透過で緑。binary 後方互換(lgbm-036 予測不変)を明示確認
- [X] T018 採否反映: 採用なら `train-evaluate --model-version lgbm-039 --objective cond_logit --calibration <採用経路> --target-encode jockey_id,trainer_id --te-smoothing 50 --baseline baseline-uniform-v1 --artifacts-dir ../artifacts`→active 昇格・lgbm-036 retired・serving 自動ロード確認。不採用なら main を lgbm-036/features-011 のまま維持しブランチ保全
- [X] T019 [P] `CLAUDE.md` 039 サマリを OOS 結果で更新(採否・win LogLoss/ECE/winner-NLL/top1/fold)。memory に結果記録
- [X] T020 codex 反映確認: 校正 A/B・sum(y)=1 規律・全経路 postprocess 一致・group row 同期 が実装に反映されていることを最終確認

## Dependencies & Execution Order
- Phase1→2(T003 cond_logit core・T004 WinModel objective)が全 story をブロック。
- US1(T005/T006)→US2(T007/T008/T009)は predictor が WinModel に依存で逐次。US3(T010/T011 leak/確率)は US1/US2 後。MVP=US1+US2+US3。
- US4(T012-T014 serving)は artifacts/predictor 後。US5(T015-T016 評価)は結線後。Polish(T017-T020)は最後。

## Parallel 実行例
- T005/T006 は同領域(cond_logit/win_model)で逐次。T009[P]/T010[P]/T011[P] は別ファイルで並行可。T014[P](serving)・Polish T017/T019[P]。

## 注意
- cond_logit は fit/predict/serving/eval の**全入口で group(または single-race)必須**。予測の意味が objective で変わるため postprocess を全経路一致させる(codex 最大リスク)。
- calib は必ずレース単位に区切って softmax(全体跨ぎ禁止)。sum(y)!=1 は学習中立化。
- 校正は softmax-only vs isotonic を 18-fold で両測(事前登録、選択リークでない)。
- 採用後の恣意的な閾値変更禁止(数値を見てから動かさない=憲法 III)。市場超過は採否バーでない。
- binary 既定は bit 後方互換(lgbm-036 予測不変)。スキーマ変更なし・FEATURE_VERSION 不変(features-011)。
