# 148 再現手順

リポジトリ直下、既存142/143入力と240fitを必要とする。追加学習なし。protocol/comparisonは排他的に生成し上書きしない。

```sh
training/.venv/bin/python -m pytest -q scripts/tests/test_roi_report_148.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_148/run.py freeze
training/.venv/bin/python scripts/roi_explore/roi_improvement_148/report.py
training/.venv/bin/python specs/148-postprediction-normalization/evidence/independent_audit.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_148/run.py verify
```

結果はartifacts/148-postprediction-normalization/comparison/report.mdおよびsummary.json。正規化は15seed平均の後かつEV購入判定の前。
