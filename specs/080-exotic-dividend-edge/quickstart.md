# Quickstart / Validation: Real Exotic Dividend Ingestion & Exotic Edge Measurement

前提: ローカル DB 稼働([[local-db-setup]]、`DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`)、netkeiba 日次 scraping が疎通していること。

## T0 spike — 実 markup を先に見る(最重要・最初)

```bash
# 確定済みレースの実 result ページ fixture を捕獲(cache 使用可・1 枚)
cd scrape && uv run python -m horseracing_scrape.cli capture-fixture \
  --kind results --race-id <確定済み12桁race_id> --out tests/fixtures/real
```

- 保存された HTML を開き、払戻テーブル(`Payout_Detail_Table` 相当)の実クラス名・組合せ表記・払戻金セルを確認。
- **合否**: 6 券種(複勝/馬連/ワイド/馬単/3連複/3連単)の payout が識別可能。想定と大きく違えば parser 設計(contracts/parser.md)を修正してから実装へ。

## US1 — parser 実 markup 対応(fixture テスト)

```bash
cd scrape && uv run pytest tests/unit/test_parse_exotic_odds.py -q
```

- **期待**: 実 fixture に対し全対応券種の (bet_type, selection, 倍率) 一致・同着複数払戻の全行抽出・未対応券種スキップで継続・結果未確定 fixture は空・期待券種欠落で fail-loud。

## US2 — 日次 results 相乗り(追加リクエスト 0・冪等・例外隔離)

```bash
# 実 DB integration(相乗り・冪等・追加fetch 0・例外隔離)
cd scrape && uv run pytest tests/integration/test_exotic_cli.py -q

# 実運用相当: 確定済みの 1 日を results 取得(exotic 相乗りで exotic_odds が埋まる)
cd scrape && DATABASE_URL=... uv run python -m horseracing_scrape.cli scrape-results \
  --urls <result_url ...>   # or 既存日次経路
```

- **確認(SC-002/003/004)**:
  - その日の確定レースの exotic_odds 行が生成される
  - fetcher の呼び出し回数が result 取得分のみ(exotic 用の追加呼び出し 0)= テストで assert
  - 再実行で行数不変・値一致(冪等)
  - exotic parse を故意に失敗させても result(着順)保存が成功継続

```sql
-- 蓄積確認
SELECT bet_type, count(*), count(distinct race_id) FROM exotic_odds GROUP BY 1 ORDER BY 2 DESC;
```

## US3 — exotic edge 測定(データ蓄積後)

pre-registration doc を**結果前に固定**した上で:

```bash
cd betting && DATABASE_URL=... uv run python -m horseracing_betting.cli exotic-divergence --from <d1> --to <d2>
cd betting && DATABASE_URL=... uv run python -m horseracing_betting.cli exotic-backtest   --from <d1> --to <d2>
```

- **期待(SC-006)**: 実配当 n が n_min 未満の券種は **verdict=NO_DECISION**(edge を主張しない)。
- n 十分な券種のみ baseline 超過・cluster-bootstrap CI・OOS で ADOPT候補/REJECT 判定。

## リーク境界(SC-005)

```bash
# exotic_odds を変えてもモデル予測が byte 不変(leak-guard)
cd features && uv run pytest -q -k "leak" ; cd ../serving && uv run pytest -q -k "leak or parity"
```

- **期待**: exotic 配当の有無/値はモデル特徴・予測に一切影響しない。

## 全体回帰

```bash
cd scrape && uv run pytest -q          # parser + pipeline 相乗り
cd betting && uv run pytest -q         # exotic-backtest/divergence
uv run ruff check scrape betting
```

## 運用ノート(T028)— exotic_odds 被覆の可視化

日次 `scrape_results` 相乗り(US2)が有効化されると、確定レースごとに exotic_odds が前向きに埋まる。
被覆(確定レース中の配当取得率)は以下で監視:

```sql
-- 日別: 確定レース数 vs exotic 配当を持つレース数
SELECT r.race_date,
       count(DISTINCT rr.race_id)                        AS settled_races,
       count(DISTINCT eo.race_id)                        AS races_with_exotic,
       round(100.0 * count(DISTINCT eo.race_id)
             / nullif(count(DISTINCT rr.race_id),0), 1)  AS coverage_pct
FROM races r
JOIN race_results rr ON rr.race_id = r.race_id
LEFT JOIN exotic_odds eo ON eo.race_id = r.race_id
WHERE r.race_date >= current_date - 30
GROUP BY r.race_date ORDER BY r.race_date DESC;

-- 券種別の蓄積量
SELECT bet_type, count(*) rows, count(DISTINCT race_id) races FROM exotic_odds GROUP BY 1 ORDER BY 2 DESC;
```

edge 測定(US3)は実配当が pre-registration の n_min(place/quinella/wide=500・exacta=700・trio=1000・
trifecta=1500)に達してから:
```bash
cd betting && DATABASE_URL=... uv run python -m horseracing_betting.cli exotic-gate --from <d1> --to <d2>
```
n<n_min の券種は **NO_DECISION**(偽の勝ちを出さない)。

**注記**: `exotic-gate`/`exotic-backtest` は現状 lgbm-065 の serving parity fail-close(feature_hash 不一致=
feature 080 と無関係の既存 model/環境問題)で実 DB 実行がブロックされる。driver 自体は integration テスト
(matching model seed)で E2E 実証済。model hash 整合後に実行可能。
