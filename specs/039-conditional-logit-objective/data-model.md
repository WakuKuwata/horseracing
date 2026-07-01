# Data Model: Conditional-logit 目的関数 (039)

**スキーマ変更なし**(migration head 不変)。本 feature は学習設定と実行時の中間表現のみ。DB エンティティの追加・変更はゼロ。

## 概念エンティティ(コード内、非永続)

### Objective(目的関数)
- **表現**: 文字列 `"binary"`(既定)| `"cond_logit"`。WinModel / LightGBMPredictor / cli の設定値。
- **意味**: 学習の損失・勾配定義。binary = 独立 P(win) 二値交差エントロピー。cond_logit = レース内 softmax の −log p_winner(Plackett-Luce top-1)。
- **不変条件**: cond_logit は fit/predict/serving の全入口で group(race_id 配列)入力を要する。binary は group 不要(後方互換)。

### Race group(レース group)
- **表現**: 学習/校正行に対応する race_id 配列(X 行順)。cond_logit の softmax 正規化単位。
- **由来**: race_id のみ(結果 finish_order/result_status を参照しない=リーク境界)。
- **不変条件**: 同一 race_id が model/calib split や OOF fold 境界を跨がない(race 単位 split で保証、assert)。sum(y)=1 の group のみ学習に寄与(勝ち馬不在・同着は grad/hess=0 で中立化)。

### model_version `lgbm-039`(採用時のみ)
- **表現**: 既存 `model_versions` 行(スキーマ不変)。
- **フィールド**: model_family=lightgbm、feature_version=features-011(不変)、objective=cond_logit(metrics_summary/metadata に記録)、adoption_status、weights_uri/calibrator_uri、metrics_summary(採用ゲート結果 + objective + postprocess + calibration 種別)。
- **artifacts**: `../artifacts/model_versions/lgbm-039/` に model.txt(booster raw)/ calibrator.pkl / preprocessor.pkl(encoders + feature_hash + objective + postprocess)/ metadata.json。

## 中間表現(学習時)

| 名前 | 型 | 説明 |
|---|---|---|
| group_sizes | list[int] | レース連続整列後の各レース行数(cond_logit softmax 単位) |
| raw margin | np.ndarray | booster.predict(raw_score=True)、cond_logit の softmax 前スコア s_i |
| softmax prob | np.ndarray | レースごと exp(s_i)/Σ exp(s_j)、cond_logit の生 win 確率 |
| calibrated prob | np.ndarray | (採用校正経路)softmax→isotonic、009 前 |
| final prob | np.ndarray | 009 で clip→Σ=1 再正規化した各馬 win 確率(binary/cond_logit 共通の最終出力) |

## リーク境界(憲法 II)

- 特徴列: features-011 で完全に同一(新特徴ゼロ)。odds/payout/dividend トークンを含まない(既存 leak-guard 不変)。
- group: race_id のみ依存。勝敗ラベル y は損失計算のみに使用(特徴・group に非流入)。
- as-of / OOF TE / chronological fold / training-only encoder は 036 と不変。
