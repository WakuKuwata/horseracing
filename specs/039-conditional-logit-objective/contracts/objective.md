# Contract: Objective(目的関数)インターフェイス (039)

## cond_logit custom objective

```
cond_logit_objective(group_sizes: list[int]) -> callable
    returns fobj(preds: np.ndarray, dataset) -> (grad: np.ndarray, hess: np.ndarray)
```

- `preds`: raw margin(全 model 行、レース連続整列済み)。
- 各 group(サイズ g)で:
  - `v = preds[g] - max(preds[g])`(数値安定)
  - `p = exp(v) / sum(exp(v))`
  - `y_g = labels[g]`
  - **sum(y_g) == 1 のときのみ**: `grad[g] = p - y_g`, `hess[g] = max(p*(1-p), 1e-6)`
  - **sum(y_g) != 1(勝ち馬不在/同着)**: `grad[g] = 0`, `hess[g] = 1e-6`(中立化、学習非寄与)
- 不変: group 割当は race_id のみ依存。labels は損失計算のみ。

## WinModel(objective 対応)

```
WinModel(seed, params, objective="binary")
  .fit(X, y, *, categorical_cols=None, group_ids=None) -> WinModel
  .predict(X, *, group_ids=None) -> np.ndarray   # per-row win prob
  .predict_softmax(X, group_ids) -> np.ndarray   # cond_logit: race softmax (predict の実体)
```

- objective="binary": 現行と bit 一致。group_ids 無視。predict = predict_proba[:,1]。
- objective="cond_logit": fit は group_ids(X 行順の race_id)必須 → 内部で stable sort → group sizes → custom objective で train。predict は raw_score → group_ids ごと softmax。group_ids=None で cond_logit predict は **エラー**(全入口で group 必須、codex)。
- 劣化(単一クラス/空): 一様 fallback(binary の _constant と同型)。

## LightGBMPredictor(objective パススルー)

```
LightGBMPredictor(session, ..., objective="binary")
  .fit(train_races)         # model 行 race_id を group_ids に、calib 予測も group で
  .predict_race(race)       # softmax → calibrator → 009(cond_logit)/ 現行(binary)
```

- fit: TE 適用後に X/y/race_id を stable sort で同期。cond_logit は model_df race_id を group_ids に。calib 行の予測は calib race_id を group_ids に(**レース単位に区切って** softmax、跨ぎ禁止)。
- 校正: calib softmax 確率に isotonic を fit(採用評価で softmax-only vs isotonic を両測)。
- fit_info_/artifacts に objective・postprocess(group_softmax)・calibration 種別を記録。

## serving(model_loader / predict_race)

```
ServingModel(..., objective="binary")
  .raw_predict(X) -> np.ndarray
serving.predict_race(...)   # started 整列 → TE 適用 → raw_predict → calibrator → 009
```

- raw_predict: cond_logit は softmax(booster.predict(raw_score=True)) over X(=1レース、全行1 group)。binary は現行。
- 後段(calibrator.transform → assemble_predictions で 009 Σ=1)不変。
- feature_hash は features-011 で整合。objective は metadata から復元。

## 採用ゲート(eval、predictor-agnostic)

```
evaluate_feature_adoption(session, candidate=cond_logit_predictor, baseline=binary_predictor,
                          start_date=...) -> AdoptionReport
```

- 18-fold expanding-yearly walk-forward OOS。両 predictor を同一特徴・同一 TE・同一 fold で。
- PRIMARY: win LogLoss(最終 postprocess 後の確率で)改善 かつ ECE 非悪化 + fold ガード(strict majority・worst-fold ECE tol・worst-fold dLogLoss tol)。
- SECONDARY(診断): winner-NLL(勝ち馬1頭レース限定)・top1・AUC。
- baseline/candidate とも最終 postprocess 後の確率で比較(codex)。
