# Contract: serving ロード・推論・実行

`horseracing_serving` が公開する契約。

## モデルロード

```python
@dataclass(frozen=True)
class ServingModel:
    model_version: str
    booster: object            # LightGBM Booster
    calibrator: object         # Calibrator (training.calibration)
    feature_cols: list[str]    # 学習時の列順
    categorical_cols: list[str]
    encoders: dict             # col -> TargetEncoder (TE 不使用なら空)
    feature_version: str
    feature_hash: str
    metadata: dict

def resolve_model_version(session, explicit: str | None) -> str:
    # explicit があればそれ。無ければ active を解決:
    #   active が 1 つ -> それ。0 -> エラー。複数 -> エラー(明示要求)。

def load_serving_model(session, model_version: str | None = None) -> ServingModel:
    # 1. resolve_model_version
    # 2. 成果物 model.txt / calibrator.pkl / preprocessor.pkl をロード
    #    preprocessor 欠落時: metadata.target_encode_cols が空なら再構成(feature_cols=model_input_features())、
    #    feature_hash 一致を検証。TE 使用かつ欠落は ServingError で fail-fast。
    # 3. 現行 model_input_features() の feature_hash と保存 feature_hash が不一致なら fail-fast (INV-S4)。
```

## 推論

```python
def predict_race(model: ServingModel, race_id: str, feature_rows: pd.DataFrame) -> tuple[
        dict[str, Prediction], dict[str, dict]]:
    # feature_rows: 当該 race_id の started 母集団 (race_id, horse_id, feature_cols...) as-of 済み
    # 1. started 馬順に整列、encoders を適用して model-input 行列を構成 (列順 = model.feature_cols)
    # 2. raw = booster.predict(X) -> calibrator.transform -> clip -> 正規化(Σ=1) -> harville_topk
    # 3. 返り値: (predictions[horse_id]->Prediction, snapshots[horse_id]->{features..., _raw_win, _calibrated_win})
    # check_consistency(predictions) を通す (INV-S2)。ResultMarket/race_results 不参照 (INV-S3)。
```

## 実行(永続化込み)

```python
@dataclass(frozen=True)
class ServingResult:
    prediction_run_id: uuid.UUID
    race_id: str
    model_version: str
    logic_version: str
    n_horses: int

def run_serving(session, *, race_id: str | None = None, date: datetime.date | None = None,
                model_version: str | None = None) -> list[ServingResult]:
    # race_id か date のどちらか必須。date 指定は当日の対象レース全件。
    # 1. load_serving_model
    # 2. build_feature_matrix(session, end_date=対象日) を一度構築、対象レース行を抽出 (as-of)
    # 3. 各レース: predict_race -> check_consistency
    # 4. persist: prediction_runs + race_predictions + feature_snapshots (append-only)
    # 5. ServingResult を返す
```

## 保証(テストで検証)

- 出力は各馬 `0<=win<=top2<=top3<=1`・レース内合計が許容内(`PROB_MONOTONIC` を満たし保存できる)。
- 特徴は `model_input_features()` のみ。ResultMarket / `race_results` を参照しない(リーク検査)。
- 結果未確定レース(race_results 無し)でも推論・保存できる。
- feature_hash / feature_version 不一致、または TE モデルの前処理器欠落で fail-fast(保存しない)。
- 決定論: 同一(race, model, logic_version)・同一成果物で 2 回実行すると `race_predictions` が完全一致。
- 再実行は新しい `prediction_run`(append-only)。
