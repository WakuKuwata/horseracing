# 実施計画

1. 親と独立レビューでseed schedule、2019 warmup、対応比較、集計の境界を固定する。
2. scriptsだけで115証跡検証、seed42レース単位の再現検査、30fitのjob固定と孤立cacheを実装する。
3. 4構成smoke、最大2workerの学習、厳密なread-only replay、各seed固有の先行年gamma fitと2比較を用意する。
4. scope/seed/receipt/cache不整合・再開・時系列・本番誤採用を対象とするテストを実行する。
5. source確定と親レビュー完了後、親がprepare→smoke→train→evaluateを実行する。親の別scriptでseed平均と品質を集計する。

training/.venv/bin/pythonを使用する。110〜115とpackage sourceは変更しない。実装担当は学習を起動しない。
