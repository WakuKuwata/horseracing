# 実施計画

1. 2018 raw125基準と3候補・3/4 outer jobs・native cache再利用条件を独立レビューで確認（回答済み）。
2. scripts内に行列の11列追加、strict scope、元110 native cacheのreport結合証明、isolated fresh workersとreceiptを実装する。
3. 全共通値/カテゴリ/レース/foldの一致、F03置換・F05 residual除外/F02維持、数値型・時間境界・colsample実配線・tamper/再開・小差の進行判定をテストする。
4. 独立実装レビュー後source/config編集を停止する。親がprepareでsource-framesと新行列を固定し、smokeで4構成配線を検証する。
5. 親がtrain --workers 2、evaluateを順に実行。3比較の全結果を残して予備進行を記録し、次のfull研究は別途固定する。

CLI: training/.venv/bin/python scripts/market_feature_screen.py {prepare,smoke,train,evaluate}。trainの内部workerは独立process、学習器は1 thread。DB読取・package変更・旧cache変換なし。
