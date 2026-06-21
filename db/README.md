# horseracing-db

競馬予測システムの共有データパッケージ。スキーマ・Alembic マイグレーション・データ契約・
再利用バリデータを提供する。将来の api / training サーバーはこのパッケージに依存する。

- 仕様: [specs/001-core-db-schema](../specs/001-core-db-schema/) (spec / plan / data-model)
- スタック: Python 3.12, PostgreSQL 16, SQLAlchemy 2.0, Alembic, psycopg3

## セットアップ

```bash
cd db
uv sync                      # 依存 + パッケージを editable インストール
```

## マイグレーション

`DATABASE_URL` を設定して実行する (例: `postgresql+psycopg://user:pass@localhost:5432/horseracing`)。

```bash
export DATABASE_URL=postgresql+psycopg://...
uv run alembic upgrade head      # 0001 core / 0002 ingestion-id / 0003 prediction
uv run alembic downgrade base    # 逆順 drop
```

## テスト

Docker が必要 (testcontainers が使い捨て PostgreSQL 16 を起動)。

```bash
uv run pytest                    # 全テスト
uv run pytest tests/unit         # バリデータ (DB 不要)
uv run pytest -m integration     # 制約・トリガ・マイグレーション (実 Postgres)
```

## 構成

```text
src/horseracing_db/
  base.py         DeclarativeBase + 命名規約
  enums.py        状態コード体系 (entry/result/mapping/job/adoption status, source, bet_type)
  constraints.py  CHECK 式・制約名
  session.py      engine / session factory
  validation.py   再利用バリデータ (race_id 形式, 2007 境界)
  labels.py       status-aware ラベル導出
  sql/triggers.py updated_at 自動更新トリガ
  models/         core / ingestion / prediction の ORM モデル
migrations/versions/  0001_core_schema, 0002_ingestion_id_schema, 0003_prediction_contract
```

状態コード・制約・列定義の正本は [data-model.md](../specs/001-core-db-schema/data-model.md)。
2007 境界は `validation.is_in_ingest_scope` が唯一の正本 (スキーマに日付制約は持たない)。
