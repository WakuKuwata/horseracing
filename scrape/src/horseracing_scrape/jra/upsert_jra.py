"""公式経路の書込み(Feature 176) — 共通 writer(``upsert.py``)を呼ぶ薄い層.

ここでやること: 期間ガード(``race_date < 2025-10-11`` は書かない=JRA-VAN 行の保護)、未知 ID の隔離、
取消馬の馬番復元、マスタの名前照合(報告のみ)、値オブジェクト→共通 writer の入力への写像。規則
そのもの(衝突・遷移・オッズ・結果・払戻)は共通 writer にあり、ここでは再実装しない。
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from horseracing_db.enums import BetType, EntryStatus, ResultStatus, Source
from horseracing_db.models import Horse, Jockey, Race, RaceHorse, Trainer
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import precedence as P
from ..models import (
    ResolvedEntryHorse,
    ResolvedResultRow,
    ScrapedLaps,
    ScrapedOdds,
    ScrapedOddsRow,
    ScrapedPlaceQuoteRow,
    ScrapedRaceKey,
    ScrapedWinPayout,
)
from ..provenance import write_horse_provenance, write_race_provenance
from ..upsert import (
    NETKEIBA_FINAL_ODDS_FROM,
    OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
    RACE_ATOMIC,
    Counts,
    _insert_results,
    lock_race,
    replace_exotic_dividends,
    update_odds,
    upsert_laps,
    upsert_official_win_payouts,
    write_entries,
)
from .identity import jra_name_key
from .models import (
    JraChange,
    JraDayPage,
    JraEntry,
    JraPayoutSet,
    JraRaceHeader,
    JraRaceResult,
    JraWinOdds,
)
from .parse.payouts import BRACKET_QUINELLA

JRA = Source.JRA_OFFICIAL


class CancelUnresolved(Exception):
    pass


def era_ok(race_date: datetime.date | None) -> bool:
    """FR-016 §7: the official route never writes a race of the JRA-VAN era."""
    return race_date is not None and race_date >= NETKEIBA_FINAL_ODDS_FROM


def race_values_of(race: JraRaceHeader) -> dict:
    return {
        "race_date": race.race_date,
        "race_number": race.race_no,
        "venue_code": race.venue_code,
        "distance": race.distance,
        "track_type": race.track_type,
        "going": race.going,
        "weather": race.weather,
        "race_class": race.race_class,
        "race_name": race.race_name,
        "grade": race.grade,
        "post_time": race.post_time,
        "prize_money": race.prize_money,
    }


# --- masters: name check is anomaly detection only (FR-010) ---------------------------------
def master_name_mismatches(session: Session, entry: JraEntry) -> list[str]:
    msgs: list[str] = []
    horse_ids = [h.horse_id for h in entry.horses if h.horse_id]
    jockey_ids = [h.jockey_id for h in entry.horses if h.jockey_id]
    trainer_ids = [h.trainer_id for h in entry.horses if h.trainer_id]
    horses = (
        {
            r.horse_id: r.horse_name
            for r in session.scalars(select(Horse).where(Horse.horse_id.in_(horse_ids)))
        }
        if horse_ids
        else {}
    )
    jockeys = (
        {
            r.jockey_id: r.jockey_name
            for r in session.scalars(select(Jockey).where(Jockey.jockey_id.in_(jockey_ids)))
        }
        if jockey_ids
        else {}
    )
    trainers = (
        {
            r.trainer_id: r.trainer_name
            for r in session.scalars(select(Trainer).where(Trainer.trainer_id.in_(trainer_ids)))
        }
        if trainer_ids
        else {}
    )
    for h in entry.horses:
        for kind, hid, name, table in (
            ("horse", h.horse_id, h.horse_name, horses),
            ("jockey", h.jockey_id, h.jockey_name, jockeys),
            ("trainer", h.trainer_id, h.trainer_name, trainers),
        ):
            if hid is None or hid not in table or table[hid] is None:
                continue
            if jra_name_key(table[hid]) != jra_name_key(name):
                msgs.append(
                    f"master_name_mismatch {kind} {hid}: stored={table[hid]!r} official={name!r}"
                )
    return msgs


# --- cancelled horses: recover the lost 馬番 from the changes page --------------------------
def resolve_cancelled_numbers(
    entry: JraEntry, changes: Sequence[JraChange], existing_by_id: dict[str, RaceHorse]
) -> dict[str, int]:
    """horse_id → 馬番 for every 取消/除外 horse whose number cell was blanked.

    Sources, in order: an existing race_horses row of the same official id, then the changes page
    (同じレースの 出走取消/競走除外 with a matching name key). Never the row order (FR-011 edge).
    Raises ``CancelUnresolved`` when one cannot be recovered."""
    out: dict[str, int] = {}
    by_name = {}
    for ch in changes:
        if (
            ch.race_id == entry.race.race_id
            and ch.kind in ("cancel", "exclude")
            and ch.horse_number
        ):
            by_name.setdefault(jra_name_key(ch.horse_name), []).append(ch.horse_number)
    for h in entry.horses:
        if h.horse_number is not None or h.horse_id is None:
            continue
        ex = existing_by_id.get(h.horse_id)
        if ex is not None and ex.horse_number is not None:
            out[h.horse_id] = ex.horse_number
            continue
        nums = by_name.get(jra_name_key(h.horse_name), [])
        if len(set(nums)) == 1:
            out[h.horse_id] = nums[0]
            continue
        raise CancelUnresolved(
            f"{entry.race.race_id}: 馬番 of cancelled {h.horse_name!r} unresolved"
        )
    return out


def _resolved_horses(entry: JraEntry, numbers: dict[str, int]) -> list[ResolvedEntryHorse]:
    year = entry.race.race_date.year
    out = []
    for h in entry.horses:
        out.append(
            ResolvedEntryHorse(
                horse_id=h.horse_id,  # type: ignore[arg-type] — unknown ids are quarantined before
                horse_name=h.horse_name,
                frame=h.frame,
                horse_number=h.horse_number
                if h.horse_number is not None
                else numbers.get(h.horse_id),
                jockey_id=h.jockey_id,
                jockey_name=h.jockey_name,
                trainer_id=h.trainer_id,
                trainer_name=h.trainer_name,
                weight=h.weight,
                weight_diff=h.weight_diff,
                jockey_weight=h.jockey_weight,
                sex=h.sex,
                age=h.age,
                entry_status=h.entry_status,
                birth_year=(year - h.age) if h.age is not None else None,
                data_source=JRA,
            )
        )
    return out


def upsert_jra_entries(
    session: Session,
    day_page: JraDayPage[JraEntry],
    changes: Sequence[JraChange] = (),
    *,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None = None,
    race_ids: Sequence[str] | None = None,
) -> Counts:
    """One venue's 出馬表 page → races / race_horses / masters / provenance, race by race."""
    c = Counts()
    blocks = sorted(
        (b for b in day_page.races if b.state == "published"), key=lambda b: b.race_id or ""
    )
    for block in blocks:
        entry: JraEntry = block.payload
        rid = entry.race.race_id
        if race_ids is not None and rid not in race_ids:
            continue
        if not era_ok(entry.race.race_date):
            c.skipped += len(entry.horses)
            c.bump("skipped_pre_netkeiba_era")
            continue
        if any(h.unknown_id_kinds for h in entry.horses):
            c.skipped += len(entry.horses)
            c.bump("quarantined_unknown_id")
            bad = [(h.horse_name, h.unknown_id_kinds) for h in entry.horses if h.unknown_id_kinds]
            c.error_messages.append(f"quarantined {rid}: unknown ids {bad}")
            continue
        try:
            with session.begin_nested():
                snap = lock_race(session, rid)  # resolve 馬番 under the lock (codex Q2-3)
                numbers = resolve_cancelled_numbers(entry, changes, snap.horses)
        except CancelUnresolved as exc:
            c.skipped += len(entry.horses)
            c.bump("quarantined_cancel_unresolved")
            c.error_messages.append(f"quarantined {exc}")
            continue
        mismatches = master_name_mismatches(session, entry)
        if mismatches:
            c.bump("master_name_mismatch", len(mismatches))
            c.error_messages.extend(f"{rid}: {m}" for m in mismatches)
        c.merge(
            write_entries(
                session,
                rid,
                race_values_of(entry.race),
                _resolved_horses(entry, numbers),
                source=JRA,
                observed_at=observed_at,
                job_id=job_id,
                page_kind=P.PAGE_ENTRIES,
            )
        )
    return c


# --- changes page -----------------------------------------------------------------------------
def _jockey_id_by_name(session: Session, name: str | None) -> str | None:
    if not name:
        return None
    key = jra_name_key(name)
    hits = [
        j.jockey_id for j in session.scalars(select(Jockey)) if jra_name_key(j.jockey_name) == key
    ]
    return hits[0] if len(hits) == 1 else None


def apply_jra_changes(
    session: Session,
    changes: Sequence[JraChange],
    *,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None = None,
    race_ids: Sequence[str] | None = None,
) -> Counts:
    """変更情報 → post_time / entry_status / jockey, each through the transition table under the
    race lock (page_kind=changes: the only page besides results that may correct 取消↔除外)."""
    c = Counts()
    by_race: dict[str, list[JraChange]] = {}
    for ch in changes:
        if race_ids is None or ch.race_id in race_ids:
            by_race.setdefault(ch.race_id, []).append(ch)
    for rid in sorted(by_race):
        race_date = session.scalar(select(Race.race_date).where(Race.race_id == rid))
        if race_date is None:
            c.skipped += len(by_race[rid])
            c.error_messages.append(f"changes {rid}: race not in DB (entries first)")
            continue
        if not era_ok(race_date):
            c.skipped += len(by_race[rid])
            c.bump("skipped_pre_netkeiba_era")
            continue
        with session.begin_nested():
            snap = lock_race(session, rid)
            by_number = snap.by_number()
            for ch in by_race[rid]:
                c.processed += 1
                if ch.kind == "post_time" and ch.new_post_time is not None:
                    ctx = P.TransitionContext(
                        JRA,
                        observed_at,
                        P.PAGE_CHANGES,
                        snap.prov.race_source(P.GROUP_POST_TIME),
                        None,
                    )
                    t = P.field_transition(
                        P.GROUP_POST_TIME, snap.race.post_time, ch.new_post_time, ctx
                    )
                    if t.decision == "write":
                        session.execute(
                            update(Race)
                            .where(Race.race_id == rid)
                            .values(post_time=ch.new_post_time)
                        )
                        write_race_provenance(
                            session,
                            rid,
                            P.GROUP_POST_TIME,
                            source=JRA,
                            observed_at=observed_at,
                            job_id=job_id,
                        )
                        c.bump("post_time_changes")
                        c.written += 1
                    continue
                rh = by_number.get(ch.horse_number) if ch.horse_number is not None else None
                if rh is None:
                    c.skipped += 1
                    c.error_messages.append(
                        f"changes {rid}: 馬番 {ch.horse_number} not in race_horses"
                    )
                    continue
                if ch.kind in ("cancel", "exclude"):
                    target = EntryStatus.CANCELLED if ch.kind == "cancel" else EntryStatus.EXCLUDED
                    ctx = P.TransitionContext(
                        JRA,
                        observed_at,
                        P.PAGE_CHANGES,
                        snap.prov.horse_source(rh.horse_id, P.GROUP_ENTRY_STATUS),
                        None,
                    )
                    t = P.field_transition(P.GROUP_ENTRY_STATUS, rh.entry_status, target, ctx)
                    if t.decision == "write":
                        session.execute(
                            update(RaceHorse)
                            .where(
                                RaceHorse.race_id == rid,
                                RaceHorse.horse_id == rh.horse_id,
                            )
                            .values(entry_status=target)
                        )
                        write_horse_provenance(
                            session,
                            rid,
                            rh.horse_id,
                            P.GROUP_ENTRY_STATUS,
                            source=JRA,
                            observed_at=observed_at,
                            job_id=job_id,
                        )
                        c.bump("status_transitions")
                        c.written += 1
                    elif t.decision == "reject":
                        c.bump("status_rejected")
                    continue
                if ch.kind == "jockey":
                    new_id = _jockey_id_by_name(session, ch.new_jockey_name)
                    if new_id is None:
                        c.skipped += 1
                        c.bump("jockey_change_unresolved")
                        c.error_messages.append(
                            f"changes {rid}: jockey {ch.new_jockey_name!r} not resolvable to one id"
                        )
                        continue
                    ctx = P.TransitionContext(
                        JRA,
                        observed_at,
                        P.PAGE_CHANGES,
                        snap.prov.horse_source(rh.horse_id, P.GROUP_JOCKEY),
                        snap.prov.horse_observed_at(rh.horse_id, P.GROUP_JOCKEY),
                    )
                    t = P.field_transition(P.GROUP_JOCKEY, rh.jockey_id, new_id, ctx)
                    if t.decision == "write":
                        # decide BEFORE the UPDATE: the ORM synchronises ``rh`` with the new value
                        changed = rh.jockey_id != new_id
                        session.execute(
                            update(RaceHorse)
                            .where(
                                RaceHorse.race_id == rid,
                                RaceHorse.horse_id == rh.horse_id,
                            )
                            .values(jockey_id=new_id)
                        )
                        write_horse_provenance(
                            session,
                            rid,
                            rh.horse_id,
                            P.GROUP_JOCKEY,
                            source=JRA,
                            observed_at=observed_at,
                            job_id=job_id,
                        )
                        if changed:
                            c.bump("jockey_changes")
                        c.written += 1
    return c


# --- 単複オッズ ---------------------------------------------------------------------------------
def popularity_ranks(odds_by_number: dict[int, float]) -> dict[int, int]:
    """単勝人気 = rank by win odds ascending, ties share the lower rank (competition ranking)."""
    ranks: dict[int, int] = {}
    ordered = sorted(odds_by_number.items(), key=lambda kv: kv[1])
    prev = None
    rank = 0
    for i, (n, o) in enumerate(ordered, start=1):
        if o != prev:
            rank = i
            prev = o
        ranks[n] = rank
    return ranks


@dataclass(frozen=True)
class OddsOutcome:
    counts: Counts


def upsert_jra_odds(
    session: Session,
    odds: JraWinOdds,
    *,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None = None,
) -> Counts:
    """One race's 単複 page → ``update_odds`` (shared contract) + body weights (pair rule).

    The page's horse ids are checked against the stored field under the lock: a 馬番 whose stored
    horse differs is a collision (quarantined, codex Q2-4), a missing 馬番 is just a partial field
    (``update_odds`` skips it)."""
    c = Counts()
    rid = odds.race_id
    if odds.kind == "prev_day_final":
        c.skipped += len(odds.rows)
        c.bump("odds_prev_day_final_ignored")
        return c
    race_date = session.scalar(select(Race.race_date).where(Race.race_id == rid))
    if race_date is None:
        c.skipped += len(odds.rows)
        c.error_messages.append(f"odds {rid}: race not in DB (entries first)")
        return c
    if not era_ok(race_date):
        c.skipped += len(odds.rows)
        c.bump("skipped_pre_netkeiba_era")
        return c
    with session.begin_nested():
        snap = lock_race(session, rid)
        by_number = snap.by_number()
        collisions = [
            (r.horse_number, by_number[r.horse_number].horse_id, r.horse_id)
            for r in odds.rows
            if r.horse_number is not None
            and r.horse_id is not None
            and r.horse_number in by_number
            and by_number[r.horse_number].horse_id != r.horse_id
        ]
        if collisions:
            c.skipped += len(odds.rows)
            c.bump("quarantined_collision")
            c.error_messages.append(f"quarantined {rid}: odds page ids differ {collisions}")
            return c
        # body weights (pair rule) for horses the page has weighed
        for r in odds.rows:
            rh = by_number.get(r.horse_number) if r.horse_number is not None else None
            if rh is None or (r.weight is None and r.weight_diff is None):
                continue
            ctx = P.TransitionContext(
                JRA,
                observed_at,
                P.PAGE_ODDS,
                snap.prov.horse_source(rh.horse_id, P.GROUP_BODY_WEIGHT),
                snap.prov.horse_observed_at(rh.horse_id, P.GROUP_BODY_WEIGHT),
            )
            t = P.field_transition(
                P.GROUP_BODY_WEIGHT, (rh.weight, rh.weight_diff), (r.weight, r.weight_diff), ctx
            )
            if t.decision == "write":
                session.execute(
                    update(RaceHorse)
                    .where(
                        RaceHorse.race_id == rid,
                        RaceHorse.horse_id == rh.horse_id,
                    )
                    .values(weight=r.weight, weight_diff=r.weight_diff)
                )
                write_horse_provenance(
                    session,
                    rid,
                    rh.horse_id,
                    P.GROUP_BODY_WEIGHT,
                    source=JRA,
                    observed_at=observed_at,
                    job_id=job_id,
                )
    win = {
        r.horse_number: r.win_odds
        for r in odds.rows
        if r.horse_number is not None
        and r.win_odds is not None
        and r.entry_status == EntryStatus.STARTED
    }
    ranks = popularity_ranks(win)
    rows = tuple(ScrapedOddsRow(n, o, ranks[n]) for n, o in win.items())
    place = tuple(
        ScrapedPlaceQuoteRow(r.horse_number, r.place_low, r.place_high, None)
        for r in odds.rows
        if r.horse_number is not None
        and r.place_low is not None
        and r.place_high is not None
        and r.entry_status == EntryStatus.STARTED
    )
    key = ScrapedRaceKey(int(rid[0:4]), rid[4:6], int(rid[6:8]), int(rid[8:10]), int(rid[10:12]))
    scraped = ScrapedOdds(key=key, rows=rows, place_rows=place, official_at=odds.official_at)
    c.merge(update_odds(session, rid, scraped, source=JRA, observed_at=observed_at, job_id=job_id))
    return c


# --- レース結果 ---------------------------------------------------------------------------------
def _race_key(rid: str) -> ScrapedRaceKey:
    return ScrapedRaceKey(int(rid[0:4]), rid[4:6], int(rid[6:8]), int(rid[8:10]), int(rid[10:12]))


def first_3f_from_laps(distance: int | None, laps: Sequence[float]) -> float | None:
    """テン3F = the first three 200m laps, defined only when every lap is a full 200m (the
    distance is a multiple of 200). Other distances start with a short lap; nothing is guessed."""
    if distance is None or distance % 200 != 0 or len(laps) < 3:
        return None
    return round(sum(laps[:3]), 1)


def _write_body_weight(
    session, snap, rid, rh, weight, diff, *, observed_at, job_id, page_kind
) -> bool:
    if weight is None and diff is None:
        return False
    ctx = P.TransitionContext(
        JRA,
        observed_at,
        page_kind,
        snap.prov.horse_source(rh.horse_id, P.GROUP_BODY_WEIGHT),
        snap.prov.horse_observed_at(rh.horse_id, P.GROUP_BODY_WEIGHT),
    )
    t = P.field_transition(P.GROUP_BODY_WEIGHT, (rh.weight, rh.weight_diff), (weight, diff), ctx)
    if t.decision != "write":
        return False
    session.execute(
        update(RaceHorse)
        .where(RaceHorse.race_id == rid, RaceHorse.horse_id == rh.horse_id)
        .values(weight=weight, weight_diff=diff)
    )
    write_horse_provenance(
        session,
        rid,
        rh.horse_id,
        P.GROUP_BODY_WEIGHT,
        source=JRA,
        observed_at=observed_at,
        job_id=job_id,
    )
    return True


_NON_STARTER = (EntryStatus.CANCELLED, EntryStatus.EXCLUDED)


def upsert_jra_results(
    session: Session,
    day_page: JraDayPage[JraRaceResult],
    *,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None = None,
    race_ids: Sequence[str] | None = None,
) -> Counts:
    """One venue's results page → race_results (race-atomic first insert), body weights, entry
    status corrections, laps (only when absent). Payouts are NOT written here: the CLI calls
    ``upsert_jra_payouts`` with the ``jra_payouts`` job id (codex Q2-6).

    The result page's 単勝人気 is never stored (user decision 2026-11-02); a disagreement with the
    stored popularity is only counted (``popularity_mismatch``)."""
    c = Counts()
    blocks = sorted(
        (b for b in day_page.races if b.state == "published"), key=lambda b: b.race_id or ""
    )
    for block in blocks:
        res: JraRaceResult = block.payload
        rid = res.race.race_id
        if race_ids is not None and rid not in race_ids:
            continue
        if not era_ok(res.race.race_date):
            c.skipped += len(res.rows)
            c.bump("skipped_pre_netkeiba_era")
            continue
        if any(r.unknown_id_kinds for r in res.rows):
            c.skipped += len(res.rows)
            c.bump("quarantined_unknown_id")
            bad = [(r.horse_name, r.unknown_id_kinds) for r in res.rows if r.unknown_id_kinds]
            c.error_messages.append(f"quarantined {rid}: unknown ids {bad}")
            continue
        with session.begin_nested():
            snap = lock_race(session, rid)
            if snap.race is None or not snap.horses:
                c.skipped += len(res.rows)
                c.bump("results_race_not_in_db")
                c.error_messages.append(f"results {rid}: race/entries not in DB (entries first)")
                continue
            incoming = [
                P.EntryRow(
                    r.horse_id,  # type: ignore[arg-type]
                    r.horse_number,
                    r.result_status if r.result_status in _NON_STARTER else EntryStatus.STARTED,
                )
                for r in res.rows
            ]
            report = P.check_entry_collisions(snap.entry_rows(), incoming)
            if report.quarantined:
                c.skipped += len(res.rows)
                c.bump("quarantined_collision")
                if report.merge_candidates:
                    c.bump("merge_candidates", len(report.merge_candidates))
                c.error_messages.append(f"quarantined {rid}: results collisions {report.kinds()}")
                continue
            # 1) 取消/除外 the result page carries — before the insert, so the started set the
            #    insert validates against is the corrected one
            for r in res.rows:
                if r.result_status not in _NON_STARTER:
                    continue
                rh = snap.horses.get(r.horse_id)
                if rh is None:
                    continue
                ctx = P.TransitionContext(
                    JRA,
                    observed_at,
                    P.PAGE_RESULTS,
                    snap.prov.horse_source(rh.horse_id, P.GROUP_ENTRY_STATUS),
                    None,
                )
                t = P.field_transition(P.GROUP_ENTRY_STATUS, rh.entry_status, r.result_status, ctx)
                if t.decision == "write" and rh.entry_status != r.result_status:
                    session.execute(
                        update(RaceHorse)
                        .where(RaceHorse.race_id == rid, RaceHorse.horse_id == rh.horse_id)
                        .values(entry_status=r.result_status)
                    )
                    write_horse_provenance(
                        session,
                        rid,
                        rh.horse_id,
                        P.GROUP_ENTRY_STATUS,
                        source=JRA,
                        observed_at=observed_at,
                        job_id=job_id,
                    )
                    c.bump("status_transitions")
                elif t.decision == "reject":
                    c.bump("status_rejected")
            snap = lock_race(session, rid)  # re-read after the status writes (same lock)
            # 2) race-atomic first insert of the result rows
            rows = [
                ResolvedResultRow(
                    horse_id=r.horse_id,  # type: ignore[arg-type] — unknown ids quarantined above
                    finish_order=r.finish_order,
                    result_status=r.result_status,
                    finish_time=r.finish_time,
                    last_3f=r.last_3f,
                    corner_orders=r.corner_orders,
                )
                for r in res.rows
                if r.result_status in ResultStatus.ALL
            ]
            c.merge(
                _insert_results(
                    session,
                    rid,
                    rows,
                    mode=RACE_ATOMIC,
                    source=JRA,
                    observed_at=observed_at,
                    job_id=job_id,
                    snapshot=snap,
                )
            )
            # 3) body weights (pair rule, page_kind=results)
            for r in res.rows:
                rh = snap.horses.get(r.horse_id)
                if rh is not None and _write_body_weight(
                    session,
                    snap,
                    rid,
                    rh,
                    r.weight,
                    r.weight_diff,
                    observed_at=observed_at,
                    job_id=job_id,
                    page_kind=P.PAGE_RESULTS,
                ):
                    c.bump("body_weight_written")
            # 4) popularity: audit material only, never written
            for r in res.rows:
                rh = snap.horses.get(r.horse_id)
                if (
                    rh is not None
                    and r.popularity is not None
                    and rh.popularity is not None
                    and rh.popularity != r.popularity
                ):
                    c.bump("popularity_mismatch")
        # 5) laps (race level, only when absent)
        if res.lap_times:
            laps = ScrapedLaps(
                _race_key(rid),
                tuple(res.lap_times),
                pace_first_3f=first_3f_from_laps(res.race.distance, res.lap_times),
                pace_last_3f=res.pace_last_3f,
            )
            lc = upsert_laps(
                session,
                rid,
                laps,
                source=JRA,
                only_if_absent=True,
                observed_at=observed_at,
                job_id=job_id,
            )
            for k in ("laps_inserted", "laps_existing"):
                if lc.extra.get(k):
                    c.bump(k, lc.extra[k])
    return c


# --- 払戻 -----------------------------------------------------------------------------------------
_STATE_KEY = {
    "complete": "payout_complete",
    "not_published": "payout_not_published",
    "not_sold": "payout_not_sold",
    "refund_only": "payout_refund_only",
    "parse_failed": "payout_parse_failed",
}
PAYOUT_SUMMARY_KEYS = (*_STATE_KEY.values(), "not_mapped_bet_types")


def upsert_jra_payouts(
    session: Session,
    race_id: str,
    payout_set: JraPayoutSet,
    *,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None = None,
    html_sha256: str | None = None,
) -> Counts:
    """One race's 払戻 → official_win_payouts (単勝) + exotic_odds (6 券種) as SETS per bet type.

    Each bet type is judged on its own block state (contracts/precedence.md §6): ``complete``
    replaces the set, ``not_published`` leaves the stored rows, ``not_sold`` / ``refund_only`` are
    reported, ``parse_failed`` skips only that bet type. 枠連 is parsed but has no table
    (``not_mapped_bet_types``)."""
    c = Counts()
    race_date = session.scalar(select(Race.race_date).where(Race.race_id == race_id))
    if race_date is None:
        c.skipped += 1
        c.bump("payout_race_not_in_db")
        c.error_messages.append(f"payouts {race_id}: race not in DB")
        return c
    if not era_ok(race_date):
        c.skipped += 1
        c.bump("skipped_pre_netkeiba_era")
        return c
    for bet_type, block in sorted(payout_set.by_bet_type.items()):
        if block.refunds:
            c.bump("refunds", len(block.refunds))
        if bet_type == BRACKET_QUINELLA:
            c.bump("not_mapped_bet_types")
            continue
        c.bump(_STATE_KEY[block.state])
        if block.state == "parse_failed":
            c.error_messages.append(f"payouts {race_id} {bet_type}: parse_failed ({block.error})")
            continue
        if block.state != "complete":
            continue
        c.processed += 1
        if bet_type == BetType.WIN:
            out = upsert_official_win_payouts(
                session,
                race_id,
                [ScrapedWinPayout(ln.numbers[0], ln.payout_yen) for ln in block.lines],
                observed_at=observed_at,
                html_sha256=html_sha256,
                source=OFFICIAL_WIN_PAYOUT_SOURCE_JRA,
                job_id=job_id,
            )
            c.written += out.written
            if out.confirmed:
                c.bump("win_payout_confirmed", out.confirmed)
            if out.removed:
                c.bump("payout_rows_removed", out.removed)
            continue
        lines = {tuple(ln.numbers): ln.payout_yen for ln in block.lines}
        c.merge(
            replace_exotic_dividends(
                session,
                race_id,
                bet_type,
                lines,
                source=JRA,
                observed_at=observed_at,
                job_id=job_id,
            )
        )
    return c


# --- 馬場(0024) -----------------------------------------------------------------------------------
def _num(v: float | None):
    from decimal import Decimal

    return None if v is None else Decimal(str(v))


def upsert_jra_baba(
    session: Session,
    readings: Sequence,
    *,
    fetched_at: datetime.datetime,
    raw_sha256: str,
    job_id: uuid.UUID | None = None,
) -> Counts:
    """Track-condition readings of ONE fetched page → ``jra_track_conditions`` (INV-B1/B2).

    * ``measured_at > fetched_at`` → ``baba_rejected_future`` (nothing written).
    * ``time_basis='measured'``: UPSERT on (venue, measured_at, item). Same value → nothing changes
      (``fetched_at`` included); a different value → overwrite + ``baba_value_changed``.
    * ``time_basis='fetched'`` (accessJ going/weather have no measurement time): a new row only
      when the value differs from the latest stored row of that (venue, item) — re-fetching the
      same state adds nothing, so the table records change points, not every fetch.

    ``job_id`` is accepted for symmetry with the other writers (the table has no job column)."""
    from horseracing_db.models import JraTrackCondition

    c = Counts()
    for r in readings:
        c.processed += 1
        if r.measured_at > fetched_at:
            c.skipped += 1
            c.bump("baba_rejected_future")
            continue
        value = (_num(r.value_num), r.value_text)
        if r.time_basis == "fetched":
            latest = session.scalars(
                select(JraTrackCondition)
                .where(
                    JraTrackCondition.venue_code == r.venue_code, JraTrackCondition.item == r.item
                )
                .order_by(JraTrackCondition.measured_at.desc())
                .limit(1)
            ).first()
            if latest is not None and (latest.value_num, latest.value_text) == value:
                c.skipped += 1
                continue
        existing = session.get(JraTrackCondition, (r.venue_code, r.measured_at, r.item))
        if existing is None:
            session.add(
                JraTrackCondition(
                    venue_code=r.venue_code,
                    measured_at=r.measured_at,
                    item=r.item,
                    time_basis=r.time_basis,
                    value_num=value[0],
                    value_text=value[1],
                    fetched_at=fetched_at,
                    raw_sha256=raw_sha256,
                    source=JRA,
                )
            )
            c.written += 1
            c.bump("baba_rows_written")
            continue
        if (existing.value_num, existing.value_text) == value:
            c.skipped += 1
            continue
        existing.value_num, existing.value_text = value
        existing.time_basis = r.time_basis
        existing.fetched_at = fetched_at
        existing.raw_sha256 = raw_sha256
        c.written += 1
        c.bump("baba_rows_written")
        c.bump("baba_value_changed")
    session.flush()
    return c
