# Contract: LightGBMPredictor

`horseracing_training` が公開する、Feature 003 Predictor 契約の実装。

```python
class LightGBMPredictor:
    is_leaky_reference = False   # 結果確定 odds/popularity を使わない (FR-004)

    def __init__(self, session, *, seed=42, calibration="platt",
                 ece_clip=1e-6, params=None): ...

    def fit(self, train_races: list[RaceContext]) -> None:
        # 1. leak-safe feature matrix を取得 (Feature 004, キャッシュ可)
        # 2. train race_id 行を選択、win ラベル (started 全頭, finished&1着=1 else 0)
        # 3. train を時系列で model-fit / calibration-fit に分割
        # 4. WinModel を model-fit で学習 (seed 固定)、Calibrator を calibration-fit で fit
        #    (valid/test を一切見ない、INV-T3)

    def predict_race(self, race: RaceContext) -> dict[str, Prediction]:
        # raw win -> calibrate -> clip([eps,1-eps]) -> race-normalize(Σ=1) -> Harville top2/top3
        # 返り値: started 馬全頭の Prediction。INV-T1/T2 を満たす
```

## 保証 (harness が検証)

- `predict_race` の出力は各馬 `0<=win<=top2<=top3<=1`、レース内合計が許容内 (harness fail-fast を通る)。
- 特徴は `model_input_features()` のみ。`RaceContext.started_horses[].result_market` (ResultMarket) を
  参照しない (リーク検査)。
- `fit` は train_races のみ。校正器は train 内 held-out のみで fit (valid を見ない)。
- 決定論 (seed 固定で同一出力)。
- Harville は `horseracing_eval.baselines.harville_topk` を再利用 (market baseline と同一導出)。
