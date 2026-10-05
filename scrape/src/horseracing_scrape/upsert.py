"""Core-table writes with the netkeiba-specific safety rules (INV-N3..N5, codex BLOCKERs).

- entries: build a valid race_id or skip the race (no fake IDs). Entities (horses/jockeys/
  trainers) are INSERT-or-leave (never clobber existing JRA-VAN rows); races/race_horses upsert
  so entry_status (cancellations) and finalizing fields update.
- odds: update race_horses.odds ONLY for result-pending races (no race_results) — protects
  JRA-VAN final odds.
- results: INSERT-ONLY (ON CONFLICT DO NOTHING) — never overwrite JRA-VAN; no row for
  non-starters; dead heats share finish_order; finished rows must carry a finish_order.
- final odds (139): the result page's FINAL win odds/popularity overwrite race_horses only for
  netkeiba-era races (race_date >= NETKEIBA_FINAL_ODDS_FROM), only where a number was read and
  only where the value changes.
- official win payouts (139): single latest value per (race, 馬番), rewritten only when the
  payout changes.
"""

from __future__ import annotations

import datetime
import hashlib
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from horseracing_db.enums import BetType, CoverageScope, EntryStatus, ResultStatus
from horseracing_db.models import (
    ExoticOdds,
    ExoticQuote,
    Horse,
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

from .idmap import resolve_entity
from .models import (
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


def _insert_ignore(session: Session, model, values: dict, pk: tuple[str, ...]) -> None:
    session.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=list(pk)))


#: `never_blank="*"` — protect every non-PK column the caller supplies, including ones added later.
#: Naming the columns instead would silently drop protection the next time the values dict grows.
NEVER_BLANK_ALL = "*"


def _upsert(
    session: Session, model, values: dict, pk: tuple[str, ...],
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
        "race_class": scraped.race.race_class, "race_name": scraped.race.race_name,
        "grade": scraped.race.grade, "post_time": scraped.race.post_time,
        "prize_money": scraped.race.prize_money,
    }, ("race_id",), fill_if_null=("prize_money",), never_blank=NEVER_BLANK_ALL)

    # Feature 067: entries carry the horse/jockey/trainer NAME (and horse AGE), so identity
    # evidence is available here — resolve_entity can promote to canonical instead of minting a new
    # surrogate. 馬齢 = calendar year − birth_year (JRA 満年齢, since 2001) → birth_year derivation.
    race_year = scraped.race.race_date.year if scraped.race.race_date else None
    for h in scraped.horses:
        c.processed += 1
        birth_year = (race_year - h.age) if (race_year is not None and h.age is not None) else None
        horse_id = resolve_entity(
            session, entity_type="horse", netkeiba_id=h.netkeiba_horse_id,
            candidate_name=h.horse_name, candidate_birth_year=birth_year,
        )
        # never clobber an existing (JRA-VAN) entity; new surrogate horses get inserted
        horse_vals = {"horse_id": horse_id, "horse_name": h.horse_name}
        if horse_id.startswith("nk:"):
            horse_vals["data_source"] = "netkeiba"  # only the horses table has data_source
        _insert_ignore(session, Horse, horse_vals, ("horse_id",))

        jockey_id = trainer_id = None
        if h.netkeiba_jockey_id:
            jockey_id = resolve_entity(session, entity_type="jockey",
                                       netkeiba_id=h.netkeiba_jockey_id,
                                       candidate_name=h.jockey_name)
            _insert_ignore(session, Jockey,
                           {"jockey_id": jockey_id, "jockey_name": h.jockey_name}, ("jockey_id",))
        if h.netkeiba_trainer_id:
            trainer_id = resolve_entity(session, entity_type="trainer",
                                        netkeiba_id=h.netkeiba_trainer_id,
                                        candidate_name=h.trainer_name)
            _insert_ignore(session, Trainer,
                           {"trainer_id": trainer_id, "trainer_name": h.trainer_name},
                           ("trainer_id",))

        _upsert(session, RaceHorse, {
            "race_id": race_id, "horse_id": horse_id, "frame": h.frame,
            "horse_number": h.horse_number, "jockey_id": jockey_id, "trainer_id": trainer_id,
            "weight": h.weight, "weight_diff": h.weight_diff, "jockey_weight": h.jockey_weight,
            "sex": h.sex, "age": h.age,
            "entry_status": h.entry_status or EntryStatus.STARTED,
        }, ("race_id", "horse_id"), never_blank=NEVER_BLANK_ALL)
        c.written += 1
    return c


# --- odds -------------------------------------------------------------------
def update_odds(session: Session, race_id: str, scraped: ScrapedOdds) -> Counts:
    """Update win odds + popularity from netkeiba (single-latest, constitution V).

    Two write modes keep the JRA-VAN final-odds protection while still capturing odds for both
    upcoming and finished netkeiba races (the confirmed odds JSON serves both):
    - result-pending race  -> overwrite (latest pre-race value).
    - result-finalized race -> fill ONLY where odds IS NULL. This lets a netkeiba-only finished
      race get its confirmed odds, but NEVER clobbers an existing (JRA-VAN) final odds value.
    netkeiba win-odds JSON is keyed by 馬番 → match race_horses by (race_id, horse_number); no
    id_mapping needed (Feature 022 I1)."""
    c = Counts()
    has_results = session.scalar(select(exists().where(RaceResult.race_id == race_id)))
    for row in scraped.rows:
        if row.odds is None or row.odds <= 0:
            continue
        c.processed += 1
        stmt = (
            update(RaceHorse)
            .where(RaceHorse.race_id == race_id, RaceHorse.horse_number == row.horse_number)
        )
        if has_results:  # finalized: fill-if-null, never overwrite existing (JRA-VAN) odds
            stmt = stmt.where(RaceHorse.odds.is_(None))
        res = session.execute(stmt.values(odds=Decimal(str(row.odds)), popularity=row.popularity))
        if res.rowcount:
            c.written += res.rowcount
        elif has_results:  # existing odds protected (or no matching horse)
            c.skipped += 1

    c.merge(update_place_quote(session, race_id, scraped))
    return c


def update_place_quote(session: Session, race_id: str, scraped: ScrapedOdds) -> Counts:
    """Store the 複勝 (place) market QUOTE range from the same payload (Phase 0-2, 0 extra request).

    Deliberately NOT written to `exotic_odds`: that table holds real DIVIDENDS keyed by
    (race_id, bet_type, selection), so writing quotes there would overwrite settled place dividends
    with pre-race prices (and a range does not fit its single `odds` column). Quote and dividend are
    different facts; constitution V forbids a HISTORY of each, not keeping them apart.

    Cross-sectional fail-closed: the quote is written only when EVERY started horse has a valid
    range. A partially-priced field would silently break any cross-pool comparison (which needs the
    whole field at one instant), so a gap skips the race entirely rather than storing a half field.

    Monotone in source time: a payload whose `official_at` predates the stored one is refused, so a
    replayed/stale response can never walk the latest value backwards. Unlike win odds there is no
    fill-if-null protection — place quotes have no JRA-VAN value to protect (netkeiba is the sole
    source), and the latest quote is always the better one.
    """
    c = Counts()
    quotes = {r.horse_number: r for r in scraped.place_rows
              if r.odds_low is not None and r.odds_high is not None}
    if not quotes:
        return c  # group "2" absent/unusable — leave any existing quote untouched

    started = set(session.scalars(
        select(RaceHorse.horse_number).where(
            RaceHorse.race_id == race_id,
            RaceHorse.entry_status == EntryStatus.STARTED,
            RaceHorse.horse_number.is_not(None),
        )
    ))
    if not started or not started.issubset(quotes):
        c.skipped += len(started or quotes)
        return c

    official_at = scraped.official_at
    prior = session.scalar(select(Race.place_odds_official_at).where(Race.race_id == race_id))
    if official_at is not None and prior is not None and official_at < prior:
        c.skipped += len(started)
        return c

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
    session.execute(
        update(Race).where(Race.race_id == race_id).values(
            place_odds_official_at=official_at,
            place_odds_observed_at=datetime.datetime.now(datetime.UTC),
        )
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
    return v if v > 0 else None      # 負や 0 は入力が壊れている合図。埋めない。


def backfill_results(session: Session, race_id: str, scraped: ScrapedResult) -> Counts:
    c = Counts()
    started = set(
        session.scalars(
            select(RaceHorse.horse_id)
            .where(RaceHorse.race_id == race_id)
            .where(RaceHorse.entry_status == EntryStatus.STARTED)
        )
    )
    field_size = len(started)
    distance = session.scalar(select(Race.distance).where(Race.race_id == race_id))
    # winner time anchors finish_time_diff (seconds behind winner — JRA-VAN-consistent interval)
    winner_td = next(
        (parse_netkeiba_time(r.finish_time) for r in scraped.rows if r.finish_order == 1), None
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
        own_td = parse_netkeiba_time(row.finish_time)
        diff = own_td - winner_td if (own_td is not None and winner_td is not None) else None
        # INSERT-ONLY: never overwrite an existing (JRA-VAN) race_results row
        session.execute(
            insert(RaceResult).values(
                race_id=race_id, horse_id=horse_id, finish_order=row.finish_order,
                result_status=row.result_status, finish_time=own_td, finish_time_diff=diff,
                last_3f=row.last_3f,
                first_3f=_derive_first_3f(distance, own_td, row.last_3f),
                corner_orders=list(row.corner_orders) if row.corner_orders else None,
            ).on_conflict_do_nothing(index_elements=["race_id", "horse_id"])
        )
        # corner_orders fill-NULL-only. The result page fetched on race night carries the
        # finishing order and times but NOT yet the 通過順 (measured 2026-07/08: rows created the
        # same night were 100% NULL, rows fetched days later 0%); with the insert above being
        # INSERT-ONLY that gap never closed, and corner_trajectory/position_style/pace_scenario
        # as-of features were silently starving. Only the NULL cell is filled — finish_order /
        # status / times stay whatever was inserted first (JRA-VAN rows are never clobbered, and a
        # JRA-VAN row already has its corners so this never matches one).
        if row.corner_orders:
            session.execute(
                update(RaceResult)
                .where(RaceResult.race_id == race_id, RaceResult.horse_id == horse_id,
                       RaceResult.corner_orders.is_(None))
                .values(corner_orders=list(row.corner_orders))
            )
        # B: fill-if-null derived 脚質 (never clobbers a JRA-VAN running_style)
        style = _derive_running_style(row.corner_orders, field_size)
        if style is not None:
            session.execute(
                update(RaceHorse)
                .where(RaceHorse.race_id == race_id, RaceHorse.horse_id == horse_id,
                       RaceHorse.running_style.is_(None))
                .values(running_style=style)
            )
        c.written += 1
    return c


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
    session: Session, race_id: str, rows: Iterable[ScrapedResultRow]
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
      stay pre-race — surfaced per race, like a missing 単勝 payout).
    """
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
            select(RaceHorse.horse_id, RaceHorse.odds, RaceHorse.popularity)
            .where(RaceHorse.race_id == race_id)
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

    @property
    def written(self) -> int:
        return self.inserted + self.updated + self.removed


def upsert_official_win_payouts(
    session: Session,
    race_id: str,
    payouts: Sequence[ScrapedWinPayout],
    *,
    observed_at: datetime.datetime,
    html_sha256: str | None = None,
) -> WinPayoutCounts:
    """Store the race's official 単勝 payouts (single latest value per 馬番, constitution V).

    A row is (re)written only when its ``payout_yen`` differs from what is stored; ``observed_at``
    and ``html_sha256`` then record the observation that set the value. Re-reading the same payout
    — the same page again, or a later fetch of it — changes nothing, so a re-run reports 0.

    When the page lists winners, a stored winner it no longer lists is deleted (an official
    correction, e.g. a 降着 that changes the winner). An EMPTY list writes nothing and deletes
    nothing: a page without a 単勝 row is not evidence that the race paid nobody.
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
    if not session.scalar(select(exists().where(Race.race_id == race_id))):
        out.no_race = 1
        return out

    stored = dict(session.execute(
        select(OfficialWinPayout.horse_number, OfficialWinPayout.payout_yen)
        .where(OfficialWinPayout.race_id == race_id)
    ).tuples().all())
    stale = sorted(set(stored) - set(by_number))
    if stale:
        res = session.execute(
            delete(OfficialWinPayout).where(
                OfficialWinPayout.race_id == race_id,
                OfficialWinPayout.horse_number.in_(stale),
            )
        )
        out.removed += res.rowcount

    for number, payout_yen in sorted(by_number.items()):
        if stored.get(number) == payout_yen:
            out.unchanged += 1
            continue
        stmt = insert(OfficialWinPayout).values(
            race_id=race_id, horse_number=number, payout_yen=payout_yen,
            observed_at=observed_at, html_sha256=html_sha256,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["race_id", "horse_number"],
            set_={"payout_yen": stmt.excluded.payout_yen,
                  "observed_at": stmt.excluded.observed_at,
                  "html_sha256": stmt.excluded.html_sha256},
            where=OfficialWinPayout.payout_yen.is_distinct_from(stmt.excluded.payout_yen),
        )
        session.execute(stmt)
        if number in stored:
            out.updated += 1
        else:
            out.inserted += 1
    return out


def bloodline_for(session: Session, *, line_col: str, name_col: str, name: str) -> str | None:
    """The single bloodline line recorded for ``name`` among horses that already carry one.

    Returns None when the name is unknown OR maps to more than one distinct line (ambiguous —
    never pick one). Exact-name match on purpose: a looser match would be a guess."""
    line_attr, name_attr = getattr(Horse, line_col), getattr(Horse, name_col)
    lines = list(session.scalars(
        select(line_attr).where(name_attr == name, line_attr.isnot(None)).distinct()
    ))
    return lines[0] if len(lines) == 1 else None


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
            line = bloodline_for(session, line_col=line_col, name_col=name_col,
                                 name=getattr(horse, name_col))
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


# --- race laps (034) --------------------------------------------------------


def upsert_laps(session: Session, race_id: str, scraped: ScrapedLaps) -> Counts:
    """Store the race-level sectional lap profile with single-latest-value overwrite (constitution
    V). RESULT-derived; written only when the race row exists (FK). Skips empty lap arrays."""
    c = Counts()
    c.processed += 1
    if not scraped.lap_times:
        c.skipped += 1
        return c
    if not session.scalar(select(exists().where(Race.race_id == race_id))):
        c.skipped += 1   # no race row yet → nothing to attach laps to
        return c
    first = None if scraped.pace_first_3f is None else Decimal(str(scraped.pace_first_3f))
    last = None if scraped.pace_last_3f is None else Decimal(str(scraped.pace_last_3f))
    laps = [float(x) for x in scraped.lap_times]
    stmt = insert(RaceLaps).values(
        race_id=race_id, lap_times=laps, pace_first_3f=first, pace_last_3f=last, source="netkeiba",
    ).on_conflict_do_update(
        index_elements=["race_id"],
        set_={"lap_times": laps, "pace_first_3f": first, "pace_last_3f": last},
    )
    session.execute(stmt)
    c.written += 1
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
    stmt = insert(ExoticQuote).values(
        race_id=scraped.race_id, bet_type=scraped.bet_type, quotes=payload,
        n_combinations=len(payload), official_at=scraped.official_at,
        observed_at=datetime.datetime.now(datetime.UTC), source="netkeiba",
    ).on_conflict_do_update(
        constraint="uq_exotic_quotes_race_bettype",
        set_={"quotes": payload, "n_combinations": len(payload),
              "official_at": scraped.official_at,
              "observed_at": datetime.datetime.now(datetime.UTC)},
    )
    session.execute(stmt)
    c.written += 1
    return c
