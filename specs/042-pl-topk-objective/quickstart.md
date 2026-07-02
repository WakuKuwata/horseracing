# Quickstart: PL top-k 目的関数 (042)

## 1. 単体(DB-free)
```bash
cd training && uv run pytest tests/unit -k "pl_topk or cond_logit or objective" -q
cd ../serving && uv run pytest tests/unit -k "pl_topk or cond_logit" -q
```
期待: stage 勾配(手計算一致)・中断規則(同着/少頭数)・中立化・weight 適用・後方互換(binary/cond_logit bit 不変)・serving softmax 分岐 緑。

## 2. 採用評価(実 DB, 18-fold OOS, 校正 A/B)
```bash
cd training && export DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing
# inline 比較: baseline=cond_logit+TE+isotonic(lgbm-041 相当) vs candidate=pl_topk+TE+{isotonic,none}
# (039 と同型の evaluate_feature_adoption スクリプト)
```
期待: PRIMARY(win LogLoss 改善+ECE 非悪化)+ fold ガード。良い校正経路を採用。SECONDARY: winner-NLL/top1/AUC/top2/top3。

## 3. 採用時: lgbm-042
```bash
uv run python -m horseracing_training train-evaluate \
  --model-version lgbm-042 --objective pl_topk --calibration <採用経路> \
  --target-encode jockey_id,trainer_id --te-smoothing 50 \
  --baseline baseline-uniform-v1 --artifacts-dir ../artifacts
```
期待: active 昇格・lgbm-041 retired・serving ロード(objective=pl_topk・feature_hash=features-012)。

## 4. 不採用時
main は lgbm-041 のまま。ブランチ保全 + 負の結果記録。

## チェックリスト
- [ ] STAGE_WEIGHTS=[1,.5,.25] 固定(OOS 後に動かさない)
- [ ] rank は label のみ(feature_cols/model_input_features 外)
- [ ] 予測経路は cond_logit と同一(objective 集合分岐のみ)
- [ ] baseline/candidate とも最終 postprocess 後の確率で比較
- [ ] binary/cond_logit の後方互換 bit 不変
