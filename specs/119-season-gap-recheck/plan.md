# 実施計画

1. joint3項・raw125基準・2019warmup・成分欠測・全牝/全非牝の不変性を独立レビューで合意する（回答済み、阻害事項なし）。
2. source118完了証跡を結合し、pure candidate_matrix、vector係数fit、vector TiltFactory、旧baseline replayを119内に実装する。
3. prepareは性別群属性/被覆/識別性と旧baseline一致を固定する。追加boosterは一切fitしない。
4. vector shape/成分順/欠測/閏年/ゼロ係数/不変性/先行年/係数と結果receipt/完全再開をテストする。
5. 119集計scriptと独立レビュー完了後sourceを停止し、親がprepare→evaluate→summaryを実行する。

training/.venv/bin/pythonと既存純関数を利用する。旧source/成果物は変更しない。
