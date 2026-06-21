# Contract: パーサ/マッパ関数契約

`horseracing_ingest` が公開する純関数群。DB 非依存・副作用なし (upsert を除く)。

## parser.py

```python
def parse_rows(path: str) -> Iterator[ParsedRow]:
    """Shift_JIS(cp932) で行ストリーム。各行を 73 列固定で検証し ParsedRow を yield。
    列数≠73 / デコード不能は RowError(line_no, reason) を yield (例外で全体を止めない)。"""
```

- `ParsedRow`: 73 フィールドを持つ dataclass (生文字列、行番号付き)。
- 不変条件: メモリに全行を載せない (ストリーム)。

## mapping.py

```python
def derive_race_id(year, venue_code, kai, nichime, race_no) -> str   # 12桁、is_valid_race_id を満たす
def venue_to_code(venue_name: str) -> str                            # R3 表、未知は KeyError
def normalize_status(row) -> StatusDecision                          # R4: entry/result status, finish_order
def to_core_records(row) -> CoreRecords                              # races/horses/jockeys/trainers/race_horses/race_results 用 dict
```

- `StatusDecision`: `entry_status`, `make_result_row: bool`, `result_status | None`, `finish_order | None`。
- 不変条件: 未知状態は例外/エラー化し、黙って `finished` にしない。欠損は None (0 を入れない)。

## upsert.py

```python
def upsert_core(session, records: CoreRecords) -> None
    # 順序: races -> horses/jockeys/trainers -> race_horses -> race_results
    # PostgreSQL ON CONFLICT DO UPDATE (PK 上)。冪等。
```

## pipeline.py

```python
def ingest_year(session, path: str) -> IngestSummary
    # parse -> map -> batch upsert -> ingestion_jobs 監査 + checkpoint。
    # 2007 境界は is_in_ingest_scope。<2007 は skip して記録。
```

- `IngestSummary`: `{year, races, race_horses, race_results, skipped, errors}`。
