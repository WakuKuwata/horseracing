# 実施計画

1. 121独立数値監査PASSを完成し、A/Bだけが進行条件を満たしたことを保存する。
2. 独立レビューで二候補・両比較・平均品質・個seed硬い品質問題の分離・複数保持時の無順位を合意する。
3. 原121特徴・年別係数の読取とHarville三確率、旧基準replay、v4評価、保存検証、集計を新scriptへ実装する。
4. 候補選別、欠損、日付、係数、温度域、gamma0恒等性、微小確率、三head整合、actual paired_eval、NLL parity、全seed/品質判定、改変・再開をテストする。
5. 全変更停止と独立レビュー・親テスト後、親がprepare/evaluate/summaryを順に実行する。学習workerは追加しない。

API: load_inputs() → matrix/races/folds/lookup/population/audit。lookup値は(ISO日付,gap_log,prior_gap_log)。h_values(context,lookup) → 2配列。coefficients(seed,candidate) → 121保存COMPLETE結果のfolds等。Factory(base,lookup,candidate,coef) はReadOnly raw125に補正を付ける。verify() → cfg/frozen。verified_result(seed,candidate,contrast,cfg,frozen) はreport/evidence/receipt/121 parityを検証。verdict.candidates[candidate].stateはRETAIN/DEFER/REVIEW_REQUIRED、retained_candidatesに無順位で保持候補を示す。
