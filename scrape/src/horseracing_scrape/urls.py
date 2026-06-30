"""race_id -> netkeiba URL builders (Feature 022).

A netkeiba race_id is identical to the JRA-VAN race_id (``YYYYVVKKDDRR``): the venue codes are
shared (see ``venues.NETKEIBA_TO_JRAVAN_VENUE`` — an identity map for JRA courses) and netkeiba's
own ``race_id`` query parameter uses the same digits. Constructing the page URL from a race_id is
NOT an entity guess-join (constitution I concerns horse/jockey/trainer ids via ``id_mappings``);
it only decides which page to fetch. Callers still validate the race_id with ``build_race_id`` and
re-check the race_id parsed from the page body against the one in the URL (fail-close on mismatch).
"""

from __future__ import annotations

import datetime

from horseracing_db.validation import is_valid_race_id

_BASE = "https://race.netkeiba.com"
#: win/place odds JSON endpoint (type=1 == 単勝/複勝). Confirmed against a live sample in US3.
_ODDS_API = _BASE + "/api/api_get_jra_odds.html"
#: classic server-rendered DB site (horse/jockey/trainer profiles).
_DB_BASE = "https://db.netkeiba.com"


def _check(race_id: str) -> str:
    if not is_valid_race_id(race_id):
        raise ValueError(f"invalid race_id for URL build: {race_id!r}")
    return race_id


def _kaisai_date(date: str | datetime.date) -> str:
    """Normalize a date to the netkeiba ``kaisai_date`` form (YYYYMMDD)."""
    if isinstance(date, datetime.date):
        return date.strftime("%Y%m%d")
    digits = date.replace("-", "").strip()
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError(f"invalid kaisai_date: {date!r} (want YYYYMMDD)")
    return digits


def entries_url(race_id: str) -> str:
    """出馬表 (server-rendered HTML)."""
    return f"{_BASE}/race/shutuba.html?race_id={_check(race_id)}"


def result_url(race_id: str) -> str:
    """結果 (server-rendered HTML)."""
    return f"{_BASE}/race/result.html?race_id={_check(race_id)}"


def win_odds_url(race_id: str) -> str:
    """単勝オッズ JSON (type=1). Fetched no-cache by ``odds_adapter`` (US3)."""
    return f"{_ODDS_API}?race_id={_check(race_id)}&type=1&action=update"


def race_list_url(date: str | datetime.date) -> str:
    """開催日のレース一覧 (server-rendered Ajax fragment).

    The doc lists ``top/race_list.html?kaisai_date=`` but that top page renders its race links via
    JavaScript. netkeiba serves the same list as a server-rendered HTML fragment at
    ``top/race_list_sub.html`` — which httpx can fetch directly (no JS) and which carries the
    ``race_id=`` links we discover. Returns an empty list-of-races for a date with no JRA racing.
    """
    return f"{_BASE}/top/race_list_sub.html?kaisai_date={_kaisai_date(date)}"


def race_db_url(race_id: str) -> str:
    """db.netkeiba.com race DB page (carries the ラップタイム sectional profile, Feature 034)."""
    return f"{_DB_BASE}/race/{_check(race_id)}/"


def _horse_id(netkeiba_horse_id: str) -> str:
    if not netkeiba_horse_id or not str(netkeiba_horse_id).strip():
        raise ValueError("empty netkeiba_horse_id")
    return str(netkeiba_horse_id).strip()


def horse_profile_url(netkeiba_horse_id: str) -> str:
    """馬プロフィール本体 (classic server-rendered db.netkeiba.com page).

    Server-renders 識別属性 (馬名 / 性別 / 生年). NOTE: this page's pedigree block is JS-rendered
    (empty ``#horse_pedigree_box`` container) — pedigree comes from ``horse_pedigree_url`` instead
    (verified against live markup 2026-06-28). Career performance stats are NEVER read (leak
    boundary, constitution II)."""
    return f"{_DB_BASE}/horse/{_horse_id(netkeiba_horse_id)}/"


def horse_pedigree_url(netkeiba_horse_id: str) -> str:
    """馬血統 (server-rendered ``table.blood_table`` page).

    The profile page renders pedigree via JS; this dedicated page server-renders the full blood
    table (sire = ``td.b_ml`` line, dam = ``td.b_fml`` line) for the sire/dam/damsire fields."""
    return f"{_DB_BASE}/horse/ped/{_horse_id(netkeiba_horse_id)}/"
