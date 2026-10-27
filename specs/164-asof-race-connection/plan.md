# 164 実装・検証計画

Python3.12 / training/.venv、既存pandas/pyarrow/LightGBM。新しい外部依存/DB変更なし。163の実モデル、時計、公式会計の純部品と162の排他的publish/readを再利用する。

- `sources.py`: 133実保存の138特徴/履歴/元時計/原本と内部digestを抽出。MEV資料はschema-onlyの時刻証拠不足台帳。
- `guards.py`: raw capture/HTML/query packetから発走前/馬集合/結果未確定の範囲を導出し、partialとstrictを分ける。
- `features.py`: 元source/pin/全列/全行/履歴のas-of・durable時刻を検査し実モデル入力へ結合。
- `contract.py`: quote30分、工程間の因果順、両実予測保存、source/消費行列の結合を検査。
- `store.py`: candidate journal、現在の診断再推論、過去への新規封印拒否、現在の完全packetの封印/再現/公式改訂。
- `settlement.py`: 公式単勝額のみの会計規則をnewmodeで維持。
- `run.py`: 全46実保存の台帳付き検証、旧実入力再推論、過去9公式会計の別replay、manifest/再検証。

完成成果物の検証:

```sh
training/.venv/bin/python scripts/roi_explore/asof_connection_164/run.py verify --out artifacts/164-asof-race-connection/run-02
```

新しい出力先での再実行:

```sh
training/.venv/bin/python scripts/roi_explore/asof_connection_164/run.py rehearse --out /tmp/horse-asof-164
training/.venv/bin/python scripts/roi_explore/asof_connection_164/run.py verify --out /tmp/horse-asof-164
```

主要テスト:

```sh
training/.venv/bin/python -m pytest scripts/tests/test_asof_connection_164.py scripts/tests/test_asof_sources_164.py scripts/tests/test_asof_guards_164.py scripts/tests/test_asof_review_164.py -q
```

合成完全packetは実21member/実時計を使用して全gateの正例・封印/再現を試す。実保存46件は別cohortとして元時刻・不足を保持する。新規収集/DB/実query実行は`connection_disabled`内で禁止。

Constitution Check: 12桁ID/全頭一致、2007以降strictpast、旧市場非入力/preweight、as-ofと耐久保存分離、固定モデル/係数年、1race1判断の使用証跡、公式精算分離、手動offlineのみ。独立source/guard/設計レビューと別実装での全46packet/時計/旧6本体/公式9会計検算を完了条件にする。
