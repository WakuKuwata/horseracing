# 175 contracts — CLI

## `live near-post-score`

```
python -m horseracing_live near-post-score --from YYYY-MM-DD --to YYYY-MM-DD
    [--candidate NAME ...] [--candidates PATH] [--features-dir DIR]
    [--ensemble-dir DIR] [--single-dir DIR] [--retention-replay]
    [--out PATH] [--database-url URL]
```

- 読み取り専用(DB 書き込みゼロ・`hr_manual_observer` で実行可)。
- `--candidates` 既定 `specs/175-decision-quote-scoring/candidates.json`。`--candidate` 未指定なら候補なし(`verdict: no_candidate`)。
- `--features-dir` 既定 `artifacts/near_post`(`<day>/features-<sha12>.parquet` を探す。無い日は再構築し `features_source: rebuilt`)。
- `--out` 既定 `artifacts/near_post/score/<from>_<to>_<run_id>.json`。既存パスは拒否。
- 終了コード: 0=完走 / 2=拒否(凍結 JSON の sha 不一致・候補 artifact の sha 不一致・`--out` 既存・from>to・候補名未登録)/ 1=例外。
- 標準出力: 1 行目 `power_note`、funnel、列ごとの n_bets/recovery/ci、候補ごとの diff/ci/verdict、診断の要約。語彙は spec FR-020。
- 決定論: 同じ DB 状態・同じ引数で同じ JSON(`run_id`/`computed_at` を除く)。

## `live near-post-capture`(174・追加引数)

- `--features-dir DIR`(既定 `artifacts/near_post`): `build_day_features` 直後に `<day>/features-<sha12>.parquet` を書く。存在すれば書かない。書き込み失敗は警告のみで捕捉は続行。dry-run でも書く。
- 判定順・fetch・保存・終了コードは不変(174 contracts)。

## `scripts/near_post_ops.sh`

- `capture`/`morning`: `--features-dir "$ROOT/artifacts/near_post"` を付ける。
- 新規 `score <from> <to> [candidate...]`: 夜の results の後に実行。`--out` は `$ROOT/artifacts/near_post/score/`。
