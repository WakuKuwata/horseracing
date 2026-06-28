"""race_id -> netkeiba URL builders (Feature 022).

A netkeiba race_id is identical to the JRA-VAN race_id (``YYYYVVKKDDRR``): the venue codes are
shared (see ``venues.NETKEIBA_TO_JRAVAN_VENUE`` — an identity map for JRA courses) and netkeiba's
own ``race_id`` query parameter uses the same digits. Constructing the page URL from a race_id is
NOT an entity guess-join (constitution I concerns horse/jockey/trainer ids via ``id_mappings``);
it only decides which page to fetch. Callers still validate the race_id with ``build_race_id`` and
re-check the race_id parsed from the page body against the one in the URL (fail-close on mismatch).
"""

from __future__ import annotations

from horseracing_db.validation import is_valid_race_id

_BASE = "https://race.netkeiba.com"
#: win/place odds JSON endpoint (type=1 == 単勝/複勝). Confirmed against a live sample in US3.
_ODDS_API = _BASE + "/api/api_get_jra_odds.html"


def _check(race_id: str) -> str:
    if not is_valid_race_id(race_id):
        raise ValueError(f"invalid race_id for URL build: {race_id!r}")
    return race_id


def entries_url(race_id: str) -> str:
    """出馬表 (server-rendered HTML)."""
    return f"{_BASE}/race/shutuba.html?race_id={_check(race_id)}"


def result_url(race_id: str) -> str:
    """結果 (server-rendered HTML)."""
    return f"{_BASE}/race/result.html?race_id={_check(race_id)}"


def win_odds_url(race_id: str) -> str:
    """単勝オッズ JSON (type=1). Fetched no-cache by ``odds_adapter`` (US3)."""
    return f"{_ODDS_API}?race_id={_check(race_id)}&type=1&action=update"
