# Quickstart: 実 exotic オッズ取込と疑似→実 ROI 化の検証

実装後に「実 exotic オッズ取込 → 実 ROI 推奨/バックテスト → 推定 vs 実 乖離」が動くことを確認する手順。

## 前提

- Feature 008(netkeiba 取込)・009/010/011(確率・推定オッズ・exotic EV)が適用済み。
- `db` に `exotic_odds` マイグレーション(0005)適用。`scrape`/`betting` を拡張。
- 実 DB(2007+ データ、活性モデル)。

## セットアップ / マイグレーション

```bash
cd db && uv run alembic upgrade head    # 0005_exotic_odds を適用
cd ../scrape && uv sync
cd ../betting && uv sync                 # probability/db 依存は既存
export DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing
```

## 実 exotic オッズ取込(US1)

```bash
uv run python -m horseracing_scrape scrape-exotic-odds --race-id 200805030411 --database-url "$DATABASE_URL"
```

期待: 6 券種の実オッズが `exotic_odds`(selection=011 と同一 JSONB 安全配列、`UNIQUE(race_id,bet_type,selection)`)に格納され、
再実行で重複が増えず最新値に収束(履歴なし)。netkeiba ID は id_mappings 経由のみ、unmapped は `nk:` surrogate + 監査。

## 実 ROI 推奨/バックテスト(US2)

```bash
uv run python -m horseracing_betting exotic-recommend --race-id 200805030411 --run-id <id> --top-k 5
uv run python -m horseracing_betting exotic-backtest --from 2008-10-01 --to 2008-10-31 --top-k 2
```

期待: 実オッズのある組み合わせは `market_odds_used=実値`・`is_estimated_odds=false`・実 ROI、無い組み合わせは 011 推定
(二重疑似)にフォールバック。バックテストは実払戻と疑似払戻をラベル分離。推奨後取消は void/skip。

## 推定 vs 実 乖離評価(US3、評価先行)

```bash
uv run python -m horseracing_betting exotic-divergence --from 2008-10-01 --to 2008-10-31
```

期待: 券種別に coverage_rate・`log(実/推定)` の median/MAE/P90 が推定= baseline 明示で表示され、推定側は二重疑似ラベル。

## テスト

```bash
cd db && uv run pytest -m integration       # exotic_odds マイグレーション・UNIQUE・上書き
cd ../scrape && uv run pytest tests/unit    # fixture パーサ(6 券種、ネットワーク非依存)
cd ../scrape && uv run pytest -m integration # 実 DB upsert 冪等・id_mappings・ingestion_jobs 監査
cd ../betting && uv run pytest tests/unit   # selection 突合・実/推定フォールバック・実 ROI 採点・乖離・推奨後取消
cd ../betting && uv run pytest -m integration # 実 DB 推奨/バックテスト/乖離
```

検証する受け入れ基準:

- **SC-001**: 6 券種が同一 JSONB 安全配列 selection で格納、`(race_id,bet_type,selection)` で冪等(上書き、重複ゼロ、履歴なし)。
- **SC-002**: netkeiba ID は id_mappings 経由のみ、unmapped は `nk:` surrogate + 監査、guess-join ゼロ。
- **SC-003**: `exotic_odds.odds` は最新値で上書き(レース前=事前/確定後=最終配当)、updated_at のみ、履歴なし。決定時値は
  recommendations にスナップショット。
- **SC-004**: 実オッズは `market_odds_used`=実値・is_estimated_odds=false・実 ROI、無ければ 011 推定にフォールバック、selection
  完全一致で行単位区別。
- **SC-005**: バックテストが実払戻/疑似払戻をラベル分離、推奨後取消は void/skip。
- **SC-006**: 推定 vs 実 乖離(coverage_rate/log 比 median/MAE/P90)が券種別・レース単位で算出、推定= baseline ラベル分離。
- **SC-007**: exotic オッズがモデル特徴に一切使われない。取込・突合・評価は決定論・監査可能。

## 核心の考え方(リーク境界 / 憲法 V)

exotic オッズは**市場データ**でモデル特徴にしない(win オッズと同一)。`exotic_odds` は**単一最新値 + updated_at**(履歴なし、
憲法 V)で、レース前は事前オッズ・確定後は最終配当に上書き。推奨は決定時オッズを recommendations にスナップショットして監査。
実オッズで 011 の二重疑似が**単一評価(実 ROI)**に格上げされ、欠損は推定にフォールバックしてカバレッジを明示する。
