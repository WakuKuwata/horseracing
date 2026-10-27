# 149 再現手順

リポジトリ直下で実行。既存142/143入力・240fitと148凍結対照を使用する。新規学習なし。protocolとcomparisonは上書きしない。

```sh
training/.venv/bin/python -m pytest -q scripts/tests/test_roi_report_149.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_149/run.py freeze
training/.venv/bin/python scripts/roi_explore/roi_improvement_149/report.py
training/.venv/bin/python specs/149-half-normalization/evidence/independent_audit.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_149/run.py verify
```

結果: artifacts/149-half-normalization/comparison/report.md・summary.json。指数0.5固定、案2の固定100円配分は保留。
