# 150 実装計画

2026-10-25完了。③固定100円114.75%・利益71,250円、完全正規化固定100円128.20%・利益116,340円。後者は従来配分比ROI+0.85ptだが実投資縮小で利益減。7比較の補正区間は0を含む。新fit0、独立監査合格、本番変更なし。[result-review.md](result-review.md)参照。

Python3.12 training/.venv、既存numpy/pandas/LightGBM。追加学習なし。150新規研究CLI/成果物のみ。既存142〜149の凍結物と共有変更を保護する。

1. run.pyは凍結149runのreferencesを再利用して148の4対照を復元。既存240fit・720artifact・15seed平均・77特徴・学習来歴を検証。148独立auditのsummary SHAとも照合。
2. 候補採点前に全依存・148の4対照予測/買い目/レース別損益・150source・7比較・固定100円・例示レースをprotocolへ凍結。
3. report.pyで143scoreを再利用し4対照を再採点。148summaryと全指標を厳密比較、保存買い目もindexを正規化して全列一致を検証する。
4. fixed_scoreは買い目をコピーしてunits=1/stake=100へ変更し、全共通race calendarへ集計する。元買い目キー・n_bets/n_hits/n_betting_races不変、max10・予算上限・未投資現金を検証。qualityは不変、policy/annual/riskだけ再計算。
5. 148paired/sensitivityで7比較を同開催日drawで再計算。固定100円同士の比較で賭金変更から正規化の選別差を分ける。
6. 独立監査は150実装importなしで143独立helperから親を再構築し、別のflat配賦/精算/年別/リスク/7CI/全感度/例示を照合。6固定保存本体×64頭を再生。結果レビューで投資規模差と未参照検証でない限界を明記。

## Constitution check

2007以降・12桁raceID・完全field・前年末cutoff・15seed平均を維持。候補に予測/選別変更なし。買い目固定後の配賦変更のみ。実投資ROIと共通予算利益・現金を区別。採点前固定・独立監査・確定オッズproxy・既参照期間の限界を維持。新サービス不要。
