# horseracing-training

単一 win **LightGBM** を Feature 003 の Predictor 契約として学習・校正・評価・採用・保存する
パッケージ。`db` / `features` / `eval` にパス依存。

## 設計の要点

- **母集団 / ラベル**: started 全頭。`win = 1` iff `result_status='finished'` かつ `finish_order==1`、
  それ以外は 0（DNF=stopped/disqualified 含む）。評価採点用の `labels.derive_labels`（finished-only）
  とは別物で、学習はこれを再利用しない（`dataset.py`）。
- **特徴量**: Feature 004 の `model_input_features()` のみ。as-of（race_date < R）で leak-safe。
  全レースを一度だけ matrix 化してキャッシュ（履歴は行ごとに as-of なので将来レースに依存しない）。
  結果確定 odds/popularity・`ResultMarket` はモデルが一切参照しない（リーク検査 `test_leak.py`）。
- **校正（最重要）**: 校正器（既定 **Platt** / isotonic 可）は **train 内の時系列 held-out** だけで fit。
  race 単位で時系列分割し、同一レースが model-fit と calibration-fit に跨らない。valid/test は一切
  参照しない（INV-T3、035/036 の再発防止）。退化スライス（単一クラス等）は identity+clip に fallback。
- **推論順序（INV-T1）**: `raw win → 校正 → clip([eps,1-eps]) → レース内正規化(Σwin=1) → Harville top2/top3`。
  Harville は `horseracing_eval.baselines.harville_topk` を再利用し market baseline と同一導出。
  これで `0<=win<=top2<=top3<=1`・Σ 許容内を機構保証。
- **採用ゲート**: `win LogLoss(model) < baseline` 厳密 かつ `top2/top3 LogLoss <= baseline` かつ
  `win ECE <= 閾値`（事前固定）→ `active`、そうでなければ `candidate`。baseline は `model_versions` の
  market/uniform を同一評価条件で参照。
- **保存（スキーマ変更なし）**: `model_versions` に upsert（`metrics_summary` + `weights_uri` +
  `calibrator_uri`）し、`artifacts/model_versions/{model_version}/` に `model.txt` /
  `calibrator.pkl` / `metadata.json`（seed/params/fold 境界/校正方式/feature_version/feature hash/
  git sha）を書く。保存する成果物は **全履歴で学習した serving モデル**、報告指標は walk-forward。

## CLI

```bash
cd training
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 取込済み + baseline 保存済み DB
uv run python -m horseracing_training train-evaluate \
    --first-valid-year 2008 --calibration platt --ece-threshold 0.05 \
    --baseline uniform --model-version lightgbm-win-v1 --artifacts-dir artifacts
```

walk-forward で fold ごとに LightGBM 学習 + train-only 校正 → harness 評価 → baseline と比較 →
採用判定 → `model_versions` + artifacts に保存。label 別指標と採用結果を表示。

## テスト

```bash
cd training
uv run pytest tests/unit       # 整合性・校正 fold 漏れ・ECE 改善・採用ゲート・HPO/OOF（Docker 不要）
uv run pytest -m integration   # 実 DB（testcontainers）で学習→評価→保存・決定論・リーク検査
```

最重要テスト: `tests/unit/test_calibration_foldleak.py`（校正 fold 漏れ）、
`tests/unit/test_consistency.py`（確率整合性）、`tests/integration/test_train_eval.py`
（baseline 超え + 校正が valid 不変）。
