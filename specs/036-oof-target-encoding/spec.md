# Feature Specification: OOF Target Encoding + isotonic 校正 (Modeling)

**Feature Branch**: `036-oof-target-encoding`

**Created**: 2026-06-30

**Status**: Implemented

**Input**: モデリング変更(新データ・新特徴なし)。高カーディナリティ categorical(jockey_id/trainer_id)を OOF target encoding でクリーンな数値に変換 + 校正を platt→isotonic に。FEATURE_VERSION 不変(features-011)、スキーマ変更なし。

## 概要・動機

030-035 で公開情報・交互作用は逓減フェーズ(積み上げ −0.0009)に見えたが、**持っているデータを過小活用**していた: jockey_id/trainer_id は数千水準の高カーディナリティで、LightGBM の native categorical 処理が苦手。これを平滑化 **OOF target encoding** でクリーンな数値に変換すると木が一気に学習でき、大幅改善。加えて校正を platt→isotonic にすると(900k 行で柔軟に校正曲線を fit)無トレードオフで LogLoss/ECE が大改善。

既存 infra(020 の `TargetEncoder`/`oof_target_encode`/`fit_target_encoder`、predictor の `target_encode_cols`/`te_smoothing`)を利用。新 infra 不要。

## User Scenarios & Testing

### US1 - OOF TE 評価 (P1)
`model-eval --target-encode jockey_id,trainer_id` で TE candidate vs no-TE baseline を walk-forward OOS 比較(同一 feature 列=FEATURE_VERSION 不変)。

### US2 - isotonic 校正 (P1)
calibration=isotonic で baseline/candidate を校正。platt より OOS LogLoss/ECE が改善。

### US3 - 採用 (P1)
isotonic + TE(jockey_id, trainer_id, smoothing 50)の lgbm-036 を再学習し、**生産 lgbm-033(platt, no-TE)を全 OOS 指標で上回る**ことを確認 → active 昇格。

## リーク安全(憲法 II, 非交渉)

- OOF TE: training 行は OOF(自分のラベルを見ない=他 fold のみ、chronological_race_folds)。eval 行は **training のみで fit した最終 encoder** で変換(walk-forward で strictly-before)。harness は fold 毎に train のみで再学習(`predictor.fit([fold.train])`)。
- 傍証: TE 後でも LogLoss 0.218 で市場 0.202 に依然負け(リークなら市場を楽に超え AUC>0.9 になる)= 本物。
- TE 値/校正は predict のみで使い、特徴量にフィードバックしない(p≠q 不変)。

## Requirements

- **FR-001**: `model-eval` CLI で TE candidate(target_encode_cols, te_smoothing, calibration)vs no-TE baseline を adoption gate で比較。
- **FR-002**: train-evaluate に --te-smoothing 追加。calibration は既存 --calibration(platt/isotonic)。
- **FR-003**: FEATURE_VERSION 不変(features-011)・スキーマ変更なし。TE は predictor 内部変換=serving feature_hash を壊さない。
- **FR-004**: 採用は walk-forward OOS で生産モデルを上回ること(LogLoss 改善 + ECE 非悪化 vs 生産)。

## Success Criteria

- **SC-001 (リーク)**: harness 再学習が fold 毎 train-only、TE encoder training-only(既存 infra、テスト済)。市場非超過の傍証。
- **SC-002 (改善)**: 18 fold OOS で lgbm-036(isotonic+TE)が lgbm-033(platt,no-TE)を LogLoss(0.232→0.218)・AUC(0.752→0.790)・ECE(0.0088→0.0040)全てで上回る。
- **SC-003 (serving 安全)**: FEATURE_VERSION 不変 → feature_hash 整合、serving が lgbm-036 をロード。
- **SC-004 (透過)**: training lint/test 緑。

## 結果(18 fold OOS）

| | lgbm-033(現prod) | isotonic のみ | lgbm-036(isotonic+TE) |
|---|---|---|---|
| win LogLoss | 0.23187 | 0.22489 | **0.21847** |
| AUC | 0.752 | 0.766 | **0.790** |
| ECE | 0.00878 | 0.00203 | 0.00401 |

isotonic 単独=無トレードオフの校正改善、TE=識別力(AUC +0.023)。合計 LogLoss −0.0134・AUC +0.038・ECE も生産比改善。市場 gap +0.030→+0.016。

## Out of Scope / Deferred
- sire_name の TE(matrix 列でない=feature 変更が要る、別途)。
- venue_code TE(低カーディナリティ、native で十分)。
- HPO・条件別モデル(別レバー)。
- 035 lap 特徴(別ブランチ、full backfill 待ち)。
