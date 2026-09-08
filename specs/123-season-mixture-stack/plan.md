# 実施計画

1. 119/120の完了・独立監査結果、係数、全レポートとreceiptを固定する。
2. 凍結済119のjoint factoryと120の全head平均を再利用し、新規学習0で2候補・6比較を実装する。
3. 係数再推定のない組み合わせ、完全母集団、既存baselineの各行一致、品質不足の明示、判断境界をテストして独立レビューする。
4. 親がprepare→evaluate→summarizeを実施する。source/configはprepare後変更しない。
5. 独立数値監査、実測費用、結果・残余リスクを記録する。

Runtime: training/.venv/bin/python。既存110〜120は読取専用。123だけに成果物を追加する。
