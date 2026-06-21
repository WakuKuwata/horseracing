# Quickstart: Core DB スキーマ検証

本 feature の成果物 (マイグレーション・制約・トリガ・バリデータ) が動作することを end-to-end で
確認する手順。実装詳細は tasks.md / 実装フェーズで埋める。

## 前提

- Python 3.12、`uv` または `pip`。
- Docker (testcontainers が使い捨て PostgreSQL を起動)。
- `db/` パッケージに依存をインストール済み (`uv sync` または `pip install -e db/`)。

## セットアップ

```bash
cd db
uv sync            # SQLAlchemy 2.0 / Alembic / psycopg3 / pytest / testcontainers
```

## マイグレーション適用・ロールバック (SC-005)

```bash
# テスト用 Postgres を起動 (例) し DATABASE_URL を設定してから:
alembic upgrade head      # 0001 を適用
alembic downgrade base    # 逆順 drop で空に戻る
alembic upgrade head      # 再適用が冪等
```

期待: いずれもエラーなく完了。`downgrade base` 後に全 13 テーブル・トリガ・関数が消える。

## 検証シナリオ (pytest)

```bash
cd db
pytest                     # 全テスト
pytest tests/integration   # 制約・トリガ (実 Postgres)
pytest tests/unit          # バリデータ
```

検証する受け入れ基準:

- **SC-001 / US1**: コア6テーブルが制約付きで作成され、不正値が拒否される。
  - `race_id='12345'` (11桁) → CHECK 違反で reject。
  - `race_number=13` → CHECK 違反で reject。
  - `(race_id, horse_id)` 重複 INSERT → upsert で行が増えない。
- **US2 / INV-2 / INV-4**:
  - `result_status='finished'` かつ `finish_order IS NULL` → CHECK 違反で reject。
  - 同一 `race_horses` の `odds` を 2 回更新 → 行は 1 つ、`updated_at` が進む (履歴なし)。
  - `race_date` 基準日より前のみの集計クエリで、基準日以降が混入しない。
- **SC-002 / INV-3 / INV-5**: 完走・取消・除外・中止・同着を含む結果から
  `result_status='finished'` のみでラベル (win/top2/top3) を導出し、非出走・非完走が除外される。
  標準ケースでレース内 1着1頭・2着以内2頭・3着以内3頭に一致。
- **SC-003 / US3**: `id_mappings` に未対応 (`mapping_status='unmapped'`, `canonical_id IS NULL`) を
  記録できる。同一 `(entity_type,source,source_id)` の重複は UNIQUE で拒否。衝突を `conflict` +
  `conflict_group_id` で表現できる。
- **SC-004 / US4**: 予測実行 + 馬別確率 + 推奨を 1 件保存。`race_predictions` の単調 CHECK
  (`0<=win<=top2<=top3<=1`) を違反する値は reject。`race_horses.odds` を後から上書きしても、
  `recommendations` の監査列だけで提示時点の判断根拠を再構成できる。
- **SC-006 / バリデータ**: `is_in_ingest_scope(date(2006,12,31)) is False`、
  `is_in_ingest_scope(date(2007,1,1)) is True`。

## レース内確率合計の検証 (参考クエリ, 下流責務)

行 CHECK では担保しない Σ確率は検証クエリで確認する (許容誤差は評価 feature で定義):

```sql
SELECT prediction_run_id,
       SUM(win_prob)  AS sum_win,    -- ≈ 1
       SUM(top2_prob) AS sum_top2,   -- ≈ 2
       SUM(top3_prob) AS sum_top3    -- ≈ 3
FROM race_predictions
GROUP BY prediction_run_id;
```
