---
description: "Task list — OOF Target Encoding + isotonic 校正 (036)"
---

# Tasks: OOF Target Encoding + isotonic 校正 (Modeling)

**Input**: [spec.md](spec.md)

## Phase 1: eval 経路
- [X] T001 `training/cli.py`: `model-eval` サブコマンド(TE candidate vs no-TE baseline を evaluate_feature_adoption で比較、--target-encode/--te-smoothing/--calibration)。train-evaluate に --te-smoothing
- [X] T002 既存 OOF TE infra のリーク安全確認: harness fold 毎 train-only fit、oof_target_encode は chronological fold + OOF、予測は training-only encoder

## Phase 2: 評価
- [X] T003 model-eval(jockey_id,trainer_id, platt, smoothing10): LogLoss 0.23187→0.22476・AUC +0.028・18/18 fold(ECE 悪化で in-run gate False)。リーク傍証=市場非超過
- [X] T004 model-eval(isotonic, smoothing50): baseline(isotonic 単独)0.22489/ECE0.00203=校正だけで大改善、candidate(isotonic+TE)0.21847/AUC0.790/ECE0.00401

## Phase 3: 採用
- [X] T005 lgbm-036 再学習(--calibration isotonic --target-encode jockey_id,trainer_id --te-smoothing 50)→ 生産 lgbm-033 を全 OOS 指標で上回ることを確認 → active 昇格・lgbm-033 retired・serving ロード確認(feature_hash=features-011 整合)
- [X] T006 `CLAUDE.md` 036 サマリ追記、commit & main マージ

## 注意
- TE/校正は predictor 内部=feature 列/FEATURE_VERSION 不変・スキーマ変更なし・lap backfill 非干渉・serving 安全。
- リーク安全は既存 infra(020)+ harness train-only。市場 0.202 非超過が傍証。
- sire_name TE は matrix 列でないため別途(feature 変更)。
