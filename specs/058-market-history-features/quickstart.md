# Quickstart / 検証: 過去走の市場評価 as-of 特徴(058)

実装後の検証手順。前提スタック = [[local-db-setup]](docker postgres `horseracing` DB)。DATABASE_URL=`postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`。

## US1: 特徴 + リーク境界

1. スモーク: `build_feature_matrix` が past_market 4 列を生成・カバレッジ表示(spike で ~82% 確認済)。
2. leak-guard: `pytest features/tests/unit/test_past_market_leak.py`
   - INV-L1 今走人気変更で不変・INV-L2 同日不変・INV-L3 未来不変・INV-P1 過去人気変更で変化・名前トークン検査。
3. パリティ: 既存 materialize parity テスト(features-015・新 4 列)が実 DB bit 一致で緑。
4. グローバル leak: `pytest features/tests/unit/test_feature020_leak_guard.py`(model_input_features に odds/popularity 名が無いこと=asof_mkt_* 命名で通過)。

## US2: 事前登録採用ゲート(フル OOS)

```
# PRIMARY(binary、020–056 と同一設定): baseline=features-014(past_market drop)vs candidate=features-015
training feature-eval --from <FIRST_VALID> --to 2024-12-31 --drop-groups past_market
```

- **判定(事前登録・plan 固定)**: win 平均 LogLoss 改善 + 平均 ECE 非悪化(1e-3)+ fold guards(strict majority・worst ECE 2e-3・worst dLL 5e-3)。
- **MUST(追加)**: 同一 harness の top2/top3 平均 LogLoss 非悪化(spike スクリプト同様に overall[top2]/[top3] を読む)。
- 数値を見て閾値を動かさない(憲法 III)。

## US3: 採用時のみ — production 再学習 + 共存(057 基盤)

1. production 確認: `training model-eval --objective pl_topk --target-encode jockey_id,trainer_id --calibration isotonic ...`(win/top2/top3 の production 寄与を確認、020 教訓)。
2. 精度最優先モデル学習・登録(非 active):
   `training train-evaluate --objective pl_topk ... --model-version lgbm-058-acc`(past_market 含む features-015)。
3. 用途ラベル(057): `training set-model-label --model-version lgbm-058-acc --display-name "精度最優先モデル" --purpose "過去市場評価(人気)含む・最高精度"`。
4. 予測生成(057/044): `serving predict-backfill --from --to --model-version lgbm-058-acc`。
5. 切替確認(057): レース詳細で意思決定支援(既定)⇄ 精度最優先を切替表示。**既定=意思決定支援のまま**(active 不変)。

## 不変・回帰

- default(意思決定支援)モデルの予測は本 feature 前と不変(past_market drop、SC-005)。
- 009 win→joint 不変。migration head 不変(0011)。スキーマ/API/openapi 不変。
- FEATURE_VERSION features-015。source_fingerprint に popularity 自動包含。
