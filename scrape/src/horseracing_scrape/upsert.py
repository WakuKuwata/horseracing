"""Core-table writes with the netkeiba-specific safety rules (INV-N3..N5, codex BLOCKERs).

- entries: build a valid race_id or skip the race (no fake IDs). Entities (horses/jockeys/
  trainers) are INSERT-or-leave (never clobber existing JRA-VAN rows); races/race_horses upsert
  so entry_status (cancellations) and finalizing fields update.
- odds: update race_horses.odds ONLY for result-pending races (no race_results) — protects
  JRA-VAN final odds.
- results: INSERT-ONLY (ON CONFLICT DO NOTHING) — never overwrite JRA-VAN; no row for
  non-starters; dead heats share finish_order; finished rows must carry a finish_order.
"""

from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal

from horseracing_db.enums import BetType, CoverageScope, EntryStatus, ResultStatus
from horseracing_db.models import ExoticOdds, Horse, Jockey, Race, RaceHorse, RaceResult, Trainer
from horseracing_db.selection import canonical_selection
from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .idmap import resolve_entity
from .models import (
    ScrapedEntry,
    ScrapedExoticOdds,
    ScrapedHorseProfile,
    ScrapedOdds,
    ScrapedResult,
)
from .venues import build_race_id

_NETKEIBA_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2})\.(\d)$")  # "2:00.5" or "59.8"


def parse_netkeiba_time(value: str | None) -> datetime.timedelta | None:
    """netkeiba finish time 'M:SS.s' / 'SS.s' -> timedelta; None if empty/unparseable."""
    if not value:
        return None
    m = _NETKEIBA_TIME_RE.match(value.strip())
    if not m:
        return None
    minutes = int(m.group(1)) if m.group(1) else 0
    return datetime.timedelta(
        minutes=minutes, seconds=int(m.group(2)), milliseconds=int(m.group(3)) * 100
    )


@dataclass
class Counts:
    processed: int = 0
    written: int = 0
    skipped: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)


def _insert_ignore(session: Session, model, values: dict, pk: tuple[str, ...]) -> None:
    session.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=list(pk)))


def _upsert(session: Session, model, values: dict, pk: tuple[str, ...]) -> None:
    stmt = insert(model).values(**values)
    update_cols = {c: getattr(stmt.excluded, c) for c in values if c not in pk}
    stmt = stmt.on_conflict_do_update(index_elements=list(pk), set_=update_cols)
    session.execute(stmt)


# --- entries ----------------------------------------------------------------
def upsert_entries(session: Session, scraped: ScrapedEntry) -> Counts:
    c = Counts()
    race_id = build_race_id(
        year=scraped.race.key.year, track_code=scraped.race.key.track_code,
        kai=scraped.race.key.kai, nichime=scraped.race.key.nichime,
        race_no=scraped.race.key.race_no,
    )
    if race_id is None:  # no fake IDs — skip the whole race
        c.skipped += 1
        c.error_messages.append("race_id not constructible (unknown venue / out of scope)")
        return c

    _upsert(session, Race, {
        "race_id": race_id, "race_date": scraped.race.race_date,
        "race_number": scraped.race.key.race_no, "venue_code": race_id[4:6],
        "distance": scraped.race.distance, "track_type": scraped.race.track_type,
        "going": scraped.race.going, "weather": scraped.race.weather,
        "race_class": scraped.race.race_class,
    }, ("race_id",))

    for h in scraped.horses:
        c.processed += 1
        horse_id = resolve_entity(session, entity_type="horse", netkeiba_id=h.netkeiba_horse_id)
        # never clobber an existing (JRA-VAN) entity; new surrogate horses get inserted
        horse_vals = {"horse_id": horse_id, "horse_name": h.horse_name}
        if horse_id.startswith("nk:"):
            horse_vals["data_source"] = "netkeiba"  # only the horses table has data_source
        _insert_ignore(session, Horse, horse_vals, ("horse_id",))

        jockey_id = trainer_id = None
        if h.netkeiba_jockey_id:
            jockey_id = resolve_entity(session, entity_type="jockey",
                                       netkeiba_id=h.netkeiba_jockey_id)
            _insert_ignore(session, Jockey,
                           {"jockey_id": jockey_id, "jockey_name": h.jockey_name}, ("jockey_id",))
        if h.netkeiba_trainer_id:
            trainer_id = resolve_entity(session, entity_type="trainer",
                                        netkeiba_id=h.netkeiba_trainer_id)
            _insert_ignore(session, Trainer,
                           {"trainer_id": trainer_id, "trainer_name": h.trainer_name},
                           ("trainer_id",))

        _upsert(session, RaceHorse, {
            "race_id": race_id, "horse_id": horse_id, "frame": h.frame,
            "horse_number": h.horse_number, "jockey_id": jockey_id, "trainer_id": trainer_id,
            "weight": h.weight, "sex": h.sex, "age": h.age,
            "entry_status": h.entry_status or EntryStatus.STARTED,
        }, ("race_id", "horse_id"))
        c.written += 1
    return c


# --- odds -------------------------------------------------------------------
def update_odds(session: Session, race_id: str, scraped: ScrapedOdds) -> Counts:
    c = Counts()
    has_results = session.scalar(select(exists().where(RaceResult.race_id == race_id)))
    if has_results:  # result-finalized race -> protect JRA-VAN final odds
        c.skipped += 1
        c.error_messages.append("race has results (final odds protected); odds not updated")
        return c
    for row in scraped.rows:
        c.processed += 1
        if row.odds is None or row.odds <= 0:
            continue
        # netkeiba win-odds JSON is keyed by 馬番 → match the existing race_horses row by
        # (race_id, horse_number); no id_mapping needed (Feature 022 I1). Also persist popularity.
        res = session.execute(
            update(RaceHorse)
            .where(RaceHorse.race_id == race_id, RaceHorse.horse_number == row.horse_number)
            .values(odds=Decimal(str(row.odds)), popularity=row.popularity)
        )
        c.written += res.rowcount or 0
    return c


# --- results ----------------------------------------------------------------
def backfill_results(session: Session, race_id: str, scraped: ScrapedResult) -> Counts:
    c = Counts()
    started = set(
        session.scalars(
            select(RaceHorse.horse_id)
            .where(RaceHorse.race_id == race_id)
            .where(RaceHorse.entry_status == EntryStatus.STARTED)
        )
    )
    for row in scraped.rows:
        c.processed += 1
        horse_id = resolve_entity(session, entity_type="horse", netkeiba_id=row.netkeiba_horse_id)
        if horse_id not in started:  # no result row for non-starters
            c.skipped += 1
            continue
        if row.result_status == ResultStatus.FINISHED and row.finish_order is None:
            c.errors += 1  # finished requires finish_order (DB constraint) — fail-close
            c.error_messages.append(f"finished without finish_order: {horse_id}")
            continue
        # INSERT-ONLY: never overwrite an existing (JRA-VAN) race_results row
        session.execute(
            insert(RaceResult).values(
                race_id=race_id, horse_id=horse_id, finish_order=row.finish_order,
                result_status=row.result_status,
                finish_time=parse_netkeiba_time(row.finish_time),
            ).on_conflict_do_nothing(index_elements=["race_id", "horse_id"])
        )
        c.written += 1
    return c


# --- horse profile completion (leak-safe, opt-in) ---------------------------
def complete_horse_profile(
    session: Session, horse_id: str, profile: ScrapedHorseProfile
) -> Counts:
    """Fill leak-safe identity/pedigree attributes on an EXISTING horse row.

    fill-NULL-only: never clobber an attribute already set (protects JRA-VAN data, INV-N4). Only
    identity/pedigree columns are written — career stats are never read or stored (leak boundary,
    constitution II). Pedigree ids are resolved via id_mappings (canonical or ``nk:`` surrogate),
    never guess-joined. A horse not yet in the DB is skipped (entries must be ingested first)."""
    c = Counts()
    c.processed += 1
    horse = session.get(Horse, horse_id)
    if horse is None:  # entries create the row first; nothing to complete otherwise
        c.skipped += 1
        c.error_messages.append(f"horse not in DB: {horse_id}")
        return c

    def _ped_id(netkeiba_id: str | None) -> str | None:
        if not netkeiba_id:
            return None
        return resolve_entity(session, entity_type="horse", netkeiba_id=netkeiba_id)

    candidates = {
        "sex": profile.sex,
        "birth_year": profile.birth_year,
        "sire_id": _ped_id(profile.netkeiba_sire_id),
        "sire_name": profile.sire_name,
        "dam_id": _ped_id(profile.netkeiba_dam_id),
        "dam_name": profile.dam_name,
        "damsire_id": _ped_id(profile.netkeiba_damsire_id),
        "damsire_name": profile.damsire_name,
    }
    changed = False
    for col, value in candidates.items():
        if value is not None and getattr(horse, col) is None:
            setattr(horse, col, value)
            changed = True
    if changed:
        c.written += 1
    else:
        c.skipped += 1  # nothing new to fill (already complete / page had nothing leak-safe)
    return c


# --- exotic odds (012) ------------------------------------------------------
def _expected_count(bet_type: str, n: int) -> int:
    """Full-grid combination count for n started horses (drives coverage_scope)."""
    if n <= 0:
        return 0
    if bet_type == BetType.PLACE:
        return n                              # per-horse 複勝 odds
    if bet_type in (BetType.QUINELLA, BetType.WIDE):
        return math.comb(n, 2)
    if bet_type == BetType.EXACTA:
        return n * (n - 1)
    if bet_type == BetType.TRIO:
        return math.comb(n, 3)
    if bet_type == BetType.TRIFECTA:
        return n * (n - 1) * (n - 2)
    return 0


def upsert_exotic_odds(session: Session, race_id: str, scraped: ScrapedExoticOdds) -> Counts:
    """Store REAL exotic odds with the single-latest-value overwrite (constitution V).

    selection is the db canonical array (same as 011 to_selection); combos are 馬番 so no
    id-mapping is needed. ON CONFLICT overwrites the latest value (pre-race -> final dividend),
    even after results exist (netkeiba is the sole source — nothing to protect). coverage_scope is
    full when a bet type's observed combos equal the expected full-grid count, else partial.
    """
    c = Counts()
    n_started = session.scalar(
        select(func.count())
        .select_from(RaceHorse)
        .where(RaceHorse.race_id == race_id, RaceHorse.entry_status == EntryStatus.STARTED)
    ) or 0

    # group valid rows by bet type to decide coverage from observed-vs-expected counts
    by_type: dict[str, list[tuple[list[int], float]]] = {}
    for row in scraped.rows:
        c.processed += 1
        if row.odds is None or row.odds <= 0:
            c.skipped += 1
            continue
        try:
            selection = canonical_selection(row.bet_type, row.numbers)
        except ValueError as exc:
            c.errors += 1
            c.error_messages.append(str(exc))
            continue
        by_type.setdefault(row.bet_type, []).append((selection, float(row.odds)))

    for bet_type, items in by_type.items():
        # dedupe by selection (keep last seen) so observed count matches stored rows
        deduped: dict[tuple[int, ...], float] = {tuple(sel): odds for sel, odds in items}
        expected = _expected_count(bet_type, n_started)
        scope = (
            CoverageScope.FULL
            if expected > 0 and len(deduped) == expected
            else CoverageScope.PARTIAL
        )
        for sel_tuple, odds in deduped.items():
            stmt = insert(ExoticOdds).values(
                race_id=race_id, bet_type=bet_type, selection=list(sel_tuple),
                odds=Decimal(str(odds)), coverage_scope=scope, source="netkeiba",
            ).on_conflict_do_update(
                constraint="uq_exotic_odds_race_bettype_selection",
                set_={"odds": Decimal(str(odds)), "coverage_scope": scope},
            )
            session.execute(stmt)
            c.written += 1
    return c
