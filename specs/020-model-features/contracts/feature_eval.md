# Contract: 特徴量拡張の学習・評価（CLI / features+training+eval）

新特徴を registry に登録し、fold 内選択の walk-forward で baseline と比較、改善時のみ採用。スキーマ変更なし。

## コマンド（例）

```
# 新特徴込みで walk-forward 評価（fold 内選択、baseline=現行モデル）
eval feature-eval --from <date> --to <date> [--feature-version features-005] [--seed 42]
# group ablation
eval feature-ablation --from <date> --to <date> --groups recent_form,aptitude,race_condition,human_form
# SECONDARY diagnostic
eval feature-diagnostic --from <date> --to <date>   # pseudo-ROI/Kelly + market q edge
```

## 新特徴（registry 登録、leak 安全）

§data-model §2 の 9 特徴を `registry.REGISTRY` に FeatureMeta + group で登録。各々:
- as-of/out-of-fold/同日除外（`_cumulative_before` 同型の daily cumsum−当日、または merge_asof backward+exact 無し）
- 跨馬（jockey/trainer）は jockey_id/trainer_id でグルーピング＝対象行+同日除外
- Unknown=NULL（0 代入しない）。cutoff test + 跨馬は target-row 除外 test。

## 評価フロー（候補固定 + fold 内ハイパラのみ）

1. **候補特徴集合を事前固定**（既存 + 新規9）。OOS を見て特徴を選ばない。
2. walk-forward 各 fold: 学習窓を inner train/val に分割し、**ハイパラ・early stopping のみ**を inner で完結
   （検証 fold ラベル不使用）。
3. fold test（OOS）で「固定候補集合 vs baseline」の LogLoss/Brier/AUC/ECE + diff。
4. 全 fold 集計: 平均 + fold 別差分（勝ち fold 数・最悪 fold・ECE 差分）。
5. group ablation（4 group 分離、**diagnostic**）+ fold 安定性（gain/SHAP 符号・順位）。
6. 採用時は同一固定集合を全体再学習（評価モデル＝デプロイモデル一致）。

## 採用ゲート（AdoptionReport）

- PRIMARY: **LogLoss 改善 かつ ECE 非悪化**（Brier 非悪化が望ましい、AUC 順位説明限定）。
- + fold 別差分 OK（最悪 fold 非悪化・勝ち fold 多数）+ 過学習検査 OK（特徴数上限/正則化/安定性）。
- 未達なら不採用（false positive を出さない）。adopted=true のときのみ全体再学習で固定特徴セット採用。

## SECONDARY（diagnostic、非ゲート）

pseudo-ROI/Kelly（高分散）+ 市場 q edge（p−q calibration / edge bucket 実現勝率 / q 条件付き LogLoss）。
成功基準は OOS win 改善（市場超過は努力目標）。

## 不変条件

- 全特徴 as-of/out-of-fold・同日除外・跨馬は対象行除外。**候補特徴は事前固定で OOS で選択しない**（選択リーク
  無し）、fold 内はハイパラ/early-stopping のみ。market odds/結果は特徴にしない。win→joint(009) 維持。決定論
  （seed）。スキーマ変更なし。importance 単独で採否を決めない。
