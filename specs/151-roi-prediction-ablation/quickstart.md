# 151 再現手順

リポジトリ直下、training/.venvの既存環境を使用。booster再学習・DB操作なし。

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 training/.venv/bin/python -m pytest scripts/tests/test_roi_prediction_ablation_151.py -q
training/.venv/bin/ruff check --select E4,E7,E9,F scripts/roi_explore/roi_prediction_ablation_151 scripts/tests/test_roi_prediction_ablation_151.py
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 training/.venv/bin/python scripts/roi_explore/roi_prediction_ablation_151/analysis.py verify
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 training/.venv/bin/python specs/151-roi-prediction-ablation/evidence/independent_audit.py --verify-existing
```

採点済み比較は上書きしない。新しい出力先で同じ条件を再計算する場合だけ、`analysis.py freeze --out ABSOLUTE_NEW_PATH`、続いて`analysis.py run --out ABSOLUTE_NEW_PATH`を実行する。元の固定入力/sourceが変わっていればverifyは失敗する。

入力検証・予測再構成は約1〜2分、係数15fitと採点は数秒。111 snapshot約1.1GiBを読み、実行時メモリ約3GiB、1process/各1thread。独立監査は151実装をimportせず保存結果を再計算する（係数再fitは対象外）。
