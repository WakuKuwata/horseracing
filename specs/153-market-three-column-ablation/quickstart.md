# 153 再現手順

既存出力を上書きしない。中断時はrecipe/hash一致のexperiment.py --resumeのみ。

```sh
training/.venv/bin/python -m pytest -q scripts/tests/test_roi_models_153.py scripts/tests/test_roi_report_153.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_153/run.py freeze
training/.venv/bin/python scripts/roi_explore/roi_improvement_153/experiment.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_153/report.py
training/.venv/bin/python specs/153-market-three-column-ablation/evidence/independent_audit.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_153/run.py verify
```

summary/reportはartifacts/153-market-three-column-ablation/comparison/。本番変更なし、価格の時間別履歴取得は保留のまま。
