# 実装と再実行

Python3.12 / training/.venv。新規外部依存なし。標準ライブラリで契約・保存・HTML精算を実装し、既存pandasは過去159資料の件数/不足確認にだけ使用する。DB/UI/API/スケジューラは変更しない。

実装:

- `scripts/roi_explore/prospective_rehearsal_162/contract.py`: 固定モデル/係数/特徴列、時刻・全頭・quote・両予測の検査、両案の仮想判断。
- `store.py`: 排他的公開、各fsync/readback receipt、封印・中断/復旧・不採用整理、精算改訂。
- `settlement.py`: 公式HTMLの結果/単勝表と正規化資料の照合、未精算/同着/訂正会計。
- `fixtures.py`: 明示的合成入力/出力と公式形式の合成HTML。実モデル実行なし。
- `rehearsal.py`: offline-only CLI、全対象台帳、過去資料の不足確認、manifest検証。

実行例（新しい出力先のみ）:

```sh
training/.venv/bin/python scripts/roi_explore/prospective_rehearsal_162/rehearsal.py rehearse --out /tmp/horse-rehearsal-162
training/.venv/bin/python scripts/roi_explore/prospective_rehearsal_162/rehearsal.py verify --out /tmp/horse-rehearsal-162
```

完成成果物の確認:

```sh
training/.venv/bin/python scripts/roi_explore/prospective_rehearsal_162/rehearsal.py verify --out artifacts/162-prospective-offline-rehearsal/run-04
training/.venv/bin/python -m pytest scripts/tests/test_prospective_rehearsal_162.py scripts/tests/test_prospective_review_162.py scripts/tests/test_prerace_compare_158.py scripts/tests/test_prerace_fresh30_159.py scripts/tests/test_saved_timepoints_160.py -q
```

Constitution Check: 12桁raceID/全頭集合、両モデル固定、未来入力拒否、入力/出力/コードSHA、公式精算分離。1race1判断の使用入力証拠のみで汎用オッズ時系列を作らない。実データ収集/DB変更はない。全成果物をoffline/採用不可とする。独立設計/実装レビューと別実装の時刻・確率・買い目・会計検算を完了条件にする。
