# 144 再現手順

Repository rootからtraining/.venv/bin/pythonを使用する。既存出力は上書き禁止。途中再開時はexperiment.pyに--resumeを付け、実入力・source・重みと保存receiptの一致を必須にする。

```sh
training/.venv/bin/python -m pytest -q scripts/tests/test_roi_models_144.py scripts/tests/test_roi_report_144.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_144/run.py freeze
training/.venv/bin/python scripts/roi_explore/roi_improvement_144/experiment.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_144/report.py
training/.venv/bin/python specs/144-race-normalized-weight/evidence/independent_audit.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_144/run.py verify
```

固定studyroot: artifacts/144-race-normalized-weight。comparison/summary.json・report.mdに結果と年別・4比較・既知/最大差レースの感度診断を保存。確定オッズ由来の仮想精算であり、発走前の購入利益ではない。
