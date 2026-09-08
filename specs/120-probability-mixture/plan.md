# 実施計画

1. 独立レビューで3構成・8比較・全head算術平均・旧100との別手続きを固定する。
2. 旧118までの来歴と完了結果を固定し、read-only factoryの有限混合をscripts内で実装する。
3. 部分平均・head演算変更・確率不正・品質・完全母集団・増分優先判断をテストする。
4. source/config編集を停止し、prepare→evaluate→summarizeを親が実行する。
5. 保存memberの確率から独立に再構成し、NLL・全品質・CI・群別集計・候補判断を照合する。

旧成果物とproduction packageには触れない。runtimeはtraining/.venv/bin/python、追加学習0。費用は新評価実時間と既存学習再利用を区別する。
