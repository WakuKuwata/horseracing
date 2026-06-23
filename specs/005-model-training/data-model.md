# Data Model: モデルトレーニングと校正

新テーブルは MVP では作らない。既存データを読み、`model_versions` に書く + ファイル artifacts。

## 入力

| 用途 | 取得元 |
|---|---|
| 特徴量 (X) | Feature 004 `build_feature_matrix` の `model_input_features()` 列 (leak-safe, as-of) |
| win ラベル (y) | `race_results` (started 全頭、finished&finish_order==1=1 else 0) |
| 母集団 | started 馬 (entry_status='started')、取消・除外を除外 |
| 評価・baseline | Feature 003 harness / `model_versions` の baseline metrics_summary |

## 論理エンティティ

- **WinModel**: fold ごとの LightGBM win 確率モデル。seed/params 固定。
- **Calibrator**: train 内 時系列 held-out で fit (Platt 既定 / isotonic 可)。valid/test 不使用。
- **LightGBMPredictor**: Feature 003 の Predictor 契約を満たす。`fit(train_races)` で WinModel+Calibrator
  を学習、`predict_race` で `raw→校正→clip→正規化→Harville`。
- **AdoptionGate**: 全 label 指標 + ECE の事前固定基準。
- **Artifacts**: `model.txt` (LightGBM) / `calibrator.pkl` / `metadata.json`。

## win ラベル規則 (R3)

```
started 馬:
  win = 1  if race_results.result_status == 'finished' and finish_order == 1
        0  otherwise (DNF=stopped/disqualified を含む)
取消・除外 (entry_status != 'started'): 母集団に含めない
```

## 推論パイプライン不変条件 (R1/R5)

- **INV-T1**: 推論順序は raw win → 校正 → clip([eps,1-eps]) → レース内正規化(Σwin=1) → Harville。
- **INV-T2**: 出力は各馬 `0<=win<=top2<=top3<=1`、レース内合計が harness 許容内 (0.05/0.10/0.15)。
- **INV-T3**: 校正器は train 内 held-out のみで fit し、valid/test の race を一切参照しない (fold 漏れなし)。
- **INV-T4**: モデル特徴は `model_input_features()` のみ。結果確定 odds/popularity・ResultMarket を参照しない。
- **INV-T5**: 同一データ・同一 fold・同一 seed で学習・推論・評価が完全一致 (決定論)。

## 採用ゲート (R6)

```
adopt(active) iff
  win_logloss(model)  <  win_logloss(baseline)          # 厳密に下回る
  and top2_logloss(model) <= top2_logloss(baseline)     # 劣化なし
  and top3_logloss(model) <= top3_logloss(baseline)
  and win_ece(model)  <= ece_threshold                  # 閾値は設定可能
else candidate
```
baseline は `model_versions` の market / uniform の metrics_summary を参照 (同一評価条件)。

## metrics_summary (model_versions, Feature 003 と同形)

Feature 003 `EvalResult.to_summary()` の jsonb 形 (overall/by_fold/by_field_size_ece) に加え、本 feature は
学習メタ (model_family='lightgbm', feature_version, seed, calibration='platt', adoption_gate 結果) を含める。

## artifacts (ファイル、スキーマ変更なし)

```text
artifacts/model_versions/{model_version}/
  model.txt         # LightGBM (weights_uri)
  calibrator.pkl    # 校正器 (calibrator_uri)
  metadata.json     # seed, params, fold 境界, calibration 方式, feature_version, feature hash, git sha
```

## P2 (deferred)

- ハイパラ探索 (train 内 CV、valid 不使用)。
- OOF target encoding (fit-all-train→apply-all-train を避ける)。
- 評価母集団ミスマッチ (started 学習 vs finished 採点) の再検討。
