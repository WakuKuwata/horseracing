"""Core-table writes with the source-safety rules (INV-N3..N5, codex BLOCKERs; Feature 176 §1-§7).

- entries: build a valid race_id or skip the race (no fake IDs). Entities (horses/jockeys/
  trainers) are INSERT-or-leave (never clobber existing JRA-VAN rows); races/race_horses are
  written through the dual-source transition table (``precedence.field_transition``) so a
  cancellation never reverts, a body weight is kept as a pair, and an official-owned static value
  is not overwritten by netkeiba.
- odds: result-pending races only, the WHOLE started field at once, monotone in ``official_at``
  (``precedence.odds_decision``); the settled fill-if-null path is a separate mode that protects
  JRA-VAN final odds.
- results: INSERT-ONLY (ON CONFLICT DO NOTHING) — never overwrite JRA-VAN; no row for
  non-starters; dead heats share finish_order; finished rows must carry a finish_order. Two modes:
  ``per_horse_legacy`` (netkeiba: per-horse insert + corner/running_style NULL fill) and
  ``race_atomic`` (official: nothing is inserted once the race has any result row).
- final odds (139): the result page's FINAL win odds/popularity overwrite race_horses only for
  netkeiba-era races (race_date >= NETKEIBA_FINAL_ODDS_FROM), only where a number was read and
  only where the value changes.
- official win payouts (139/176): single latest value per (race, 馬番), rewritten only when the
  payout changes; a same-value official confirmation moves ``source`` and the provenance.

Every writer that touches a race does so under ``lock_race`` (races FOR UPDATE, state re-read under
the lock) and writes values + provenance in the same savepoint (contracts/precedence.md §1).
"""

from __future__ import annotations

import datetime
import hashlib
import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from horseracing_db.enums import BetType, CoverageScope, EntryStatus, ResultStatus, Source
from horseracing_db.models import (
    ExoticOdds,
    ExoticQuote,
    Horse,
    IdMapping,
    Jockey,
    OfficialWinPayout,
    Race,
    RaceHorse,
    RaceLaps,
    RaceResult,
    Trainer,
)
from horseracing_db.selection import canonical_selection
from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import precedence as P
from .idmap import resolve_entity
from .models import (
    ResolvedEntryHorse,
    ResolvedResultRow,
    ScrapedEntry,
    ScrapedExoticOdds,
    ScrapedExoticQuotes,
    ScrapedHorseProfile,
    ScrapedLaps,
    ScrapedOdds,
    ScrapedResult,
    ScrapedResultRow,
    ScrapedWinPayout,
)
from .provenance import (
    ProvenanceSnapshot,
    read_provenance,
    write_horse_provenance,
    write_race_provenance,
)
from .venues import build_race_id

_NETKEIBA_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2})\.(\d)$")  # "2:00.5" or "59.8"

#: result-insert modes (contracts/precedence.md §5)
RACE_ATOMIC = "race_atomic"
PER_HORSE_LEGACY = "per_horse_legacy"


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


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _observed(observed_at: datetime.datetime | None) -> datetime.datetime:
    """The observation time recorded with a value. ``None`` = the legacy netkeiba callers that do
    not carry their fetch time; the write time stands in for it (documented, not silent)."""
    if observed_at is None:
        return _now()
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    return observed_at


@dataclass
class Counts:
    processed: int = 0
    written: int = 0
    skipped: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)
    #: named side counts a job reports next to the totals (e.g. 139's final_odds_updated /
    #: win_payout_errors); summed by key on merge and copied into the ingestion_jobs summary.
    extra: dict[str, int] = field(default_factory=dict)

    def merge(self, other: Counts) -> Counts:
        """Fold another Counts in place (pipeline._aggregate folds through this)."""
        self.processed += other.processed
        self.written += other.written
        self.skipped += other.skipped
        self.errors += other.errors
        self.error_messages.extend(other.error_messages)
        for key, value in other.extra.items():
            self.extra[key] = self.extra.get(key, 0) + value
        return self

    def bump(self, key: str, n: int = 1) -> None:
        self.extra[key] = self.extra.get(key, 0) + n


def _insert_ignore(session: Session, model, values: dict, pk: tuple[str, ...]) -> None:
    session.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=list(pk)))


#: `never_blank="*"` — protect every non-PK column the caller supplies, including ones added later.
#: Naming the columns instead would silently drop protection the next time the values dict grows.
NEVER_BLANK_ALL = "*"


def _upsert(
    session: Session,
    model,
    values: dict,
    pk: tuple[str, ...],
    fill_if_null: tuple[str, ...] = (),
    never_blank: tuple[str, ...] | str = (),
) -> None:
    """Upsert ``values``, overwriting on conflict.

    Two graduated protections, both COALESCE but in opposite directions:

    * ``fill_if_null`` — populate a NULL, never replace a value that is already there. For a field
      whose stored value outranks the scraped one.
    * ``never_blank`` (or ``NEVER_BLANK_ALL``) — take the scraped value, EXCEPT when it is NULL, in
      which case keep what is stored. This is the "a re-scrape may update a fact but may never
      erase one" rule.

    ``never_blank`` exists because a plain overwrite makes a degraded parse destructive. netkeiba
    pages legitimately answer "nothing here" for a field they showed before (a layout change, a
    different page variant for a settled race, a value not published yet), and the parser turns
    that into None. Overwriting with it wipes good data — and does so silently, since nothing about
    a NULL column says it was ever populated. That is not hypothetical: a repair that restored
    `races.grade` for the graded races lost after the source cutover was ROLLED BACK for 77 rows by
    an ordinary nightly entries re-fetch.

    A field that genuinely changes (取消, a jockey swap, 計不 → an actual body weight) still
    overwrites, because those arrive as values, not as NULL.
    """
    stmt = insert(model).values(**values)
    protect_all = never_blank == NEVER_BLANK_ALL
    update_cols = {}
    for c in values:
        if c in pk:
            continue
        if c in fill_if_null:
            update_cols[c] = func.coalesce(getattr(model, c), getattr(stmt.excluded, c))
        elif protect_all or c in never_blank:
            update_cols[c] = func.coalesce(getattr(stmt.excluded, c), getattr(model, c))
        else:
            update_cols[c] = getattr(stmt.excluded, c)
    stmt = stmt.on_conflict_do_update(index_elements=list(pk), set_=update_cols)
    session.execute(stmt)


# --- the race lock (contracts/precedence.md §1) -----------------------------------------------
@dataclass
class RaceSnapshot:
    """State of one race re-read UNDER the ``races FOR UPDATE`` lock."""

    race_id: str
    race: Race | None
    has_results: bool
    horses: dict[str, RaceHorse]  # by horse_id
    prov: ProvenanceSnapshot

    @property
    def started_numbers(self) -> set[int]:
        return {
            rh.horse_number
            for rh in self.horses.values()
            if rh.entry_status == EntryStatus.STARTED and rh.horse_number is not None
        }

    @property
    def started_ids(self) -> set[str]:
        return {h for h, rh in self.horses.items() if rh.entry_status == EntryStatus.STARTED}

    def by_number(self) -> dict[int, RaceHorse]:
        return {rh.horse_number: rh for rh in self.horses.values() if rh.horse_number is not None}

    def entry_rows(self) -> list[P.EntryRow]:
        return [P.EntryRow(h, rh.horse_number, rh.entry_status) for h, rh in self.horses.items()]


def lock_race(session: Session, race_id: str, *, race_defaults: dict | None = None) -> RaceSnapshot:
    """① ensure the races row (when ``race_defaults`` is given) ② FOR UPDATE ③ re-read state.

    Every writer of a race goes through here, in the same order, so two writers of the same race
    serialise on the row and each one decides on what the other left (the HTTP fetch happens
    BEFORE this call — the lock is never held across a network wait)."""
    if race_defaults:
        session.execute(
            insert(Race)
            .values(race_id=race_id, **race_defaults)
            .on_conflict_do_nothing(index_elements=["race_id"])
        )
    race = session.execute(
        select(Race)
        .where(Race.race_id == race_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    has_results = bool(session.scalar(select(exists().where(RaceResult.race_id == race_id))))
    horses = {
        rh.horse_id: rh
        for rh in session.scalars(
            select(RaceHorse)
            .where(RaceHorse.race_id == race_id)
            .execution_options(populate_existing=True)
        )
    }
    return RaceSnapshot(race_id, race, has_results, horses, read_provenance(session, race_id))


class _Quarantined(Exception):
    """Internal: unwinds the race savepoint when the collision check fails."""


# --- entries ----------------------------------------------------------------
_RACE_STATIC_COLS = (
    "distance",
    "track_type",
    "going",
    "weather",
    "race_class",
    "race_name",
    "grade",
    "prize_money",
)
_ENTRY_STATIC_COLS = ("frame", "horse_number", "jockey_weight", "sex", "age")


def _merge_candidates(
    session: Session,
    snap: RaceSnapshot,
    horses: Sequence[ResolvedEntryHorse],
    race_year: int | None,
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """(nk: id, canonical) pairs by name key + birth year — reported, never written (FR-011)."""
    from .jra.identity import jra_name_key

    surrogates = [h for h in snap.horses if h.startswith("nk:")]
    if not surrogates:
        return (), ()
    existing = []
    for row in session.execute(
        select(Horse.horse_id, Horse.horse_name, Horse.birth_year).where(
            Horse.horse_id.in_(surrogates)
        )
    ):
        existing.append(P.HorseIdentity(row[0], jra_name_key(row[1]), row[2]))
    incoming = []
    for h in horses:
        by = h.birth_year
        if by is None and race_year is not None and h.age is not None:
            by = race_year - h.age
        incoming.append(P.HorseIdentity(h.horse_id, jra_name_key(h.horse_name), by))
    approved = set(
        (r[0], r[1])
        for r in session.execute(
            select(IdMapping.source_id, IdMapping.canonical_id).where(
                IdMapping.entity_type == "horse",
                IdMapping.mapping_status == "mapped",
                IdMapping.canonical_id.is_not(None),
            )
        )
    )
    approved_pairs = {(f"nk:{s}", c) for s, c in approved} | approved
    return P.pair_merge_candidates(existing, incoming, approved=approved_pairs)


def write_entries(
    session: Session,
    race_id: str,
    race_values: dict,
    horses: Sequence[ResolvedEntryHorse],
    *,
    source: str = Source.NETKEIBA,
    observed_at: datetime.datetime | None = None,
    job_id: uuid.UUID | None = None,
    page_kind: str = P.PAGE_ENTRIES,
) -> Counts:
    """The common entries writer of both routes (contracts/precedence.md §1-§3).

    ``race_values`` carries the races columns (race_date / race_number / venue_code plus the
    static ones and post_time). ``horses`` carry canonical ids. Collision → the whole race is
    quarantined: nothing is written, not even a master row, and the savepoint (including a races
    row this call may have created) is rolled back."""
    c = Counts()
    observed = _observed(observed_at)
    race_date = race_values.get("race_date")
    race_year = race_date.year if race_date is not None else None
    defaults = {k: race_values.get(k) for k in ("race_date", "race_number", "venue_code")}
    ctx_static = P.TransitionContext(source, observed, page_kind)
    try:
        with session.begin_nested():
            snap = lock_race(session, race_id, race_defaults=defaults)
            incoming_rows = [P.EntryRow(h.horse_id, h.horse_number, h.entry_status) for h in horses]
            report = P.check_entry_collisions(snap.entry_rows(), incoming_rows)
            if report.quarantined:
                merge, pending = _merge_candidates(session, snap, horses, race_year)
                c.skipped += len(horses)
                c.bump("quarantined_collision")
                c.bump("merge_candidates", len(merge))
                c.bump("approved_mapping_pending_rekey", len(pending))
                c.error_messages.append(
                    f"quarantined {race_id}: {', '.join(report.kinds())}"
                    + (f" merge_candidates={list(merge)}" if merge else "")
                    + (f" approved_pending_rekey={list(pending)}" if pending else "")
                )
                raise _Quarantined
            _write_race_static(session, snap, race_values, c, ctx_static, observed, job_id)
            for h in horses:
                c.processed += 1
                _write_entry_horse(session, snap, h, c, source, observed, job_id, page_kind)
                c.written += 1
    except _Quarantined:
        pass
    return c


def _write_race_static(
    session: Session,
    snap: RaceSnapshot,
    race_values: dict,
    c: Counts,
    ctx: P.TransitionContext,
    observed: datetime.datetime,
    job_id: uuid.UUID | None,
) -> None:
    race = snap.race
    static_in = {k: race_values.get(k) for k in _RACE_STATIC_COLS}
    provided = {k: v for k, v in static_in.items() if v is not None}
    if provided:
        stored = tuple(getattr(race, k) for k in provided) if race is not None else None
        incoming = tuple(provided.values())
        mismatch = race is not None and any(
            getattr(race, k) is not None and getattr(race, k) != v for k, v in provided.items()
        )
        ctx_s = P.TransitionContext(
            ctx.incoming_source,
            ctx.incoming_observed_at,
            ctx.incoming_page_kind,
            snap.prov.race_source(P.GROUP_RACE_STATIC),
            None,
        )
        t = P.field_transition(P.GROUP_RACE_STATIC, stored, incoming, ctx_s)
        if mismatch and (t.mismatch or t.decision == "keep"):
            c.bump("static_mismatch")
        if t.decision == "write":
            values = {
                "race_id": snap.race_id,
                **{k: race_values.get(k) for k in ("race_date", "race_number", "venue_code")},
                **static_in,
            }
            _upsert(
                session,
                Race,
                values,
                ("race_id",),
                fill_if_null=("prize_money",),
                never_blank=NEVER_BLANK_ALL,
            )
            write_race_provenance(
                session,
                snap.race_id,
                P.GROUP_RACE_STATIC,
                source=ctx.incoming_source,
                observed_at=observed,
                job_id=job_id,
            )
    post_time = race_values.get("post_time")
    if post_time is not None:
        ctx_p = P.TransitionContext(
            ctx.incoming_source,
            ctx.incoming_observed_at,
            ctx.incoming_page_kind,
            snap.prov.race_source(P.GROUP_POST_TIME),
            None,
        )
        stored_pt = race.post_time if race is not None else None
        t = P.field_transition(P.GROUP_POST_TIME, stored_pt, post_time, ctx_p)
        if t.decision == "write":
            if stored_pt is not None and stored_pt != post_time:
                c.bump("post_time_changes")
            session.execute(
                update(Race).where(Race.race_id == snap.race_id).values(post_time=post_time)
            )
            write_race_provenance(
                session,
                snap.race_id,
                P.GROUP_POST_TIME,
                source=ctx.incoming_source,
                observed_at=observed,
                job_id=job_id,
            )


def _write_entry_horse(
    session: Session,
    snap: RaceSnapshot,
    h: ResolvedEntryHorse,
    c: Counts,
    source: str,
    observed: datetime.datetime,
    job_id: uuid.UUID | None,
    page_kind: str,
) -> None:
    race_id = snap.race_id
    # masters: INSERT-or-leave (never clobber an existing row)
    horse_vals: dict = {"horse_id": h.horse_id, "horse_name": h.horse_name}
    if h.data_source is not None:
        horse_vals["data_source"] = h.data_source
    if h.birth_year is not None:
        horse_vals["birth_year"] = h.birth_year
    if h.sex is not None and h.data_source is not None:
        horse_vals["sex"] = h.sex
    _insert_ignore(session, Horse, horse_vals, ("horse_id",))
    if h.jockey_id:
        _insert_ignore(
            session,
            Jockey,
            {"jockey_id": h.jockey_id, "jockey_name": h.jockey_name},
            ("jockey_id",),
        )
    if h.trainer_id:
        _insert_ignore(
            session,
            Trainer,
            {"trainer_id": h.trainer_id, "trainer_name": h.trainer_name},
            ("trainer_id",),
        )

    existing = snap.horses.get(h.horse_id)
    status_in = h.entry_status or EntryStatus.STARTED
    groups: list[str] = []
    if existing is None:
        session.execute(
            insert(RaceHorse).values(
                race_id=race_id,
                horse_id=h.horse_id,
                frame=h.frame,
                horse_number=h.horse_number,
                jockey_id=h.jockey_id,
                trainer_id=h.trainer_id,
                weight=h.weight,
                weight_diff=h.weight_diff,
                jockey_weight=h.jockey_weight,
                sex=h.sex,
                age=h.age,
                entry_status=status_in,
            )
        )
        groups = [P.GROUP_ENTRY_STATIC, P.GROUP_ENTRY_STATUS]
        if h.jockey_id:
            groups.append(P.GROUP_JOCKEY)
        if h.trainer_id:
            groups.append(P.GROUP_TRAINER)
        if h.weight is not None or h.weight_diff is not None:
            groups.append(P.GROUP_BODY_WEIGHT)
    else:
        updates: dict = {}
        # entry_static: per-column "None keeps", group-level ownership rule
        provided = {k: getattr(h, k) for k in _ENTRY_STATIC_COLS if getattr(h, k) is not None}
        if provided:
            stored = tuple(getattr(existing, k) for k in provided)
            ctx = P.TransitionContext(
                source,
                observed,
                page_kind,
                snap.prov.horse_source(h.horse_id, P.GROUP_ENTRY_STATIC),
                None,
            )
            t = P.field_transition(P.GROUP_ENTRY_STATIC, stored, tuple(provided.values()), ctx)
            if t.mismatch:
                c.bump("static_mismatch")
            if t.decision == "write":
                updates.update(provided)
                groups.append(P.GROUP_ENTRY_STATIC)
        if h.jockey_id is not None:
            ctx = P.TransitionContext(
                source,
                observed,
                page_kind,
                snap.prov.horse_source(h.horse_id, P.GROUP_JOCKEY),
                snap.prov.horse_observed_at(h.horse_id, P.GROUP_JOCKEY),
            )
            t = P.field_transition(P.GROUP_JOCKEY, existing.jockey_id, h.jockey_id, ctx)
            if t.decision == "write":
                if existing.jockey_id is not None and existing.jockey_id != h.jockey_id:
                    c.bump("jockey_changes")
                updates["jockey_id"] = h.jockey_id
                groups.append(P.GROUP_JOCKEY)
        if h.trainer_id is not None:
            ctx = P.TransitionContext(
                source,
                observed,
                page_kind,
                snap.prov.horse_source(h.horse_id, P.GROUP_TRAINER),
                None,
            )
            t = P.field_transition(P.GROUP_TRAINER, existing.trainer_id, h.trainer_id, ctx)
            if t.mismatch:
                c.bump("static_mismatch")
            if t.decision == "write":
                updates["trainer_id"] = h.trainer_id
                groups.append(P.GROUP_TRAINER)
        if h.weight is not None or h.weight_diff is not None:
            ctx = P.TransitionContext(
                source,
                observed,
                page_kind,
                snap.prov.horse_source(h.horse_id, P.GROUP_BODY_WEIGHT),
                snap.prov.horse_observed_at(h.horse_id, P.GROUP_BODY_WEIGHT),
            )
            t = P.field_transition(
                P.GROUP_BODY_WEIGHT,
                (existing.weight, existing.weight_diff),
                (h.weight, h.weight_diff),
                ctx,
            )
            if t.decision == "write":
                updates["weight"] = h.weight
                updates["weight_diff"] = h.weight_diff
                groups.append(P.GROUP_BODY_WEIGHT)
        ctx = P.TransitionContext(
            source,
            observed,
            page_kind,
            snap.prov.horse_source(h.horse_id, P.GROUP_ENTRY_STATUS),
            None,
        )
        t = P.field_transition(P.GROUP_ENTRY_STATUS, existing.entry_status, status_in, ctx)
        if t.decision == "write":
            updates["entry_status"] = status_in
            groups.append(P.GROUP_ENTRY_STATUS)
            c.bump("status_transitions")
        elif t.decision == "reject":
            c.bump("status_rejected")
        if updates:
            session.execute(
                update(RaceHorse)
                .where(RaceHorse.race_id == race_id, RaceHorse.horse_id == h.horse_id)
                .values(**updates)
            )
    for g in groups:
        write_horse_provenance(
            session, race_id, h.horse_id, g, source=source, observed_at=observed, job_id=job_id
        )


def upsert_entries(
    session: Session,
    scraped: ScrapedEntry,
    *,
    source: str = Source.NETKEIBA,
    observed_at: datetime.datetime | None = None,
    job_id: uuid.UUID | None = None,
    page_kind: str = P.PAGE_ENTRIES,
) -> Counts:
    """netkeiba entries: resolve ids (067 identity / ``nk:`` surrogates) and hand the race to the
    common writer. Unchanged call shape for every existing caller (new arguments have defaults)."""
    c = Counts()
    race_id = build_race_id(
        year=scraped.race.key.year,
        track_code=scraped.race.key.track_code,
        kai=scraped.race.key.kai,
        nichime=scraped.race.key.nichime,
        race_no=scraped.race.key.race_no,
    )
    if race_id is None:  # no fake IDs — skip the whole race
        c.skipped += 1
        c.error_messages.append("race_id not constructible (unknown venue / out of scope)")
        return c
    race_values = {
        "race_date": scraped.race.race_date,
        "race_number": scraped.race.key.race_no,
        "venue_code": race_id[4:6],
        "distance": scraped.race.distance,
        "track_type": scraped.race.track_type,
        "going": scraped.race.going,
        "weather": scraped.race.weather,
        "race_class": scraped.race.race_class,
        "race_name": scraped.race.race_name,
        "grade": scraped.race.grade,
        "post_time": scraped.race.post_time,
        "prize_money": scraped.race.prize_money,
    }
    # Feature 067: entries carry the horse/jockey/trainer NAME (and horse AGE), so identity
    # evidence is available here — resolve_entity can promote to canonical instead of minting a new
    # surrogate. 馬齢 = calendar year − birth_year (JRA 満年齢, since 2001) → birth_year derivation.
    race_year = scraped.race.race_date.year if scraped.race.race_date else None
    horses: list[ResolvedEntryHorse] = []
    for h in scraped.horses:
        birth_year = (race_year - h.age) if (race_year is not None and h.age is not None) else None
        horse_id = resolve_entity(
            session,
            entity_type="horse",
            netkeiba_id=h.netkeiba_horse_id,
            candidate_name=h.horse_name,
            candidate_birth_year=birth_year,
        )
        jockey_id = trainer_id = None
        if h.netkeiba_jockey_id:
            jockey_id = resolve_entity(
                session,
                entity_type="jockey",
                netkeiba_id=h.netkeiba_jockey_id,
                candidate_name=h.jockey_name,
            )
        if h.netkeiba_trainer_id:
            trainer_id = resolve_entity(
                session,
                entity_type="trainer",
                netkeiba_id=h.netkeiba_trainer_id,
                candidate_name=h.trainer_name,
            )
        horses.append(
            ResolvedEntryHorse(
                horse_id=horse_id,
                horse_name=h.horse_name,
                frame=h.frame,
                horse_number=h.horse_number,
                jockey_id=jockey_id,
                jockey_name=h.jockey_name,
                trainer_id=trainer_id,
                trainer_name=h.trainer_name,
                weight=h.weight,
                weight_diff=h.weight_diff,
                jockey_weight=h.jockey_weight,
                sex=h.sex,
                age=h.age,
                entry_status=h.entry_status or EntryStatus.STARTED,
                data_source="netkeiba" if horse_id.startswith("nk:") else None,
            )
        )
    c.merge(
        write_entries(
            session,
            race_id,
            race_values,
            horses,
            source=source,
            observed_at=observed_at,
            job_id=job_id,
            page_kind=page_kind,
        )
    )
    return c


# --- odds -------------------------------------------------------------------
def update_odds(
    session: Session,
    race_id: str,
    scraped: ScrapedOdds,
    *,
    source: str = Source.NETKEIBA,
    observed_at: datetime.datetime | None = None,
    job_id: uuid.UUID | None = None,
) -> Counts:
    """Update win odds + popularity (single-latest, constitution V) under the odds contract
    (contracts/precedence.md §4, both routes):

    * result-pending race → the WHOLE started field or nothing (``odds_partial_field``), monotone
      in ``official_at`` (a stale payload writes neither odds nor provenance and is ``skipped``), a
      payload WITHOUT ``official_at`` while one is stored is skipped (observation time alone never
      proves freshness), same-time same-value is a fresh observation (``observed_at`` moves, the
      adopted source keeps the official priority), same-time different values follow the fixed
      priority (official wins; ``odds_conflicts``).
    * result-finalized race (netkeiba win only) → ``settled_fill``: fill ONLY where odds IS NULL,
      never clobber an existing (JRA-VAN) final odds value, provenance untouched.

    netkeiba win-odds JSON is keyed by 馬番 → race_horses matched by (race_id, horse_number).
    Feature 140's ``races.win_odds_official_at`` / ``win_odds_observed_at`` are written with the
    values and mirrored into ``race_field_provenance('win_odds')`` (INV-P2)."""
    c = Counts()
    valid = [row for row in scraped.rows if row.odds is not None and row.odds > 0]
    if valid:
        observed = _observed(observed_at)
        with session.begin_nested():
            snap = lock_race(session, race_id)
            if snap.race is None:
                c.processed += len(valid)
                c.skipped += len(valid)
            else:
                _apply_win_odds(
                    session, snap, valid, scraped.official_at, c, source, observed, job_id
                )
    c.merge(
        update_place_quote(
            session, race_id, scraped, source=source, observed_at=observed_at, job_id=job_id
        )
    )
    return c


def _apply_win_odds(
    session: Session,
    snap: RaceSnapshot,
    valid,
    official_at,
    c: Counts,
    source: str,
    observed: datetime.datetime,
    job_id: uuid.UUID | None,
) -> None:
    race_id = snap.race_id
    by_number = snap.by_number()
    started = snap.started_numbers
    stored_vals = {n: by_number[n].odds for n in started}
    incoming_vals = {row.horse_number: Decimal(str(row.odds)) for row in valid}
    decision = P.odds_decision(
        P.KIND_WIN,
        P.OddsSnapshot(
            snap.race.win_odds_official_at, snap.prov.race_source("win_odds"), stored_vals
        ),
        P.OddsSnapshot(official_at, source, incoming_vals),
        started,
        has_results=snap.has_results,
    )
    if decision.action == "settled_fill":
        for row in valid:
            c.processed += 1
            res = session.execute(
                update(RaceHorse)
                .where(
                    RaceHorse.race_id == race_id,
                    RaceHorse.horse_number == row.horse_number,
                    RaceHorse.odds.is_(None),
                )
                .values(odds=Decimal(str(row.odds)), popularity=row.popularity)
            )
            if res.rowcount:
                c.written += res.rowcount
            else:
                c.skipped += 1
        return
    if decision.action == "skip":
        if decision.reason == "partial_field":
            c.processed += len(valid)
            c.skipped += len(valid)
            c.bump("odds_partial_field")
        else:
            c.skipped += len(valid)
            # a payload without a page time after a timed quote is stored is "not newer" too
            if decision.reason in ("stale", "no_official_at"):
                c.bump("odds_skipped_stale")
            if decision.conflict:
                c.bump("odds_conflicts")
        return
    # write: the whole started field + races provenance + race_field_provenance, one savepoint
    if decision.conflict:
        c.bump("odds_conflicts")
    for row in valid:
        if row.horse_number not in started:
            continue
        c.processed += 1
        res = session.execute(
            update(RaceHorse)
            .where(RaceHorse.race_id == race_id, RaceHorse.horse_number == row.horse_number)
            .values(odds=Decimal(str(row.odds)), popularity=row.popularity)
        )
        c.written += res.rowcount
    values: dict = {"win_odds_observed_at": observed}
    if official_at is not None:
        values["win_odds_official_at"] = official_at
    session.execute(update(Race).where(Race.race_id == race_id).values(**values))
    final_official = official_at if official_at is not None else snap.race.win_odds_official_at
    write_race_provenance(
        session,
        race_id,
        "win_odds",
        source=decision.adopted_source or source,
        observed_at=observed,
        job_id=job_id,
        official_at=final_official,
    )
    c.bump("odds_written_races")


def update_place_quote(
    session: Session,
    race_id: str,
    scraped: ScrapedOdds,
    *,
    source: str = Source.NETKEIBA,
    observed_at: datetime.datetime | None = None,
    job_id: uuid.UUID | None = None,
) -> Counts:
    """Store the 複勝 (place) market QUOTE range from the same payload (Phase 0-2, 0 extra request).

    Deliberately NOT written to `exotic_odds`: that table holds real DIVIDENDS keyed by
    (race_id, bet_type, selection), so writing quotes there would overwrite settled place dividends
    with pre-race prices (and a range does not fit its single `odds` column). Quote and dividend are
    different facts; constitution V forbids a HISTORY of each, not keeping them apart.

    Same contract as the win quote (contracts/precedence.md §4): whole started field or nothing,
    monotone in ``official_at``, no "unknown time → NULL the stored time" (176 removed that).
    Unlike win odds there is no settled fill — place quotes have no JRA-VAN value to protect."""
    c = Counts()
    quotes = {
        r.horse_number: r
        for r in scraped.place_rows
        if r.odds_low is not None and r.odds_high is not None
    }
    if not quotes:
        return c  # group "2" absent/unusable — leave any existing quote untouched
    observed = _observed(observed_at)
    with session.begin_nested():
        snap = lock_race(session, race_id)
        if snap.race is None:
            c.skipped += len(quotes)
            return c
        started = snap.started_numbers
        by_number = snap.by_number()
        stored_vals = {
            n: (by_number[n].place_odds_low, by_number[n].place_odds_high) for n in started
        }
        incoming_vals = {
            n: (Decimal(str(q.odds_low)), Decimal(str(q.odds_high))) for n, q in quotes.items()
        }
        decision = P.odds_decision(
            P.KIND_PLACE,
            P.OddsSnapshot(
                snap.race.place_odds_official_at, snap.prov.race_source("place_odds"), stored_vals
            ),
            P.OddsSnapshot(scraped.official_at, source, incoming_vals),
            started,
            has_results=snap.has_results,
        )
        if decision.action != "write":
            if decision.reason == "results_settled":
                # A settled race is the normal state of most refreshes; a quote arriving after the
                # result is simply not a pre-race quote (FR-013) — nothing to count as skipped.
                return c
            c.skipped += len(started or quotes)
            if decision.reason == "partial_field":
                c.bump("odds_partial_field")
            if decision.reason in ("stale", "no_official_at"):
                c.bump("odds_skipped_stale")
            if decision.conflict:
                c.bump("odds_conflicts")
            return c
        if decision.conflict:
            c.bump("odds_conflicts")
        for number in sorted(started):
            q = quotes[number]
            c.processed += 1
            res = session.execute(
                update(RaceHorse)
                .where(RaceHorse.race_id == race_id, RaceHorse.horse_number == number)
                .values(
                    place_odds_low=Decimal(str(q.odds_low)),
                    place_odds_high=Decimal(str(q.odds_high)),
                    place_popularity=q.popularity,
                )
            )
            c.written += res.rowcount
        values: dict = {"place_odds_observed_at": observed}
        if scraped.official_at is not None:
            values["place_odds_official_at"] = scraped.official_at
        session.execute(update(Race).where(Race.race_id == race_id).values(**values))
        final_official = (
            scraped.official_at
            if scraped.official_at is not None
            else snap.race.place_odds_official_at
        )
        write_race_provenance(
            session,
            race_id,
            "place_odds",
            source=decision.adopted_source or source,
            observed_at=observed,
            job_id=job_id,
            official_at=final_official,
        )
    return c


def _derive_running_style(corner_orders, field_size: int) -> str | None:
    """Derive 脚質 from the FIRST-corner position relative to field size (netkeiba has no official
    脚質 column). Mapped to JRA-VAN's vocabulary so 023's front/closer features stay consistent:
    逃げ(先頭) / 先行(前1/4) / 中団 / 差し(後1/4) / 追込(最後方). Heuristic — used only to fill a
    NULL running_style (never clobbers an authoritative JRA-VAN value)."""
    if not corner_orders or field_size <= 0:
        return None
    try:
        pos1 = int(corner_orders[0])
    except (TypeError, ValueError):
        return None
    if pos1 <= 0:
        return None
    if pos1 == 1:
        return "逃げ"
    r = pos1 / field_size
    if r <= 0.25:
        return "先行"
    if r <= 0.50:
        return "中団"
    if r <= 0.75:
        return "差し"
    return "追込"


# --- results ----------------------------------------------------------------
#: テン3F を導出できる唯一の距離。JRA-VAN の col55 と `finish_time - last_3f` は 1200m のとき
#: **187,833 行で平均誤差 0.0000 秒**、他の距離(1000/1400/1600/2000m)では 3〜50 秒ずれて全く
#: 一致しない。つまり JRA 自身が 1200m のテン3F を「走破時計 − 上がり3F」として出しており、
#: ここで計算しているのは推定値ではなく**同じ定義の再現**である。
#:
#: なぜ要るか: `race_results.first_3f` は JRA-VAN 生 CSV 由来で、供給停止により 2024 年 96.8%
#: → 2025 年 74.4% → **2026 年 0.0%** と消えた。netkeiba は馬ごとの上がり3F しか出さず、テン3F
#: は出さない(ラップページが持つのはレース単位の先頭ペースであって馬ごとの値ではない)。全復旧は
#: 不可能で、kill-test では定常状態で「何も無い」に対し「1200m だけある」が winner NLL で
#: **-0.0045** 良い。取得は一切増えない。
DERIVABLE_FIRST3F_DISTANCE = 1200


def _derive_first_3f(distance: int | None, finish_td, last_3f) -> Decimal | None:
    """1200m に限り テン3F = 走破時計 − 上がり3F。他距離は None(推測しない)。"""
    if distance != DERIVABLE_FIRST3F_DISTANCE or finish_td is None or last_3f is None:
        return None
    v = Decimal(str(finish_td.total_seconds())) - Decimal(str(last_3f))
    return v if v > 0 else None  # 負や 0 は入力が壊れている合図。埋めない。


def _insert_results(
    session: Session,
    race_id: str,
    rows: Sequence[ResolvedResultRow],
    *,
    mode: str,
    source: str = Source.NETKEIBA,
    observed_at: datetime.datetime | None = None,
    job_id: uuid.UUID | None = None,
    snapshot: RaceSnapshot | None = None,
) -> Counts:
    """INSERT-ONLY result rows, in one of two modes (contracts/precedence.md §5).

    ``per_horse_legacy`` (netkeiba): per-horse ``ON CONFLICT DO NOTHING`` plus the two enumerated
    NULL fills (corner_orders, running_style). ``race_atomic`` (official): if the race already has
    ANY result row nothing is inserted (``results_existing_races``, differences counted as
    ``results_mismatch``); all rows are validated first and one bad row inserts nothing."""
    if mode not in (RACE_ATOMIC, PER_HORSE_LEGACY):
        raise ValueError(f"unknown result insert mode: {mode!r}")
    c = Counts()
    observed = _observed(observed_at)
    with session.begin_nested():
        snap = snapshot or lock_race(session, race_id)
        started = snap.started_ids
        field_size = len(started)
        distance = snap.race.distance if snap.race is not None else None
        winner_td = next(
            (parse_netkeiba_time(r.finish_time) for r in rows if r.finish_order == 1), None
        )
        result_rows = [r for r in rows if r.result_status in ResultStatus.ALL]
        if mode == RACE_ATOMIC:
            if snap.has_results:
                stored = {
                    r.horse_id: (r.finish_order, r.result_status)
                    for r in session.scalars(
                        select(RaceResult).where(RaceResult.race_id == race_id)
                    )
                }
                mismatches = [
                    r.horse_id
                    for r in result_rows
                    if r.horse_id in stored
                    and stored[r.horse_id] != (r.finish_order, r.result_status)
                ]
                c.skipped += len(rows)
                c.bump("results_existing_races")
                if mismatches:
                    c.bump("results_mismatch", len(mismatches))
                    c.error_messages.append(f"results mismatch {race_id}: {mismatches}")
                return c
            bad = [
                r
                for r in result_rows
                if r.horse_id not in started
                or (
                    r.result_status == ResultStatus.FINISHED
                    and (r.finish_order is None or parse_netkeiba_time(r.finish_time) is None)
                )
            ]
            if bad:
                c.errors += 1
                c.error_messages.append(
                    f"results {race_id}: {len(bad)} invalid row(s) — nothing inserted (race_atomic)"
                )
                return c
        inserted_any = False
        for row in rows:
            c.processed += 1
            if row.result_status not in ResultStatus.ALL or row.horse_id not in started:
                c.skipped += 1  # no result row for non-starters
                continue
            if row.result_status == ResultStatus.FINISHED and row.finish_order is None:
                c.errors += 1  # finished requires finish_order (DB constraint) — fail-close
                c.error_messages.append(f"finished without finish_order: {row.horse_id}")
                continue
            own_td = parse_netkeiba_time(row.finish_time)
            diff = own_td - winner_td if (own_td is not None and winner_td is not None) else None
            res = session.execute(
                insert(RaceResult)
                .values(
                    race_id=race_id,
                    horse_id=row.horse_id,
                    finish_order=row.finish_order,
                    result_status=row.result_status,
                    finish_time=own_td,
                    finish_time_diff=diff,
                    last_3f=row.last_3f,
                    first_3f=_derive_first_3f(distance, own_td, row.last_3f),
                    corner_orders=list(row.corner_orders) if row.corner_orders else None,
                )
                .on_conflict_do_nothing(index_elements=["race_id", "horse_id"])
            )
            inserted_any = inserted_any or bool(res.rowcount)
            if mode == PER_HORSE_LEGACY:
                # corner_orders fill-NULL-only. The result page fetched on race night carries the
                # finishing order and times but NOT yet the 通過順 (measured 2026-07/08); with the
                # insert above being INSERT-ONLY that gap never closed. Only the NULL cell is
                # filled.
                if row.corner_orders:
                    session.execute(
                        update(RaceResult)
                        .where(
                            RaceResult.race_id == race_id,
                            RaceResult.horse_id == row.horse_id,
                            RaceResult.corner_orders.is_(None),
                        )
                        .values(corner_orders=list(row.corner_orders))
                    )
                # fill-if-null derived 脚質 (never clobbers a JRA-VAN running_style)
                style = _derive_running_style(row.corner_orders, field_size)
                if style is not None:
                    session.execute(
                        update(RaceHorse)
                        .where(
                            RaceHorse.race_id == race_id,
                            RaceHorse.horse_id == row.horse_id,
                            RaceHorse.running_style.is_(None),
                        )
                        .values(running_style=style)
                    )
            c.written += 1
        if inserted_any and not snap.has_results:
            write_race_provenance(
                session, race_id, "results", source=source, observed_at=observed, job_id=job_id
            )
            c.bump("results_inserted_races")
    return c


def backfill_results(
    session: Session,
    race_id: str,
    scraped: ScrapedResult,
    *,
    observed_at: datetime.datetime | None = None,
    job_id: uuid.UUID | None = None,
) -> Counts:
    """netkeiba results → ``_insert_results(mode=per_horse_legacy)``.

    ids via ``resolve_entity``."""
    rows = [
        ResolvedResultRow(
            horse_id=resolve_entity(session, entity_type="horse", netkeiba_id=r.netkeiba_horse_id),
            finish_order=r.finish_order,
            result_status=r.result_status,
            finish_time=r.finish_time,
            last_3f=r.last_3f,
            corner_orders=r.corner_orders,
        )
        for r in scraped.rows
    ]
    return _insert_results(
        session,
        race_id,
        rows,
        mode=PER_HORSE_LEGACY,
        source=Source.NETKEIBA,
        observed_at=observed_at,
        job_id=job_id,
    )


# --- final odds + official win payouts (Feature 139) --------------------------------------------
#: First race day of the netkeiba era: the first day an ``nk:`` horse appears in this DB. JRA-VAN
#: supplied FINAL win odds through 2025-10-05; from this date on ``race_horses.odds`` comes from
#: netkeiba alone, and for a settled race it is the last PRE-RACE quote (the live odds endpoint
#: stops serving win odds once a race settles, and ``update_odds`` only fills NULLs then). Final
#: odds are written for races on or after this date only — an older race keeps its JRA-VAN value
#: even when someone refreshes it from the result page (plan D8).
NETKEIBA_FINAL_ODDS_FROM = datetime.date(2025, 10, 11)


def page_sha256(html: str) -> str:
    """sha256 of a page's text as UTF-8 — the provenance stamp stored with a payout (plan D10).

    race.netkeiba.com serves UTF-8, so for those pages this equals the sha256 of the raw bytes."""
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


@dataclass
class FinalOddsCounts:
    """What ``apply_final_odds`` did with one race's result page."""

    #: rows whose odds (and popularity, when read) actually changed
    updated: int = 0
    #: a number was read but equals what is stored (a re-run lands here)
    unchanged: int = 0
    #: no usable win odds on the page for this horse — the stored value is kept, never NULLed
    unreadable: int = 0
    #: the horse has no race_horses row in this race (e.g. an nk:/canonical id split) — its stored
    #: (pre-race) odds stay as they were, so this is surfaced, never silent
    unmatched: int = 0
    #: 1 when the race is before NETKEIBA_FINAL_ODDS_FROM: nothing written (JRA-VAN final odds)
    skipped_pre_netkeiba: int = 0
    #: 1 when the race has no races row / no race_date: nothing written (fail closed)
    race_unknown: int = 0
    #: 1 when the page has result rows but NOT ONE readable win odds (a moved header, a markup
    #: change): the pre-race odds stay stored — the R05 failure mode, so it is counted per race
    missing: int = 0


def _usable_win_odds(value: float | None) -> Decimal | None:
    if value is None or not math.isfinite(value) or value < 1.0:  # 1.0 = the payout floor
        return None
    return Decimal(str(value))


def apply_final_odds(
    session: Session,
    race_id: str,
    rows: Iterable[ScrapedResultRow],
    *,
    observed_at: datetime.datetime,
) -> FinalOddsCounts:
    """Overwrite ``race_horses.odds`` / ``popularity`` with the result page's FINAL values.

    Why: a win bet pays the final odds, and for a netkeiba-era settled race the stored odds are
    otherwise the last pre-race quote (R05: 474 + 14 races). Rules (plan D8/D9):

    * only races with ``race_date >= NETKEIBA_FINAL_ODDS_FROM``; an older race gets nothing written
      and ``skipped_pre_netkeiba = 1``, one whose date is unknown gets nothing written and
      ``race_unknown = 1`` (fail closed: the JRA-VAN final odds must never be replaced by a
      re-scrape);
    * only horses whose page odds are a number >= 1.0 — a blank / "---" / 取消 / 除外 cell never
      NULLs or lowers a stored value (non-starters carry no result row at all);
    * popularity is written alongside only when the page gave a number for it;
    * only rows whose value changes are touched (``IS DISTINCT FROM``), so the same page twice is
      a no-op and race_horses.updated_at does not move on a re-run;
    * a page with result rows but not one readable win odds is ``missing = 1`` (the stored odds
      stay pre-race — surfaced per race, like a missing 単勝 payout);
    * Feature 140: when at least one row was rewritten, the race's win-quote provenance becomes
      ``win_odds_observed_at = observed_at`` (when the result page was fetched; for an archive
      repair the archive file's timestamp) and ``win_odds_official_at = NULL`` — a final value has
      no quote time (plan D3). A no-op re-run leaves both as they were.
    """
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    out = FinalOddsCounts()
    race_date = session.scalar(select(Race.race_date).where(Race.race_id == race_id))
    if race_date is None:
        out.race_unknown = 1
        return out
    if race_date < NETKEIBA_FINAL_ODDS_FROM:
        out.skipped_pre_netkeiba = 1
        return out
    rows = list(rows)

    stored = {
        horse_id: (odds, popularity)
        for horse_id, odds, popularity in session.execute(
            select(RaceHorse.horse_id, RaceHorse.odds, RaceHorse.popularity).where(
                RaceHorse.race_id == race_id
            )
        )
    }
    for row in rows:
        new_odds = _usable_win_odds(row.win_odds)
        if new_odds is None:
            out.unreadable += 1
            continue
        horse_id = resolve_entity(session, entity_type="horse", netkeiba_id=row.netkeiba_horse_id)
        if horse_id not in stored:
            out.unmatched += 1
            continue
        old_odds, old_popularity = stored[horse_id]
        values: dict = {"odds": new_odds}
        changed = old_odds is None or Decimal(old_odds) != new_odds
        distinct = RaceHorse.odds.is_distinct_from(new_odds)
        if row.popularity is not None:
            values["popularity"] = row.popularity
            changed = changed or old_popularity != row.popularity
            distinct = or_(distinct, RaceHorse.popularity.is_distinct_from(row.popularity))
        if not changed:
            out.unchanged += 1
            continue
        res = session.execute(
            update(RaceHorse)
            .where(RaceHorse.race_id == race_id, RaceHorse.horse_id == horse_id, distinct)
            .values(**values)
        )
        out.updated += res.rowcount
        if not res.rowcount:  # changed under us between the read and the write
            out.unchanged += 1
    if rows and out.unreadable == len(rows):
        out.missing = 1
    if out.updated:
        session.execute(
            update(Race)
            .where(Race.race_id == race_id)
            .values(win_odds_observed_at=observed_at, win_odds_official_at=None)
        )
    return out


@dataclass
class WinPayoutCounts:
    """What ``upsert_official_win_payouts`` did with one race's 単勝 payouts."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    #: stored winners the page no longer lists (an official correction) — deleted
    removed: int = 0
    #: 1 when the race has no races row yet (nothing to attach a payout to — upsert_laps rule)
    no_race: int = 0
    #: same value already stored but the adopting SOURCE moved (official confirmation, 176)
    confirmed: int = 0
    #: 1 when netkeiba tried to write over an official block (176 §6)
    skipped_official: int = 0

    @property
    def written(self) -> int:
        return self.inserted + self.updated + self.removed


OFFICIAL_WIN_PAYOUT_SOURCE_NETKEIBA = "netkeiba_result"
OFFICIAL_WIN_PAYOUT_SOURCE_JRA = "jra_official_result"


def upsert_official_win_payouts(
    session: Session,
    race_id: str,
    payouts: Sequence[ScrapedWinPayout],
    *,
    observed_at: datetime.datetime,
    html_sha256: str | None = None,
    source: str = OFFICIAL_WIN_PAYOUT_SOURCE_NETKEIBA,
    job_id: uuid.UUID | None = None,
) -> WinPayoutCounts:
    """Store the race's official 単勝 payouts (single latest value per 馬番, constitution V).

    A row is (re)written only when its ``payout_yen`` differs from what is stored; ``observed_at``
    and ``html_sha256`` then record the observation that set the value. Re-reading the same payout
    — the same page again, or a later fetch of it — changes nothing, so a re-run reports 0.

    When the page lists winners, a stored winner it no longer lists is deleted (an official
    correction, e.g. a 降着 that changes the winner). An EMPTY list writes nothing and deletes
    nothing: a page without a 単勝 row is not evidence that the race paid nobody.

    Feature 176: the set is adopted per source priority — once the official route has written the
    race's win payouts, netkeiba's rows are skipped; a same-value official confirmation moves only
    ``source`` and the provenance (``confirmed``).
    """
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    out = WinPayoutCounts()
    if not payouts:
        return out
    by_number: dict[int, int] = {}
    for p in payouts:
        if p.horse_number < 1 or p.payout_yen < 100:
            raise ValueError(f"invalid win payout for {race_id}: {p}")
        if p.horse_number in by_number and by_number[p.horse_number] != p.payout_yen:
            raise ValueError(f"conflicting win payouts for {race_id} 馬番 {p.horse_number}")
        by_number[p.horse_number] = p.payout_yen
    with session.begin_nested():
        snap = lock_race(session, race_id)
        if snap.race is None:
            out.no_race = 1
            return out
        incoming_source = (
            Source.JRA_OFFICIAL if source == OFFICIAL_WIN_PAYOUT_SOURCE_JRA else Source.NETKEIBA
        )
        stored_source = snap.prov.race_source("win_payouts")
        stored_rows = {
            number: (payout_yen, src)
            for number, payout_yen, src in session.execute(
                select(
                    OfficialWinPayout.horse_number,
                    OfficialWinPayout.payout_yen,
                    OfficialWinPayout.source,
                ).where(OfficialWinPayout.race_id == race_id)
            )
        }
        decision = P.payout_block_decision(
            "complete",
            {n: v[0] for n, v in stored_rows.items()},
            by_number,
            stored_source=stored_source,
            incoming_source=incoming_source,
        )
        if decision.action == "skip":
            out.skipped_official = 1
            return out
        if decision.to_delete:
            res = session.execute(
                delete(OfficialWinPayout).where(
                    OfficialWinPayout.race_id == race_id,
                    OfficialWinPayout.horse_number.in_(list(decision.to_delete)),
                )
            )
            out.removed += res.rowcount
        for number in sorted(decision.to_update):
            payout_yen = by_number[number]
            stmt = insert(OfficialWinPayout).values(
                race_id=race_id,
                horse_number=number,
                payout_yen=payout_yen,
                observed_at=observed_at,
                html_sha256=html_sha256,
                source=source,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["race_id", "horse_number"],
                set_={
                    "payout_yen": stmt.excluded.payout_yen,
                    "observed_at": stmt.excluded.observed_at,
                    "html_sha256": stmt.excluded.html_sha256,
                    "source": stmt.excluded.source,
                },
                where=OfficialWinPayout.payout_yen.is_distinct_from(stmt.excluded.payout_yen),
            )
            session.execute(stmt)
            if number in stored_rows:
                out.updated += 1
            else:
                out.inserted += 1
        for number in decision.to_confirm:
            if stored_rows[number][1] != source and incoming_source == Source.JRA_OFFICIAL:
                session.execute(
                    update(OfficialWinPayout)
                    .where(
                        OfficialWinPayout.race_id == race_id,
                        OfficialWinPayout.horse_number == number,
                    )
                    .values(source=source)
                )
                out.confirmed += 1
            else:
                out.unchanged += 1
        if out.written or out.confirmed or stored_source is None:
            adopted = (
                Source.JRA_OFFICIAL
                if incoming_source == Source.JRA_OFFICIAL or stored_source == Source.JRA_OFFICIAL
                else Source.NETKEIBA
            )
            write_race_provenance(
                session,
                race_id,
                "win_payouts",
                source=adopted,
                observed_at=observed_at,
                job_id=job_id,
            )
    return out


def bloodline_for(session: Session, *, line_col: str, name_col: str, name: str) -> str | None:
    """The single bloodline line recorded for ``name`` among horses that already carry one.

    Returns None when the name is unknown OR maps to more than one distinct line (ambiguous —
    never pick one). Exact-name match on purpose: a looser match would be a guess."""
    line_attr, name_attr = getattr(Horse, line_col), getattr(Horse, name_col)
    lines = list(
        session.scalars(
            select(line_attr).where(name_attr == name, line_attr.isnot(None)).distinct()
        )
    )
    return lines[0] if len(lines) == 1 else None


# --- horse profile completion (leak-safe, opt-in) ---------------------------
def complete_horse_profile(session: Session, horse_id: str, profile: ScrapedHorseProfile) -> Counts:
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
        "owner_name": profile.owner_name,
        "breeder_name": profile.breeder_name,
    }
    changed = False
    for col, value in candidates.items():
        if value is not None and getattr(horse, col) is None:
            setattr(horse, col, value)
            changed = True
    # Bloodline LINES (sire_line/damsire_line, categorical model inputs since 056) only ever came
    # from the JRA-VAN CSV, so every netkeiba horse had them NULL. The line is a pure function of
    # the sire's name in the data we already hold (1,679 sires, none with two lines), so derive it
    # locally — zero requests — and refuse the rare ambiguous name rather than guess.
    for line_col, name_col in (("sire_line", "sire_name"), ("damsire_line", "damsire_name")):
        if getattr(horse, line_col) is None and getattr(horse, name_col):
            line = bloodline_for(
                session, line_col=line_col, name_col=name_col, name=getattr(horse, name_col)
            )
            if line is not None:
                setattr(horse, line_col, line)
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
        return n  # per-horse 複勝 odds
    if bet_type in (BetType.QUINELLA, BetType.WIDE):
        return math.comb(n, 2)
    if bet_type == BetType.EXACTA:
        return n * (n - 1)
    if bet_type == BetType.TRIO:
        return math.comb(n, 3)
    if bet_type == BetType.TRIFECTA:
        return n * (n - 1) * (n - 2)
    return 0


def upsert_exotic_odds(
    session: Session,
    race_id: str,
    scraped: ScrapedExoticOdds,
    *,
    source: str = Source.NETKEIBA,
    observed_at: datetime.datetime | None = None,
    job_id: uuid.UUID | None = None,
) -> Counts:
    """Store REAL exotic odds with the single-latest-value overwrite (constitution V).

    selection is the db canonical array (same as 011 to_selection); combos are 馬番 so no
    id-mapping is needed. ON CONFLICT overwrites the latest value (pre-race -> final dividend),
    even after results exist (netkeiba is the sole source — nothing to protect). coverage_scope is
    full when a bet type's observed combos equal the expected full-grid count, else partial.

    Feature 176: once the official route owns the race's exotic payouts, netkeiba's rows are
    skipped (``skipped_official_payouts``).
    """
    c = Counts()
    with session.begin_nested():
        snap = lock_race(session, race_id)
        if (
            snap.prov.race_source("exotic_payouts") == Source.JRA_OFFICIAL
            and source == Source.NETKEIBA
        ):
            c.skipped += len(scraped.rows)
            c.bump("skipped_official_payouts")
            return c
        n_started = len(snap.started_ids)

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
                stmt = (
                    insert(ExoticOdds)
                    .values(
                        race_id=race_id,
                        bet_type=bet_type,
                        selection=list(sel_tuple),
                        odds=Decimal(str(odds)),
                        coverage_scope=scope,
                        source=source,
                    )
                    .on_conflict_do_update(
                        constraint="uq_exotic_odds_race_bettype_selection",
                        set_={
                            "odds": Decimal(str(odds)),
                            "coverage_scope": scope,
                            "source": source,
                        },
                    )
                )
                session.execute(stmt)
                c.written += 1
        if c.written and (snap.has_results or source == Source.JRA_OFFICIAL):
            write_race_provenance(
                session,
                race_id,
                "exotic_payouts",
                source=source,
                observed_at=_observed(observed_at),
                job_id=job_id,
            )
    return c


def replace_exotic_dividends(
    session: Session,
    race_id: str,
    bet_type: str,
    lines: Mapping[tuple[int, ...], int],
    *,
    source: str = Source.JRA_OFFICIAL,
    observed_at: datetime.datetime | None = None,
    job_id: uuid.UUID | None = None,
    snapshot: RaceSnapshot | None = None,
) -> Counts:
    """Replace ONE bet type's dividend rows as a SET (contracts/precedence.md §6, 176 official).

    ``lines`` maps the canonical selection to the payout per 100 yen. Rows not in the set are
    deleted (a correction), differing rows are updated, same-value rows only move ``source``."""
    c = Counts()
    if bet_type not in BetType.EXOTIC:
        raise ValueError(f"not an exotic bet type: {bet_type!r}")
    with session.begin_nested():
        snap = snapshot or lock_race(session, race_id)
        if snap.race is None:
            c.skipped += 1
            return c
        stored = {
            tuple(sel): (odds, src)
            for sel, odds, src in session.execute(
                select(ExoticOdds.selection, ExoticOdds.odds, ExoticOdds.source).where(
                    ExoticOdds.race_id == race_id, ExoticOdds.bet_type == bet_type
                )
            )
        }
        incoming = {
            tuple(canonical_selection(bet_type, list(sel))): Decimal(yen) / Decimal(100)
            for sel, yen in lines.items()
        }
        decision = P.payout_block_decision(
            "complete",
            {k: v[0] for k, v in stored.items()},
            incoming,
            stored_source=snap.prov.race_source("exotic_payouts"),
            incoming_source=source,
        )
        if decision.action != "replace":
            c.skipped += 1
            return c
        for sel in decision.to_delete:
            res = session.execute(
                delete(ExoticOdds).where(
                    ExoticOdds.race_id == race_id,
                    ExoticOdds.bet_type == bet_type,
                    ExoticOdds.selection == list(sel),
                )
            )
            c.bump("payout_rows_removed", res.rowcount)
        for sel in decision.to_update:
            odds = incoming[sel]
            stmt = (
                insert(ExoticOdds)
                .values(
                    race_id=race_id,
                    bet_type=bet_type,
                    selection=list(sel),
                    odds=odds,
                    coverage_scope=CoverageScope.PARTIAL,
                    source=source,
                )
                .on_conflict_do_update(
                    constraint="uq_exotic_odds_race_bettype_selection",
                    set_={"odds": odds, "source": source},
                )
            )
            session.execute(stmt)
            c.written += 1
            c.bump("payout_rows_written")
        for sel in decision.to_confirm:
            if stored[sel][1] != source:
                session.execute(
                    update(ExoticOdds)
                    .where(
                        ExoticOdds.race_id == race_id,
                        ExoticOdds.bet_type == bet_type,
                        ExoticOdds.selection == list(sel),
                    )
                    .values(source=source)
                )
        write_race_provenance(
            session,
            race_id,
            "exotic_payouts",
            source=source,
            observed_at=_observed(observed_at),
            job_id=job_id,
        )
    return c


# --- race laps (034) --------------------------------------------------------


def upsert_laps(
    session: Session,
    race_id: str,
    scraped: ScrapedLaps,
    *,
    source: str = Source.NETKEIBA,
    only_if_absent: bool = False,
    observed_at: datetime.datetime | None = None,
    job_id: uuid.UUID | None = None,
) -> Counts:
    """Store the race-level sectional lap profile with single-latest-value overwrite (constitution
    V). RESULT-derived; written only when the race row exists (FK). Skips empty lap arrays.
    ``only_if_absent`` (176 official route): write only when the race has no laps row yet."""
    c = Counts()
    c.processed += 1
    if not scraped.lap_times:
        c.skipped += 1
        return c
    if not session.scalar(select(exists().where(Race.race_id == race_id))):
        c.skipped += 1  # no race row yet → nothing to attach laps to
        return c
    existed = bool(session.scalar(select(exists().where(RaceLaps.race_id == race_id))))
    if only_if_absent and existed:
        c.skipped += 1
        c.bump("laps_existing")
        return c
    first = None if scraped.pace_first_3f is None else Decimal(str(scraped.pace_first_3f))
    last = None if scraped.pace_last_3f is None else Decimal(str(scraped.pace_last_3f))
    laps = [float(x) for x in scraped.lap_times]
    stmt = (
        insert(RaceLaps)
        .values(
            race_id=race_id,
            lap_times=laps,
            pace_first_3f=first,
            pace_last_3f=last,
            source=source,
        )
        .on_conflict_do_update(
            index_elements=["race_id"],
            set_={
                "lap_times": laps,
                "pace_first_3f": first,
                "pace_last_3f": last,
                "source": source,
            },
        )
    )
    session.execute(stmt)
    c.written += 1
    if not existed:
        c.bump("laps_inserted")
    write_race_provenance(
        session, race_id, "laps", source=source, observed_at=_observed(observed_at), job_id=job_id
    )
    return c


# --- exotic quotes (pre-race price grid) ------------------------------------
def upsert_exotic_quotes(session: Session, scraped: ScrapedExoticQuotes) -> Counts:
    """Store one race's PRE-RACE grid for one exotic bet type (single latest value, 憲法 V).

    Deliberately NOT `exotic_odds`: that table is the final DIVIDEND and only ever covers the
    combination that came in. A quote and a dividend are different facts, and the earlier place
    work already established that keeping them apart is what constitution V actually requires.

    Monotone in source time: a payload whose `official_at` predates the stored one is refused, so
    a replayed or stale response cannot walk the latest grid backwards.
    """
    c = Counts()
    if not scraped.quotes:
        c.skipped += 1
        return c
    prior = session.scalar(
        select(ExoticQuote.official_at).where(
            ExoticQuote.race_id == scraped.race_id, ExoticQuote.bet_type == scraped.bet_type
        )
    )
    if scraped.official_at is not None and prior is not None and scraped.official_at < prior:
        c.skipped += 1
        return c

    payload = {
        "-".join(str(n) for n in combo): [lo, hi, pop]
        for combo, (lo, hi, pop) in sorted(scraped.quotes.items())
    }
    c.processed += len(payload)
    stmt = (
        insert(ExoticQuote)
        .values(
            race_id=scraped.race_id,
            bet_type=scraped.bet_type,
            quotes=payload,
            n_combinations=len(payload),
            official_at=scraped.official_at,
            observed_at=datetime.datetime.now(datetime.UTC),
            source="netkeiba",
        )
        .on_conflict_do_update(
            constraint="uq_exotic_quotes_race_bettype",
            set_={
                "quotes": payload,
                "n_combinations": len(payload),
                "official_at": scraped.official_at,
                "observed_at": datetime.datetime.now(datetime.UTC),
            },
        )
    )
    session.execute(stmt)
    c.written += 1
    return c
