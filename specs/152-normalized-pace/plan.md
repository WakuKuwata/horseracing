# 152 実装計画

2026-10-25完了。展開完全正規化114.06%・利益614,820円。同親raw比+84,230円だが③完全正規化比−351,370円。新fit0、独立監査合格、市場3列削除案2保留。本番変更なし。[result-review.md](result-review.md)参照。

Python3.12 training/.venv、既存numpy/pandas/LightGBM。152新規研究CLI/成果物のみ追加。151は初期状態に存在する別研究のため使用せず、142〜151凍結物と共有変更を保護。

1. run.pyは凍結147runのverify/referencesからbaseline77・元③・重みなしpace91の360fitを復元。receiptとモデル/予測/特徴schema1080SHA、過去年次学習・各特徴レシピ・300round・15seed平均を確認。
2. 凍結148runのverifyと③完全正規化の保存予測を確認。raw③からp/Sを再構築して完全一致、残る各controlも保存ensembleと完全一致。
3. 候補採点前に147+148の全source/input依存、5対照の予測/買い目/レース表とsummary、152source、提案文書、4比較/予算/感度をprotocolへ固定。
4. report.pyから凍結148normalize・paired・sensitivity・143scoreを再利用。candidate=normalize(pace)のみ。全6条件を採点し、5control全指標・全買い目を既存保存物と厳密比較する。
5. 独立監査は152実装関数を使わず143独立helperから360fitを再構築し、fieldloopでγfullnorm/pacefullnormを導出。全6政策/予測/買い目/年別/リスク/4CI/感度を照合。9固定保存本体×64頭を再生する。
6. 結果レビューに両目的（同親改善/最良既存比較）と不確実性を記録。案2市場3列削除は保留、本番変更なし。

## Constitution check

2007以降・12桁raceID・完全field・前年末cutoff・train-onlyカテゴリ・15seedを維持。全807日と非購入日を含める。候補は採点前固定。確定オッズ仮想精算、既参照期間、保存seed条件付きbootstrapと反復選択の限界を明記。新サービス不要、追加学習0。
