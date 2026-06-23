# Contract: scrape 取り込みパイプライン

`horseracing_scrape.fetch` / `parse` / `upsert` / `pipeline` の契約。

## 取得層(PoliteFetcher)

```python
class PoliteFetcher(Protocol):
    def get(self, url: str) -> str: ...   # robots 確認→キャッシュ→レート制限→取得→デコード

class HttpFetcher:    # 本番: httpx + robotparser + 最小間隔 + file cache + UA + 指数バックオフ
    def __init__(self, *, user_agent, min_interval_s, cache_dir, max_retries): ...

class FixtureFetcher: # テスト: url->ローカル HTML を返す(ネットワーク非依存)
    ...
```

robots.txt 不許可 URL は取得しない(例外)。レスポンスはキャッシュしエンコーディングを正規化。

## パーサ(純粋関数、network-free)

```python
def parse_entries(html: str) -> ScrapedEntry: ...
def parse_odds(html: str) -> list[ScrapedOdds]: ...
def parse_results(html: str) -> list[ScrapedResult]: ...
# 必須要素欠損は ParseError(fail-close、誤データを作らない)
```

## upsert

```python
def upsert_entries(session, scraped: ScrapedEntry) -> Counts:
    # race_id = build_race_id(...); None なら何も書かず skip 計上
    # horses/jockeys/trainers を resolve_entity の id で PK upsert
    # races / race_horses(枠/馬番/騎手/調教師/斤量/entry_status) を PK upsert(べき等)
def update_odds(session, race_id, odds: list[ScrapedOdds]) -> Counts:
    # 対象 race に race_results が存在すれば skip(結果確定済み=JRA-VAN 保護)
    # 結果未確定なら race_horses.odds を最新値で上書き(欠損/不正は除外)
def backfill_results(session, race_id, results: list[ScrapedResult]) -> Counts:
    # race_results に INSERT ... ON CONFLICT (race_id,horse_id) DO NOTHING(既存を更新しない)
    # 非出走馬には行を作らない、同着は finish_order 共有
```

## pipeline(監査込み)

```python
def scrape_entries(session, *, race_id|date, fetcher) -> JobSummary
def scrape_odds(session, *, race_id|date, fetcher) -> JobSummary
def scrape_results(session, *, race_id|date, fetcher) -> JobSummary
# 各々: fetch→parse→upsert を ingestion_jobs(source='netkeiba', job_type, scope, counts, status,
#       parser_version, 時刻)に記録。部分失敗は partial + errors。idempotent。
```

## 保証(テストで検証)

- 出馬表が core テーブルに取り込まれ、マッピング済み=canonical_id / 未対応=`nk:{id}` + UNMAPPED キュー。
- 構成不能 race_id は行を作らず skip 計上。
- 前売りオッズは結果未確定レースのみ更新、結果確定済みの odds は不変。
- 結果は insert-only で既存 JRA-VAN 行を変更しない、欠損のみ補完。
- 再実行で重複・破壊なし(idempotent)、各実行が ingestion_jobs に監査される。
- パーサは HTML フィクスチャでネットワーク非依存にテストでき、必須要素欠損で fail-close。
