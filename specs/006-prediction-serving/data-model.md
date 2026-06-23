# Data Model: 予測 serving

新テーブルは作らない。既存の `model_versions` + 成果物を読み、`prediction_runs` / `race_predictions` /
`feature_snapshots` に書く。成果物は前処理器(`preprocessor.pkl`)を追加。

## 入力

| 用途 | 取得元 |
|---|---|
| モデル/校正器/前処理器 | `model_versions`(active or 明示) + artifacts(model.txt / calibrator.pkl / preprocessor.pkl) |
| 特徴量 (X) | Feature 004 `build_feature_matrix(end_date=対象日)` の `model_input_features()` 列(as-of、started 母集団) |
| 母集団 | `entry_status='started'` の出走馬(取消・除外を除外、`race_results` 非依存) |

## 論理エンティティ

- **ServingModel**: `booster`(LightGBM)+ `calibrator`(Platt/isotonic)+ `preprocessor`(列順・categorical
  方針・target encoders・te_smoothing)+ メタ(model_version, feature_version, feature_hash, seed, calibration)。
  `model_versions` + 成果物から復元。
- **ServingPredictor**: ServingModel で 1 レースを推論。`raw→校正→clip→正規化→Harville`。session 非依存
  (特徴行列は pipeline が用意)。
- **PredictionRun / RacePrediction / FeatureSnapshot**: 永続化先(下記)。

## 前処理器成果物(preprocessor.pkl、スキーマ変更なし・ファイル追加)

```text
artifacts/model_versions/{model_version}/
  model.txt          # LightGBM booster (既存)
  calibrator.pkl     # 校正器 (既存)
  preprocessor.pkl   # 追加: {feature_cols(列順), categorical_cols, target_encode_cols,
                     #        te_smoothing, encoders{col->TargetEncoder}, feature_version, feature_hash}
  metadata.json      # 既存 (seed/params/fold/feature_hash/feature_version/git_sha …)
```

後方互換: `preprocessor.pkl` 欠落時、metadata の `target_encode_cols` が空なら再構成
(`feature_cols=model_input_features()`、encoders 無し)し `feature_hash` 一致を検証。TE 使用かつ欠落は fail-fast。

## 推論不変条件

- **INV-S1**: 推論順序は `raw win → 校正 → clip([eps,1-eps]) → レース内正規化(Σwin=1) → Harville top2/top3`
  (Feature 005 INV-T1 と同一)。Harville は `horseracing_eval.baselines.harville_topk` を再利用。
- **INV-S2**: 出力は各馬 `0<=win<=top2<=top3<=1`・レース内合計が許容内(`check_consistency` + `PROB_MONOTONIC`)。
- **INV-S3**: モデル入力は `model_input_features()` のみ。ResultMarket / `race_results` を参照しない(リーク禁止)。
  特徴は as-of(`race_date<R`、同日除外)。
- **INV-S4**: 学習時 `feature_hash` / `feature_version` と推論時の特徴スキーマが一致しないと推論しない(fail-fast)。
- **INV-S5**: 決定論。同一(race, model, logic_version)・同一成果物で `race_predictions` が完全一致。
- **INV-S6**: 母集団は `entry_status='started'`。取消・除外は除外。`race_results` の有無に依存しない。

## active モデル解決ルール

```
active = model_versions WHERE adoption_status='active'
  count==1            -> それを使う
  count==0            -> エラー(採用モデル無し)
  count>=2            -> エラー(--model-version 明示要求)
明示 --model-version 指定 -> その model_version を使う(存在チェック + 成果物存在チェック)
```

## 書き込み先(既存スキーマ)

- **prediction_runs**: `prediction_run_id`(uuid, 自動)、`race_id`、`model_version`、`logic_version`、
  `computed_at`(now)。推論実行ごとに 1 行(append-only)。
- **race_predictions**: `prediction_run_id` × `horse_id`、`win_prob`/`top2_prob`/`top3_prob`(`PROB_MONOTONIC`
  制約: `win<=top2<=top3`)。出走全頭。
- **feature_snapshots**: `prediction_run_id` × `horse_id`、`feature_version`、`features`(jsonb = 前処理後
  model-input ベクトル + `_raw_win` + `_calibrated_win`)。

## logic_version

```
logic_version = f"feat={feature_version};serve={SERVING_LOGIC_VERSION}"
```
`SERVING_LOGIC_VERSION` は serving/後処理(校正適用・clip・正規化・Harville)のロジック版を表す定数。
特徴ロジックと推論ロジックの双方が監査で一意に追える。

## スコープ外(Feature 007 へ)

- 推奨・買い目・券種(`recommendations`)、期待値/ROI、推定オッズ変換。
- 複数 label スキーマの併存、自動運用(スケジューラ)。
