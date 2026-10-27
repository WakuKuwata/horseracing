# 149 実装計画

2026-10-25完了。半補正118.01%・利益643,160円。現行を上回ったが元③より利益−20,570円、完全正規化より利益−323,030円。独立監査合格、新fit0、案2保留。本番変更なし。[result-review.md](result-review.md)参照。

Python3.12 training/.venv、既存numpy/pandas/LightGBM。追加学習なし。研究CLIと専用artifactのみ追加。142〜148凍結物と共有変更は保護する。

1. run.pyから凍結148runのverify/referencesを使い、原入力/sourceと全240receipt・720artifact SHA・過去年次学習・77特徴・BASE_PARAMS・300round・15seed算術平均を確認。
2. 完全正規化の対照をraw③から再計算し148保存値と完全一致を検証。freezeは148依存・protocol・対照成果物・149source・提案文書を候補採点前にSHA固定。
3. report.pyのhalf_correctは凍結148normalizeの完全field/確率/重複/正和検証後、p/√Sを作る。全頭分母・合計√S・有限[0,1]・親非破壊・seed平均順をテスト。
4. 143scoreを148report経由で再利用し5条件を採点。4対照は148summaryの全指標と厳密一致を要求。候補NLLは原③と不変。raw ECE等はスコア上の診断値として扱う。
5. 148のpaired/sensitivityを利用し、3比較・4000回・family3を明示して実行。独立監査は149実装importなしで143独立helperとfieldloopから全候補/対照・政策・年別・区間・感度を再計算、6固定保存本体×64頭も再生。
6. 年別結果・不確実性・リスク・提案2保留をresult-reviewへ保存。採用・本番反映は行わない。

## Constitution check

2007以降・12桁raceID・完全field・年次前年末cutoff・元15seedを維持。全807日と非購入日を含む。候補指数/政策/比較は採点前固定。予測後スコアの変更を学習精度改善と呼ばない。確定オッズ仮想精算・反復研究・未参照holdoutなしの限界を明記。独立設計意見にブロッカーなし。
