# 153 実装計画

Python3.12 training/.venv、numpy/pandas/LightGBM/scipy、最大2worker×1thread、24GiB制約。

1. 元145の保存本体/receipt/再開検証を局所153へ移し、77行列から3列だけ除く。143重み関数をそのまま使用。追加展開データは親来歴のSHAにのみ残し、153入力ではjoinしない。
2. source/inputs/設計/独立事前レビューをprotocol凍結してから120fitsを実行。年別のweights全metadataとbyteSHAが元③の同seed/年と一致することを確認。
3. 148対照240fitを再検証・raw平均を再構築し、完全正規化対照を復元。保存全score/bets一致を確認。候補も15seed raw平均後に完全正規化。
4. 同一母集団・同policy・3固定比較/感度を保存。正規化後の候補だけを採点。
5. 74列順・元77行列slice一致、除去列を変えても行列不変、重みlabel非依存/未来OOF拒否、native LightGBM独立一致、保存再読・改ざん/再開拒否、全頭正規化/平均順/paired暦テスト。
6. 独立agentに事前実装レビュー、360fits1080artifactsと9本再生・全成績/CI独立再計算を依頼。結果を1回で確定し調整しない。

新規scope scripts/roi_explore/roi_improvement_153、scripts/tests/test_roi_{models,report}_153.py、artifacts/153-market-three-column-ablation、specs/153-market-three-column-ablation。既存151/152と他dirtyfiles変更禁止。
