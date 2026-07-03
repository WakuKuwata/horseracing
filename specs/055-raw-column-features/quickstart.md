# Quickstart: JRA-VAN 生データ未使用カラムの活用 (055) 検証ガイド

前提: ローカル Postgres(`DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`)、raw_data/jra-van/2007..2025。

## 1. Migration + 1 年スモーク(US1)

```bash
cd db && DATABASE_URL=... uv run alembic upgrade head   # 0010
cd ../ingest && DATABASE_URL=... uv run horseracing-ingest ingest-year ../raw_data/jra-van/2024
```

期待: 新列が populate(first_3f カバレッジ ~96%・他 ~100%)、既存列は同値上書き(行数不変・冪等=再実行で同一)、ingestion_jobs 記録。

```sql
select count(*) filter (where first_3f is not null)::float / count(*) from race_results r
  join races ra on ra.race_id=r.race_id where ra.race_date >= '2024-01-01';
select count(*) filter (where prize_money is not null) from races where race_date >= '2024-01-01';
```

## 2. 全期間 backfill(US1)

```bash
for y in $(seq 2007 2025); do DATABASE_URL=... uv run horseracing-ingest ingest-year ../raw_data/jra-van/$y; done
```

期待: 全年成功・既存行バイト不変(スポット検証は移行テストが担保)。

## 3. 特徴・パリティ・リーク(US2)

```bash
cd features && uv run pytest -q                        # 新群 unit + leak-guard + 冪等
DATABASE_URL=... uv run horseracing-features materialize   # fingerprint 更新で再生成
# パリティ(materialize == in-memory)は既存パリティテスト/CLI で bit 一致確認
```

期待: 新 11 列が生成・カバレッジ表示・leak-guard(今走/同日/未来の改変に不変)緑・パリティ bit 一致。

## 4. 採用ゲート(US3 — 事前登録)

```bash
cd training && DATABASE_URL=... uv run python -m horseracing_training.cli feature-eval
# 既定 drop_groups=055 新 4 群 → baseline=features-012 / candidate=features-013
```

期待: 18-fold のシリーズ標準判定(PRIMARY: win LogLoss 改善+mean ECE 非悪化 / strict majority / worst-fold ガード)。per-group は `feature-ablation --groups pace_first3f,owner_breeder,race_level,sire_line`(diagnostic)。

## 5. (採用時)再学習・昇格(US3)

```bash
DATABASE_URL=... uv run python -m horseracing_training.cli train-evaluate \
  --objective pl_topk --calibration isotonic --target-encode jockey_id,trainer_id \
  --baseline baseline-uniform-v1 --model-version lgbm-055
```

期待: 機械ゲート通過で lgbm-055 active・lgbm-042 retired・serving ロード(features-013)確認・予測整合性テスト緑。

## 6. 回帰

```bash
for p in db ingest features eval probability training serving betting; do (cd $p && uv run pytest -q); done
```

不採用時: 手順 5 を実施せず負結果を spec に記録。FEATURE_VERSION bump を含む変更は main へマージしない(ブランチ保全、035 前例)。
