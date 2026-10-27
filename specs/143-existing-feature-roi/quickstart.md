# 143 再現手順

全コマンドはrepository root、Pythonはtraining/.venv/bin/python。既存出力は上書きせず、途中再開時は既存identity/hashを検証する。

```sh
training/.venv/bin/python -m pytest -q scripts/tests/test_roi_features_143.py scripts/tests/test_roi_models_143.py scripts/tests/test_roi_report_143.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_143/features.py export --source artifacts/111-ability-observation/source-frames.pkl --rows artifacts/market_ev/rows_2007.parquet --output artifacts/143-existing-feature-roi/data
training/.venv/bin/python scripts/roi_explore/roi_improvement_143/run.py prepare-weights
training/.venv/bin/python scripts/roi_explore/roi_improvement_143/run.py freeze
training/.venv/bin/python scripts/roi_explore/roi_improvement_143/experiment.py features
training/.venv/bin/python scripts/roi_explore/roi_improvement_143/report.py features
training/.venv/bin/python scripts/roi_explore/roi_improvement_143/experiment.py weighted
training/.venv/bin/python scripts/roi_explore/roi_improvement_143/report.py weighted
training/.venv/bin/python scripts/roi_explore/roi_improvement_143/run.py verify
```

比較対象は2019〜2026-06-21、旧期間は学習履歴のみ。確定オッズ由来の回収率であり、現実の発走前購入成績ではない。出力のfeatures_comparison/weighted_comparisonにsummary.json、report.mdとensemble/race/bet parquetが保存される。
