# 146 再現手順

Repository rootでtraining/.venvを使用する。新規出力は上書き禁止。中断再開はexperiment.py各stageに--resumeを付け、全入力・レシピ・重み・保存hashの一致を必須とする。

```sh
training/.venv/bin/python -m pytest -q scripts/tests/test_roi_models_146.py scripts/tests/test_roi_report_146.py
training/.venv/bin/python scripts/roi_explore/roi_improvement_146/run.py freeze-mentor
training/.venv/bin/python scripts/roi_explore/roi_improvement_146/experiment.py mentor
training/.venv/bin/python scripts/roi_explore/roi_improvement_146/run.py prepare-weights
training/.venv/bin/python specs/146-old-oof-backfill/evidence/independent_audit.py --stage mentors
training/.venv/bin/python scripts/roi_explore/roi_improvement_146/run.py freeze-candidate
training/.venv/bin/python scripts/roi_explore/roi_improvement_146/experiment.py candidate
training/.venv/bin/python scripts/roi_explore/roi_improvement_146/report.py
training/.venv/bin/python specs/146-old-oof-backfill/evidence/independent_audit.py --stage final
training/.venv/bin/python scripts/roi_explore/roi_improvement_146/run.py verify
```

出力: artifacts/146-old-oof-backfill/。mentor_models、weights.parquet/weights_manifest.json、models、comparison/summary.json・report.md。初回教師の独立確認はaudit --stage first。確定オッズによる仮想精算であり、実際の発走前利益ではない。
