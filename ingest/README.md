# horseracing-ingest

JRA-VAN 過去データ (2007+) をコアテーブルへ取り込むパイプラインと CLI。
`horseracing-db` (スキーマ・enums・validation・labels) に依存する。

- 仕様: [specs/002-jra-van-ingest](../specs/002-jra-van-ingest/) (spec / plan / research / data-model)
- スタック: Python 3.12, SQLAlchemy 2.0, psycopg3 / 入力 = Shift_JIS CSV (73列, 1ファイル/年)

## セットアップ

```bash
cd ingest
uv sync                  # horseracing-db をパス依存で editable インストール
export DATABASE_URL=postgresql+psycopg://...   # 001〜0004 migration 適用済み DB
```

## 取込 (CLI)

```bash
# 1 年だけ
uv run python -m horseracing_ingest ingest-year ../raw_data/jra-van/2007
# 全年 (2006 以前は自動 skip、年順に処理)
uv run python -m horseracing_ingest ingest-all ../raw_data/jra-van
```

終了コード: `0`=成功 / `2`=partial(一部行エラー) / `1`=失敗 / `3`=skipped(<2007)。
取込後に `{year, races, race_horses, race_results, skipped, errors}` のサマリを表示し、年ごとに
`ingestion_jobs` 行 (件数列 + checkpoint) を残す。

## テスト

Docker が必要 (testcontainers が使い捨て PostgreSQL 16 を起動し、`db/` の migration を head まで適用)。

```bash
uv run pytest                 # 全テスト
uv run pytest tests/unit      # parser / mapping / status (DB 不要)
uv run pytest -m integration  # 取込→実 Postgres
```

## 設計の要点

- 列レイアウト・raceId 導出・venue 表・状態規則は [research.md](../specs/002-jra-van-ingest/research.md) /
  [data-model.md](../specs/002-jra-van-ingest/data-model.md) を正本。
- 2007 境界は `horseracing_db.validation.is_in_ingest_scope` が唯一の正本。
- 状態正規化は finished / DNF / DNS の 3 区分を保証 (取消/除外 vs 中止/失格 の細分は best-effort)。
  未知状態は黙って finished にせずエラーとして `ingestion_jobs` に記録する。
- JRA-VAN のオッズ・人気は「結果確定時」値。`race_horses` に保存するが発走前特徴量には使えない
  (リーク防止は特徴量フィーチャーで強制)。
- 取込は冪等 (PostgreSQL `ON CONFLICT DO UPDATE`、PK 上)。
