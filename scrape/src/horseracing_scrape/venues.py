"""netkeiba venue code -> JRA-VAN VV, and JRA-VAN-compatible race_id construction (R3, INV-N3).

A future race_id is only produced when it forms a valid JRA-VAN 12-digit id (``YYYYVVKKDDRR``)
for a known JRA course in scope. Otherwise ``None`` — the caller writes no row (no fake IDs).
"""

from __future__ import annotations

from horseracing_db.validation import INGEST_SCOPE_START, is_valid_race_id

#: netkeiba track code -> JRA-VAN venue code (VV). JRA central courses share the same 2-digit
#: course codes; non-JRA (local / overseas) codes are intentionally absent -> unmapped.
NETKEIBA_TO_JRAVAN_VENUE: dict[str, str] = {
    "01": "01",  # 札幌
    "02": "02",  # 函館
    "03": "03",  # 福島
    "04": "04",  # 新潟
    "05": "05",  # 東京
    "06": "06",  # 中山
    "07": "07",  # 中京
    "08": "08",  # 京都
    "09": "09",  # 阪神
    "10": "10",  # 小倉
}


def build_race_id(
    *, year: int, track_code: str, kai: int, nichime: int, race_no: int
) -> str | None:
    """Construct a valid JRA-VAN race_id, or None if it can't be (unknown course / out of scope)."""
    vv = NETKEIBA_TO_JRAVAN_VENUE.get(track_code)
    if vv is None or year < INGEST_SCOPE_START.year:
        return None
    race_id = f"{year:04d}{vv}{kai:02d}{nichime:02d}{race_no:02d}"
    return race_id if is_valid_race_id(race_id) else None
