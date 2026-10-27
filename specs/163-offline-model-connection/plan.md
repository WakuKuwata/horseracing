# 163 実装構成と検証手順

Python3.12 / training/.venv、既存numpy/pandas/LightGBM/scipy/sklearn。追加インストールなし。全実行は手動CLI。162と過去の凍結成果物を変更しない。

- `scripts/roi_explore/model_connection_163/models.py`: 実MEV15/旧6推論、typed consumed matrix、全member、履歴/補正、SHA binding。
- `quotes.py`: 常時停止fetchと純粋な保存原文/明示localfixture解析。
- `clock.py`: UTC wall+monotonic、逆行/drift検査。
- `contract.py` / `store.py`: 163の実時計/実モデル契約、publish/ready/decision/seal。排他的fsync/readback純helperだけ162から再利用する。
- `settlement.py`: 公式精算規則を別modeで保持。
- `replay.py`: 過去159公式会計だけの別replay。
- `run.py`: 新規収集/DB停止guard、実モデル接続、pending保持、source/output manifest、再推論検証。

新しい出力先で実行:

```sh
training/.venv/bin/python scripts/roi_explore/model_connection_163/run.py rehearse --out /tmp/horse-model-connection-163
training/.venv/bin/python scripts/roi_explore/model_connection_163/run.py verify --out /tmp/horse-model-connection-163
```

完成成果物:

```sh
training/.venv/bin/python scripts/roi_explore/model_connection_163/run.py verify --out artifacts/163-offline-model-connection/run-03
```

関連検証:

```sh
training/.venv/bin/python -m pytest scripts/tests/test_model_connection_163.py scripts/tests/test_model_connection_models_163.py scripts/tests/test_model_connection_quotes_163.py scripts/tests/test_model_connection_review_163.py scripts/tests/test_prospective_rehearsal_162.py scripts/tests/test_prospective_review_162.py scripts/tests/test_prerace_compare_158.py scripts/tests/test_prerace_fresh30_159.py scripts/tests/test_saved_timepoints_160.py -q
```

Constitution Check: 12桁ID/全頭一致、市場非入力の旧preweight、未来/後更新・古いquote拒否、モデル/係数年固定、1race1判断の使用証跡、公式会計分離、取引/DB更新/自動運用なし。独立設計・実装レビューと別実装の21本体推論/両腕判断/公式会計検算を完了条件にする。
