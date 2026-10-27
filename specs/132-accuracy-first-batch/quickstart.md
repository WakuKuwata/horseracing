# Validation guide

runtime=training/.venv/bin/python。9/6以前と証明できる固定保存物のみ。

1. scripts/testsの新規5moduleテスト。欠損窓・cutoff・確率・係数年・全既知parity。
2. accuracy_style_recheck.py監査でtoken/変更セル/他列一致を保存。
3. accuracy_topk_recheck.py,accuracy_colsample_recheck.pyで追加fit0実測。
4. style smokeとbaseline同一性確認後、2年の本比較（最大2worker各1thread）。
5. result-review.mdに全案/期間/費用/不確実性と独立検査を記載。

実コマンドは完了時にresult-review.mdへ残す。
