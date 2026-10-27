# 152 再現手順

リポジトリ直下。既存142/143入力・360fit、147/148凍結依存と対照を必要とする。追加学習なし、既存protocol/comparisonを上書きしない。151には触れない。

```sh
training/.venv/bin/python -m pytest -q scripts/tests/test_roi_report_152.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_152/run.py freeze
training/.venv/bin/python scripts/roi_explore/roi_improvement_152/report.py
training/.venv/bin/python specs/152-normalized-pace/evidence/independent_audit.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_152/run.py verify
```

結果: artifacts/152-normalized-pace/comparison/report.md・summary.json。市場3列除去案は保留。
