# 実施計画

1. 独立レビューで2追加fit・3seedごとの新旧係数・2比較・集計規則を固定する。
2. scriptsだけで115〜117来歴照合、旧原cacheへのread-only dispatch、2job固定、独立118cacheとreceiptを実装する。
3. 旧baselineの全レースreplayとper-race NLL一致、2構成smoke、2追加fit、seed別anchor gamma fitと6reportを用意する。
4. scope/seed/year/recipe/receipt/coefficient/hash/population/再開を対象とするテストを実施する。
5. 親の集計script・独立レビュー完了後source編集を停止し、親がprepare→smoke→train→evaluate→summaryを実行する。

実装担当は学習・評価を起動しない。training/.venv/bin/pythonと既存純関数を使用する。
