# 測定記録 3(2026-10-04・append-only の別ファイル)

pre-registration.md は編集していない(引用ハッシュ a3fdbd98ac86f83c26a47ad5e23157eea92f81183528e4a1a55059c1aab6c75d を保つため)。
この記録を pre-registration.md の「測定記録 3」として転記するかは利用者が決める。
実行前の事前登録: artifacts/roi_explore/missed_20261004/R03_080_gate/prereg.json(sha256 8b86a5f7dbb91ca354e48eae30cc2b5b5f69024dc1e1e3d5937b30a36a4fdc06)。

## 実行したコマンド(凍結パラメータは不変)

- window = 2026-07-23..2026-10-03(10-04 は実行時点で 24 レース中 19 しか確定していなかったので除外)
- seed=20260723・b=2000・alpha=0.05・n_min/控除率=凍結値・baseline は lowest_oest と uniform の両方(§3)

### STEP 0: 既定(--model-version なし)= 現 active mix-129-nj6 → 実行不能

`exotic-gate --from 2026-07-23 --to 2026-10-03` は
`AttributeError: 'MixtureServingModel' object has no attribute 'raw_predict'` で落ちた。
gate は serving.predictor.predict_race を直接呼ぶが、131 は混合モデルを serving.pipeline にしか結線していない。
§2 が名指しする lgbm-065 は features-018 で、現行 features-021 では servable でない。

### STEP 1: --model-version lgbm-094-cap900(測定記録 1・2 の当時の active)

```
exotic-gate 2026-07-23..2026-10-03 [lv: window=2026-07-23..2026-10-03;baseline=lowest_oest;seed=20260723;alpha=0.05;b=2000;n_min=place:500,quinella:500,wide:500,exacta:700,trio:1000,trifecta:1500;takeout=place:0.20,quinella:0.225,wide:0.225,exacta:0.25,trio:0.25,trifecta:0.275;series=prospective-primary]
place REJECT 685 23 -0.009309002433090033 ci=[-0.11160897536687629,0.10537291256483308] 1.0
quinella REJECT 690 23 0.018765700483091733 ci=[-0.4458376404024472,0.6872290154156896] 1.0
wide REJECT 691 23 -0.017301013024602 ci=[-0.30305705938941235,0.33669885250412224] 1.0
exacta NO_DECISION 691 23 0.3537192474674385 ci=[None,None] None
trio NO_DECISION 691 23 -0.18999276410998556 ci=[None,None] None
trifecta NO_DECISION 691 23 -0.3539218523878437 ci=[None,None] None

exotic-gate 2026-07-23..2026-10-03 [lv: window=2026-07-23..2026-10-03;baseline=uniform;seed=20260723;alpha=0.05;b=2000;n_min=place:500,quinella:500,wide:500,exacta:700,trio:1000,trifecta:1500;takeout=place:0.20,quinella:0.225,wide:0.225,exacta:0.25,trio:0.25,trifecta:0.275;series=prospective-primary]
place REJECT 685 23 0.10587347931873481 ci=[-0.01151227650290571,0.22738320829256958] 0.132
quinella REJECT 690 23 0.21934541062801935 ci=[-0.21183322653809245,0.7259867921369126] 0.386
wide REJECT 691 23 0.10886396526772794 ci=[-0.24857669935207677,0.4888306140068639] 0.386
exacta NO_DECISION 691 23 0.8959479015918959 ci=[None,None] None
trio NO_DECISION 691 23 -0.04122286541244574 ci=[None,None] None
trifecta NO_DECISION 691 23 -0.34885672937771345 ci=[None,None] None
```

## 主判定(事前登録した規則どおり)

| 券種 | n(gate の数え方=レース数) | n_min | vs lowest_oest | vs uniform | 判定 |
|---|---|---|---|---|---|
| place | 685 | 500 | −0.0093 CI[−0.112,+0.105] p_raw 0.571 | +0.106 CI[−0.012,+0.227] p_raw 0.044・Holm(m=3) 0.132・Holm(m=6) 0.264 | **REJECT** |
| quinella | 690 | 500 | +0.019 CI[−0.446,+0.687] p_raw 0.530 | +0.219 CI[−0.212,+0.726] p_raw 0.193 | **REJECT** |
| wide | 691 | 500 | −0.017 CI[−0.303,+0.337] p_raw 0.574 | +0.109 CI[−0.249,+0.489] p_raw 0.279 | **REJECT** |
| exacta | 691 | 700 | (+0.354・CI なし) | (+0.896・CI なし) | NO_DECISION(9 不足) |
| trio | 691 | 1000 | (−0.190) | (−0.041) | NO_DECISION |
| trifecta | 691 | 1500 | (−0.354) | (−0.349) | NO_DECISION |

§9 の OOS ガード(モデルの学習終端 2026-08-16 より後の 15 開催日・408 レース)でも、lowest_oest に対して全券種で点推定が負:
place −0.109 CI[−0.191,−0.025]、quinella −0.491、exacta −0.241、wide −0.324、trio −0.450、trifecta −0.537。

## 読む前に知っておくべきこと

1. **窓の 41%(07-25..08-16 の 8 開催日・284 レース)はこのモデルの学習期間内**。gate は保存済みの買い目を読まず、測定時に 1 つのモデルで全レースを予測し直す。
   学習期間内では EV 選抜が両 baseline を上回る(place の EV 回収 1.006・exacta 1.959)。学習期間外では大きく下回る(place 0.705・exacta 0.448)。
   baseline の的中率は 2 つの期間でほぼ同じ(place lowest_oest 42.7%→42.5%)なので、時期の違いではなく学習済みレースの記憶とみられる。
2. 診断記録 1(補系列 2025-01-05..2026-07-19・place +0.101 vs 本命筋)も lgbm-094-cap900 で計算されており、窓全体がその学習期間内だった。in-sample の数値として読み替えるべき。
3. 実装の n は「モデルと baseline の両方がその券種を買ったレース数」で、§5 本文の「scored bet 数」と違う。本文どおり bet 数で数えると exacta/trio/trifecta も n_min を越えるが、3 券種とも上記の OOS ガードで落ちる。
4. 実装の Holm は判定対象の券種だけを family にする(今回 m=3)。本文は全券種×窓(m=6)。どちらで数えても判定は変わらない。
5. 精算は実配当(exotic_odds)で、的中の 100% が実配当で精算された(推定オッズへのフォールバックは 0)。選抜には保存済みの単勝オッズを使っている(モデルと baseline で共通)。
6. 9/6 の 36 レースは全て採点に入っている(gate は買い目を予測し直すので、保存済み買い目が無かった 29 レースも欠けない)。

詳細: artifacts/roi_explore/missed_20261004/R03_080_gate/diag_report.json・per_race_strategy.parquet・roi_model_in_sample.json、スクリプト scripts/roi_explore/missed_20261004/r03_080_gate_diag.py(CLI の n・点推定・CI・p を完全に再現)。
