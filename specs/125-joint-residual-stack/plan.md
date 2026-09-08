# 実施計画

1. 124の保持状態からA/Bを選ぶ固定規則、最大12比較、単体/混合保持の分離を独立設計レビューする。
2. 原124のprior-gap入力と原119の季節式を同一snapshotで結合し、新joint係数だけを先行年fitする。原raw125と全比較基準はread-only工場を再利用する。
3. source/config、係数receipt/数値停止、全母集団と旧NLL一致、品質判断境界、全head平均、実paired_eval連携をテストする。実装者以外と親が最終レビューする。
4. 全編集停止後、親だけがprepare→evaluate→summaryを実行する。追加booster0、係数fitは3seed×7年。123/124完了の独立監査PASSが前提。
5. 保存係数/全レース/全head/CI/保持判定の独立数値監査、費用、結果と限定を記録する。

Runtime: training/.venv/bin/python。新規成果物はscripts/joint_residual_stack.py、対応tests、specs/125-joint-residual-stack、artifacts/125-joint-residual-stackのみ。
