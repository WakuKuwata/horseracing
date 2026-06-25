# Contract: 実 exotic オッズ取込

008 の polite 基盤を再利用した取込の公開契約。パーサはネットワーク非依存(fixture テスト)。

## パーサ(scrape/parse/exotic_odds.py)

```
parse_exotic_odds(html: str) -> ScrapedExoticOdds
```

- 入力: netkeiba の exotic オッズ HTML。出力: 券種別の (組み合わせ(netkeiba 馬番の組), odds) 行 + coverage_scope ヒント。
- 6 券種(複勝/馬連/馬単/ワイド/三連複/三連単)をパース。馬番の組はパーサ段階では生の number tuple。
- **禁止**: ネットワークアクセス、結果(着順)参照、推測 ID 結合。

## selection 正準化(011 と同一)

```
to_selection(bet_type, number_tuple) -> list[int]   # 011 の正準化と同一規則(順序/整列/単一)
```

- exacta/trifecta=順序保持、quinella/wide/trio=昇順整列、place=`[i]`。scrape と betting で同一規則(テストで一致保証)。

## upsert(scrape/upsert.py — 最新値上書き)

```
upsert_exotic_odds(session, race_id: str, scraped: ScrapedExoticOdds) -> Counts
```

- netkeiba ID は **008 の id_mappings 経由のみ**で解決(`resolve_entity`、guess-join 禁止、unmapped→`nk:` surrogate + UNMAPPED 監査)。
- future race_id は有効 JRA-VAN 12 桁でなければ行を書かない。
- `ON CONFLICT (race_id, bet_type, selection) DO UPDATE SET odds, updated_at`(冪等・最新値上書き、履歴なし、憲法 V)。
- `odds<=0`/欠損はスキップ。coverage_scope を full/partial で記録(完全は期待件数で判定)。
- **win オッズと違い結果確定後も上書き**(netkeiba 単独源、JRA-VAN 保護対象なし)。
- 戻り: Counts(processed/written/skipped/unmapped/errors)。

## pipeline(scrape/pipeline.py)

```
scrape_exotic_odds(session, *, race_id|date_range, fetcher, ...) -> JobResult
```

- fetch(robots/rate-limit/cache/UA/backoff)→ parse → upsert。`ingestion_jobs`(job_type='exotic_odds'、status、summary に券種別
  期待/観測/欠損・unmapped)で監査。部分取得 → status=partial。
- 決定論・冪等(再実行で重複ゼロ、最新値に収束)。

## CLI(scrape/cli.py 拡張)

```
uv run python -m horseracing_scrape scrape-exotic-odds --race-id <id> [--from --to] [--database-url ...]
```

- 券種別の格納件数・coverage_scope・unmapped 件数を表示。

## エラー/エッジ

- パース不能/空ページ → そのレース/券種を partial 扱いで監査、欠損は後段で推定フォールバック。
- 同一組み合わせ再取込 → 最新値で上書き(重複行を作らない)。
- 2007 未満 → 取込対象外(skipped 監査)。
