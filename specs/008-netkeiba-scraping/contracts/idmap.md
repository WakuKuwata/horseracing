# Contract: ID 解決と race_id 構成

`horseracing_scrape.idmap` / `venues` の契約。

## ID 解決

```python
SURROGATE_PREFIX = "nk:"

def resolve_entity(session, *, entity_type: str, netkeiba_id: str) -> str:
    # entity_type in {'horse','jockey','trainer'} (EntityType)
    # 1. id_mappings(entity_type, source='netkeiba', source_id=netkeiba_id) を検索
    # 2. mapping_status='mapped' かつ canonical_id があれば canonical_id を返す
    # 3. それ以外: id_mappings に UNMAPPED 行を upsert(source_id=netkeiba_id, canonical_id=NULL)し、
    #    f"{SURROGATE_PREFIX}{netkeiba_id}" を返す(一意・JRA-VAN ID 空間と非衝突)
    # 推測結合は一切しない
```

## race_id 構成

```python
NETKEIBA_TO_JRAVAN_VENUE: dict[str, str]   # netkeiba 開催場コード -> JRA-VAN VV(01..10)

def build_race_id(*, year, track_code, kai, nichime, race_no) -> str | None:
    vv = NETKEIBA_TO_JRAVAN_VENUE.get(track_code)
    if vv is None or year < 2007:
        return None
    rid = f"{year:04d}{vv}{kai:02d}{nichime:02d}{race_no:02d}"
    return rid if is_valid_race_id(rid) else None  # 偽 ID を返さない
```

## 保証(テストで検証)

- マッピング済み netkeiba ID は canonical_id を返す。未対応は `nk:{id}` を返し id_mappings に UNMAPPED を積む。
- 同一 netkeiba ID は常に同一の代替 ID(一意・安定)。異なる netkeiba ID は異なる代替 ID(履歴非共有)。
- 名前+生年などで推測結合しない。
- 構成不能(未知開催場/2007 未満/不正)な race_id は None を返し、呼び出し側は行を作らない。
