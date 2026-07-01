# Quickstart: Conditional-logit 目的関数 (039)

## 前提
- horseracing DB(`postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`)、2007-2025 ingest 済み。
- main = lgbm-036/features-011(binary)。本 feature は 039-conditional-logit-objective ブランチ。

## 1. 単体(DB-free)
```bash
cd training && uv run pytest tests/ -k "cond_logit or objective" -q
```
期待: cond_logit の softmax/grad/hess(sum(y)=1 で grad=p−y、sum(y)!=1 で 0)、group 整列、binary 後方互換(既定 objective で現行と一致)が緑。

## 2. 採用評価(実 DB, 18-fold OOS)
校正2経路を両方測る(codex 是正):
```bash
cd training && export DATABASE_URL="postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
# baseline = binary(lgbm-036 相当) vs candidate = cond_logit
uv run horseracing-training model-eval \
  --objective cond_logit --target-encode jockey_id,trainer_id --te-smoothing 50 \
  --calibration isotonic          # (a) softmax→isotonic→009
uv run horseracing-training model-eval \
  --objective cond_logit --target-encode jockey_id,trainer_id --te-smoothing 50 \
  --calibration none              # (b) softmax-only→009
```
期待: AdoptionReport(win LogLoss / ECE / fold 別 / winner-NLL・top1・AUC 診断)。PRIMARY(win LogLoss 改善 かつ ECE 非悪化)+ fold ガードを機械適用。良い校正経路を採る。

## 3. 採用時: lgbm-039 学習・登録
```bash
cd training && export DATABASE_URL=...
uv run horseracing-training train-evaluate \
  --model-version lgbm-039 --objective cond_logit \
  --target-encode jockey_id,trainer_id --te-smoothing 50 --calibration isotonic \
  --baseline baseline-uniform-v1 --artifacts-dir ../artifacts
```
期待: adopted=True で active 昇格・lgbm-036 retired。metadata に objective=cond_logit・postprocess=group_softmax・calibration 記録。feature_hash=features-011 整合。

## 4. serving 確認
```bash
cd serving && export DATABASE_URL=...
uv run pytest tests/ -k "cond_logit or predict" -q
```
期待: lgbm-039 をロードし predict_race が softmax→校正→009 で各馬 win 確率(Σ=1)。014 API / 021 表示は既存契約のまま。

## 5. 不採用時
main は lgbm-036/features-011 のまま維持。ブランチ保全(027/037/038 前例)。spec に負の結果を記録。

## 検証チェックリスト
- [ ] binary 後方互換(既存 training/serving テスト透過・lgbm-036 予測不変)
- [ ] cond_logit softmax/grad/hess 正当・sum(y)!=1 中立化
- [ ] leak-guard(group が結果非参照・今走変更で他馬予測不変・odds/結果非特徴)
- [ ] calib は必ずレース単位に区切って softmax(跨ぎ禁止)
- [ ] 校正2経路(isotonic vs none)を 18-fold で両測・良い方採用
- [ ] 009 不変(Σexacta=1 等)・feature_hash=features-011
- [ ] baseline/candidate とも最終 postprocess 後の確率で比較
