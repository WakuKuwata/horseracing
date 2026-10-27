# 147 再現手順

リポジトリ直下で実行。既存142/143入力と360fitを必要とする。新規学習なし。既存protocolやcomparisonを上書きしない。

```sh
training/.venv/bin/python -m pytest -q scripts/tests/test_roi_report_147.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_147/run.py freeze
training/.venv/bin/python scripts/roi_explore/roi_improvement_147/report.py
training/.venv/bin/python specs/147-fixed-half-blend/evidence/independent_audit.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_147/run.py verify
```

結果: artifacts/147-fixed-half-blend/comparison/report.md とsummary.json。案2は実行しない。
