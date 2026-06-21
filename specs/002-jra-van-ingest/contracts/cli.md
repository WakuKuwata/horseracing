# Contract: 取込 CLI

`horseracing_ingest.cli` (argparse)。`DATABASE_URL` を環境変数から読む。

## コマンド

### `ingest-year <path>`
1 つの年ファイルを取込む。

- 引数: `<path>` = `raw_data/jra-van/<year>` のパス。
- 動作: ファイル名/中身から対象年を判定 → `is_in_ingest_scope` で 2007 境界判定 → パース → upsert →
  `ingestion_jobs` に記録。
- 終了コード: `0`=succeeded、`2`=partial (一部行エラー)、`1`=failed (致命的)、`3`=skipped (<2007)。

### `ingest-all <dir>`
ディレクトリ内の全年ファイルを年順に取込む。

- 引数: `<dir>` = `raw_data/jra-van/`。
- 動作: 各年ファイルを `ingest-year` と同じ処理。2006 以前は skip 記録。年ごとに `ingestion_jobs` 1 行。
- 終了コード: 全年 succeeded で `0`、partial 含むで `2`、致命的 failed で `1`。

## 不変条件

- 同一ファイルの再実行は冪等 (行数が増えない)。
- 不正行は黙って捨てず `ingestion_jobs.error_message` に行番号付きで残る。
- 2007 境界は `validation.is_in_ingest_scope` のみで判定 (独自日付比較を書かない)。
- 失敗後の再実行は checkpoint 以降を処理 (upsert なので重複無害)。

## 標準出力 (人間向けサマリ)

取込後に `{year, races, race_horses, race_results, skipped, errors}` の件数サマリを表示する。
