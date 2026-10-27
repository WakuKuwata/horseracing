# 150 再現手順

リポジトリ直下。既存142/143入力・240fitと148対照、149の凍結依存を使用する。半補正候補は使用しない。新規学習なし、既存protocol/comparisonを上書きしない。

```sh
training/.venv/bin/python -m pytest -q scripts/tests/test_roi_report_150.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_150/run.py freeze
training/.venv/bin/python scripts/roi_explore/roi_improvement_150/report.py
training/.venv/bin/python specs/150-flat-stake/evidence/independent_audit.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_150/run.py verify
```

出力: artifacts/150-flat-stake/comparison/report.md・summary.json。同じ買い目の各馬100円、未投資現金を維持。
