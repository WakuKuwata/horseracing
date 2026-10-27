# 145 再現手順

既存出力は上書きしない。中断再開はexperiment.py --resumeで全recipe/hash一致を必須とする。repository rootで実行する。

```sh
training/.venv/bin/python -m pytest -q scripts/tests/test_roi_models_145.py scripts/tests/test_roi_report_145.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_145/run.py freeze
training/.venv/bin/python scripts/roi_explore/roi_improvement_145/experiment.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_145/report.py
training/.venv/bin/python specs/145-pace-weight-combination/evidence/independent_audit.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_145/run.py verify
```

結果: artifacts/145-pace-weight-combination/comparison/summary.json・report.md。全期間と年別、3比較区間、既知高配当/最大差レース・同暦窓の診断を保存する。発走前の実行利益ではない。提案3は実行しない。
