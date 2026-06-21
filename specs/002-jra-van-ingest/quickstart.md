# Quickstart: JRA-VAN 取込の検証

実装後に取込が end-to-end で動くことを確認する手順。実装詳細は tasks.md / 実装フェーズ。

## 前提

- Feature 001 (`db/`) が適用済みの PostgreSQL (`DATABASE_URL` 設定済み)。
- Docker (テストの testcontainers 用)。
- `ingest/` パッケージの依存をインストール (`uv sync`、`horseracing-db` にパス依存)。

## セットアップ

```bash
cd ingest
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 001 の migration 適用済み DB
```

## 実データ取込 (ローカルスモーク)

```bash
# 1 年だけ
uv run python -m horseracing_ingest ingest-year ../raw_data/jra-van/2007
# 全年 (2006 以前は自動 skip)
uv run python -m horseracing_ingest ingest-all ../raw_data/jra-van
```

期待: 取込後サマリ `{year, races, race_horses, race_results, skipped, errors}` が表示され、
`ingestion_jobs` に年ごとの行が残る。`ingest-all` で 1986-2006 は skipped に計上。

## テスト (golden fixture + testcontainers)

```bash
cd ingest
uv run pytest                    # 全テスト
uv run pytest tests/unit         # parser / mapping / raceId / venue / status (DB 不要)
uv run pytest -m integration     # 取込→実 Postgres
```

検証する受け入れ基準:

- **SC-001 / US1**: 2007 golden fixture から期待件数 (races/race_horses/race_results) が入る。
- **SC-002 / US2**: 取消/除外/中止/失格/同着を含む fixture で、entry_status/result_status が期待通り、
  `labels.derive_labels` が finished のみを返す。取消・除外は race_results 行なし (INV-1)。
- **SC-003**: 同一 fixture の再取込で行数が増えない (冪等)。
- **SC-004**: 2006 fixture が skip され、コアデータに 1 行も入らない。
- **SC-005**: 列数≠73・cp932 デコード不能・raceId 不正・未知状態の行が `ingestion_jobs` に行番号付きで
  記録され、黙って捨てられない。
- **SC-006**: (ローカル) 実データ全年取込で年ごとの取込件数が `ingestion_jobs` に残る。

## ラベル整合の確認 (取込後)

```sql
-- finished のみが教師化される (取消/除外/中止/失格は除外)
SELECT result_status, count(*) FROM race_results GROUP BY result_status;
-- 非出走 (取消/除外) は race_results に存在しない
SELECT entry_status, count(*) FROM race_horses GROUP BY entry_status;
```
