# Contract: scripts/buy_pattern_gate.py

実行は `cd training && uv run python ../scripts/buy_pattern_gate.py <sub> …`(DB は `DATABASE_URL`、束は固定パス)。共通引数 `--spec-dir`(既定 `specs/109-buy-pattern-gate`。テストは一時ディレクトリに凍結物をコピーして指定する=本物の凍結物を改変せずに fail-closed を検証できる)。

| sub | 入力 | 出力 | fail-closed |
|---|---|---|---|
| `freeze` | gate-config(patterns_hash 空) | `patterns.json` 生成 → `patterns_hash`/`code_sha`/確認窓終端を転記した gate-config を書き、hash を表示 | dirty tree 拒否(`git status --porcelain` 非空。コードと spec 成果物は freeze 前にコミットする)。既存 patterns.json は拒否 |
| `selftest` | gate-config hash | `evidence/selftest.json`(`run_code_sha` / dirty フラグ / 所要時間を含む) | hash 不一致拒否。サイズの二項片側 95% **下側**限界 > 2.5% なら exit 2(`passed=false`)。smoke では `smoke=true` を書き `passed` は常に true |
| `screen` | gate-config hash | `population.json`(`rows_hash`・`day_universe`)・`artifacts/109/rows-*.parquet`・`evidence/screening-{discovery,qualification}-summary.json`・`artifacts/109/screening-*-bets.parquet`・`survivors.json` | hash 不一致拒否。selftest 未通過(`passed=false`)なら拒否。**非 smoke は clean tree 必須**(selftest.json をコミット後)。母集団の年別件数は `expected_races_per_year` との差を記録するのみ。selftest が記録した全パターンの `selection_hash` と一致しなければ拒否 |
| `confirm` | gate-config hash + survivors hash | `evidence/confirmatory-*`・`verdict.json` | 4 hash 照合。**非 smoke は clean tree 必須**(screening 生成物と survivors.json をコミット後=生存者集合が git に固定されてから確認窓が走る)。行は `artifacts/109/rows-confirmatory.parquet` を `screen` 時に固定したものを読む(`rows_hash` 照合)。**対照 4 本は生存者数に関わらず確認窓で集計**(SC-002)。survivors 0 本なら生存者行なしの verdict を書く。verdict 既存なら拒否 |
| `recompute` | window | 一致 assert | 不一致は exit 1 |

順序の強制: `freeze → selftest → screen → confirm`。各段は前段の生成物の hash を読む。`--smoke` は窓を 2010 年の 1 か月に縮め反復数を 200 に落とし、**効果数値を出力から機構的に redact**(107 と同型・差の存在だけ表示)。`--smoke` は `--spec-dir` が既定の本物のディレクトリを指す場合は拒否する(凍結物の汚染防止)。`--smoke` かつ非既定 `--spec-dir` のときのみ `freeze / screen / confirm` の dirty-tree 検査を免除する。全サブコマンドは実行時の `git rev-parse HEAD` と dirty フラグを出力に `run_code_sha` / `run_tree_dirty` として記録する。CLI 統合テストは `scripts/tests/test_buy_pattern_gate_cli.py`(実 DB `localhost:15432` と束が無ければ skip・eval の testcontainer conftest は使わない)。
