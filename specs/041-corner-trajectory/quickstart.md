# Quickstart: コーナー通過順の軌跡特徴 (041)

## 前提
- horseracing DB(localhost:15432)、main = features-011 / lgbm-039(cond_logit)。
- 本 feature は 041-corner-trajectory ブランチ。

## 1. 単体(DB-free)
```bash
cd features && uv run pytest tests/unit/test_corner_trajectory_features.py tests/unit/test_corner_trajectory_leak.py -q
uv run pytest tests/unit/test_materialize_core.py -q   # parity + features-012
```
期待: 値の正しさ(late_gain/early_pos/mid_move・expanding as-of)・leak-guard(今走/同日/未来 不変+grep)・parity 緑。

## 2. 実 DB parity + カバレッジ
```bash
cd features && export DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing
uv run python -m horseracing_features materialize --output ../artifacts/features.parquet
# use_materialized read == in-memory bit 一致、4 列カバレッジ ~89% を確認
```

## 3. 採用評価(18-fold OOS、事前登録)
```bash
cd training && export DATABASE_URL=...
uv run python -m horseracing_training feature-eval   # 既定 drop-groups=corner_trajectory
```
期待: AdoptionReport(PRIMARY + fold ガード)機械適用で採否。

## 4. 採用時: lgbm-041(production 構成)
```bash
uv run python -m horseracing_training train-evaluate \
  --model-version lgbm-041 --objective cond_logit --calibration isotonic \
  --target-encode jockey_id,trainer_id --te-smoothing 50 \
  --baseline baseline-uniform-v1 --artifacts-dir ../artifacts
```
期待: active 昇格・lgbm-039 retired・serving 自動ロード(feature_hash=features-012)。

## 5. 不採用時
main は features-011/lgbm-039 のまま。ブランチ保全 + spec に負の結果を記録。

## 検証チェックリスト
- [ ] 4 列の値・エッジ(コーナー1つ/parse 失敗/デビュー→NaN)
- [ ] leak-guard(merge_asof allow_exact_matches=False = strictly-before+同日除外)
- [ ] materialize parity bit 一致・source_fingerprint 無改修・features-012
- [ ] feature-eval 事前登録ゲート機械適用
- [ ] probability/API/front 透過で緑
