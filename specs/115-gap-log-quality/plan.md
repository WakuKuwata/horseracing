# 実施計画

1. 独立レビューで113の分岐、113 cache key継承、gap補正、再clipを避けるeps=0方式、全v4品質評価を固定する。
2. scriptsのみでsource照合・freeze・係数fit・ReadOnlyFactoryラッパー・paired_eval・研究判定を実装する。
3. 数値・確率整合・時系列・選択規則・母集団・改変検知・研究誤採用の境界テストを行う。
4. 親レビュー完了と113全結果/summary保存後、親がprepareし、その後evaluateする。実装担当は実行しない。
5. 親が両比較と年別係数・品質条件を確認し、次の研究候補への優先度を更新する。

training/.venv Python、既存eval/training純関数を利用。114/113/111/110のsource/config/evidenceおよび本番モデルは不変。係数fitの数値失敗や証跡不一致では停止し、結果を見た設定変更は別研究にする。
