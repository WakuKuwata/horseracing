# 実施計画

1. 独立レビューを受け、3arm・2contrast・研究進行条件を固定する。
2. 110/111の凍結と実行環境を確認し、全snapshot・recipe・cacheの同値証明を作る。
3. 113専用driverを実装・境界テスト・独立レビュー後にfreezeする。既存学習器とpaired_evalを再利用する。
4. 3armの小規模smokeを完了する。
5. stackの2019〜2026の8foldを2並列で新規学習する。anchor/pruningは検証した旧予測を使用する。
6. stack−pruning、stack−anchorを数値ゲートと研究判定へ通し、直近/部分集団・来歴を独立確認する。
7. 結果と実行時間を記録し、次のseed確認または追加候補への進行を判断する。

Python3.12、training/.venv、scriptsのみ。モデル/DB/特徴定義に変更なし。過去入力を固定して比較する研究であり、本番の採用確認ではない。
