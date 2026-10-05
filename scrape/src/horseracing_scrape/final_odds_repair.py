"""Feature 139: re-apply FINAL win odds and official 単勝 payouts from ARCHIVED result pages.

``race_horses.odds`` of netkeiba-era settled races holds the last PRE-RACE quote (R05), and the
official win payout was never stored at all. The result pages that carry both were already fetched
and archived by ``HttpFetcher`` (``{archive}/race.netkeiba.com/{sha256(url)[:16]}/url.txt`` + one
``{UTC stamp}.html.gz`` per fetch), so this repair reads only those files — NO network (plan D7: a
race without an archived page is counted, never re-fetched here).

Per race: the newest archived page that has a result table is used (a pre-race fetch of the same
URL has none and is passed over), and it is written through the SAME function the results job uses
(:func:`horseracing_scrape.pipeline.apply_result_page_settlement`). Each race runs in its own
SAVEPOINT, so one unreadable or failing page never undoes or blocks another race. ``dry_run`` does
the whole pass and rolls it back, so the counts are exact and nothing is written.
"""

from __future__ import annotations

import datetime
import gzip
import re
from dataclasses import dataclass, field
from pathlib import Path

from horseracing_db.models import Race, RaceResult
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from .fetch import _META_CHARSET_RE
from .models import NotYetPublished, ScrapedResult
from .parse.results import parse_results
from .pipeline import apply_result_page_settlement
from .venues import build_race_id

#: the host directory the fetcher archives race.netkeiba.com pages under
ARCHIVE_HOST = "race.netkeiba.com"
_RESULT_URL_RE = re.compile(r"/race/result\.html\?(?:.*&)?race_id=(\d{12})(?:&|$)")
#: ``HttpFetcher._archive_write`` names each copy ``%Y%m%dT%H%M%S%fZ[-n].html.gz`` (UTC)
_STAMP_RE = re.compile(r"^(\d{8}T\d{12}Z)(?:-(\d+))?\.html\.gz$")


@dataclass
class RepairFinalOddsReport:
    #: settled races (with a race_results row) whose race_date is in the window
    races: int = 0
    #: of those, races with an archived page that has a result table
    archived: int = 0
    #: race_horses rows whose odds/popularity changed
    odds_updated: int = 0
    #: official_win_payouts rows inserted / updated / removed
    payouts: int = 0
    #: races with no archived settled result page (re-fetching them is a separate, approved step)
    missing_archive: int = 0
    #: races whose page could not be applied (unreadable page, wrong race, write failure)
    errors: int = 0
    #: archived races before the netkeiba era: their odds are left alone (payouts still stored)
    skipped_pre_netkeiba: int = 0
    #: archived races whose races row has no race_date (nothing written: fail closed)
    race_unknown: int = 0
    #: archived settled pages that print no 単勝 row
    payout_missing: int = 0
    #: archived settled pages with result rows but not one readable win odds (stored odds kept)
    odds_missing: int = 0
    #: horses on the pages without a usable win odds (blank / "---") — their stored odds are kept
    odds_unreadable: int = 0
    #: horses on the pages without a race_horses row in that race (an id split) — not updated
    odds_unmatched: int = 0
    dry_run: bool = False
    error_messages: list[str] = field(default_factory=list)
    missing_race_ids: list[str] = field(default_factory=list)
    #: races counted in ``odds_missing``
    odds_missing_race_ids: list[str] = field(default_factory=list)


def _decode(raw: bytes) -> str:
    """The archive keeps RAW bytes; decode them the way the fetcher would (meta charset, else
    UTF-8 — race.netkeiba.com is UTF-8)."""
    m = _META_CHARSET_RE.search(raw[:2048])
    if m:
        enc = m.group(1).decode("ascii", "ignore").strip().lower()
        try:
            return raw.decode(enc, errors="replace")
        except LookupError:
            pass
    return raw.decode("utf-8", errors="replace")


def archived_at(path: Path) -> datetime.datetime | None:
    """When the archived copy was fetched (UTC, from its file name), or None if not an archive."""
    m = _STAMP_RE.match(path.name)
    if m is None:
        return None
    return datetime.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S%fZ").replace(tzinfo=datetime.UTC)


def index_result_archive(archive_dir: Path) -> dict[str, list[Path]]:
    """race_id -> archived result-page copies, newest first (across every folder of that race)."""
    host_dir = archive_dir / ARCHIVE_HOST
    found: dict[str, list[tuple[datetime.datetime, int, Path]]] = {}
    if not host_dir.is_dir():
        return {}
    for marker in host_dir.glob("*/url.txt"):
        m = _RESULT_URL_RE.search(marker.read_text(encoding="utf-8").strip())
        if m is None:
            continue
        for path in marker.parent.glob("*.html.gz"):
            stamp = archived_at(path)
            if stamp is None:
                continue
            suffix = _STAMP_RE.match(path.name).group(2)
            found.setdefault(m.group(1), []).append((stamp, int(suffix or 0), path))
    return {
        race_id: [p for _, _, p in sorted(items, reverse=True)]
        for race_id, items in found.items()
    }


def _settled_page(paths: list[Path]) -> tuple[ScrapedResult, str, datetime.datetime] | None:
    """The newest copy that carries a result table. A pre-race copy (NotYetPublished) is passed
    over; any other parse failure is raised — it is a real problem with the settled page."""
    for path in paths:
        html = _decode(gzip.decompress(path.read_bytes()))
        try:
            scraped = parse_results(html)
        except NotYetPublished:
            continue
        return scraped, html, archived_at(path)
    return None


def _page_race_id(scraped: ScrapedResult) -> str | None:
    k = scraped.key
    return build_race_id(year=k.year, track_code=k.track_code, kai=k.kai, nichime=k.nichime,
                         race_no=k.race_no)


def _settled_races(session: Session, date_from: datetime.date, date_to: datetime.date
                   ) -> list[str]:
    return list(session.scalars(
        select(Race.race_id)
        .where(Race.race_date >= date_from, Race.race_date <= date_to)
        .where(exists().where(RaceResult.race_id == Race.race_id))
        .order_by(Race.race_id)
    ))


def repair_final_odds(
    session: Session, *, date_from: datetime.date, date_to: datetime.date, archive_dir: Path,
    dry_run: bool = False,
) -> RepairFinalOddsReport:
    """Apply archived result pages to the settled races in [date_from, date_to] (see module doc).

    Commits once at the end; ``dry_run`` rolls everything back instead.
    """
    if not archive_dir.is_absolute():
        raise ValueError(f"archive_dir must be absolute: {archive_dir}")
    rep = RepairFinalOddsReport(dry_run=dry_run)
    archive = index_result_archive(archive_dir)
    for race_id in _settled_races(session, date_from, date_to):
        rep.races += 1
        try:
            page = _settled_page(archive.get(race_id, []))
            if page is None:
                rep.missing_archive += 1
                rep.missing_race_ids.append(race_id)
                continue
            scraped, html, observed_at = page
            page_race_id = _page_race_id(scraped)
            if page_race_id != race_id:
                raise ValueError(f"archived page is for race {page_race_id}")
            rep.archived += 1
            with session.begin_nested():
                c = apply_result_page_settlement(session, race_id, scraped, html,
                                                 observed_at=observed_at)
        except Exception as exc:  # noqa: BLE001 — one race must never stop the others
            rep.errors += 1
            rep.error_messages.append(f"{race_id}: {exc}")
            continue
        rep.odds_updated += c.extra.get("final_odds_updated", 0)
        rep.payouts += c.extra.get("win_payouts", 0)
        rep.skipped_pre_netkeiba += c.extra.get("final_odds_skipped_pre_netkeiba", 0)
        rep.race_unknown += c.extra.get("final_odds_race_unknown", 0)
        rep.payout_missing += c.extra.get("win_payout_missing", 0)
        rep.odds_unreadable += c.extra.get("final_odds_unreadable", 0)
        rep.odds_unmatched += c.extra.get("final_odds_unmatched", 0)
        if c.extra.get("final_odds_missing", 0):
            rep.odds_missing += 1
            rep.odds_missing_race_ids.append(race_id)
        if c.errors:
            rep.errors += c.errors
            rep.error_messages.extend(c.error_messages)
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return rep
