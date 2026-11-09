"""JRDB の書込み層 (Feature 178, T048/T049; contracts/precedence-jrdb.md). 共通 writer を呼ぶ薄い層.

ここでやること: 値オブジェクト → 共通 writer の入力への写像(正規 ID・DB の語彙)、UKC からの
馬マスタ、
人物コード → ``id_mappings(source=jrdb)`` 経由の正規 ID(未解決は NULL + ``unresolved_person``)、
``nk:X`` 行への別名(176 ``jra/alias.py`` をそのまま呼ぶ)、レースごとの savepoint と
``race_status``、書込み先プロファイルの第 2 ガード(``profile.check_payload``)。規則そのもの
(衝突・遷移・オッズ・結果・払戻)は ``precedence.py`` と ``upsert.py`` にあり、ここでは再実装しない。

JRDB は行もマスタも作ってよい(precedence-jrdb §3「行・マスタの生成 = write」)が、既存行がある
レースでは
176 と同じ衝突検査と遷移表を通る。``win_odds``/``place_odds`` には触らない(TYB も基準オッズも)。
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from horseracing_db.enums import BetType, EntityType, EntryStatus, MappingStatus, ResultStatus
from horseracing_db.models import Horse, IdMapping, RaceHorse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import precedence as P
from ..jra import alias as A
from ..models import (
    ResolvedEntryHorse,
    ResolvedResultRow,
    ScrapedLaps,
    ScrapedRaceKey,
    ScrapedResultRow,
    ScrapedWinPayout,
)
from ..provenance import write_horse_provenance
from ..upsert import (
    NETKEIBA_FINAL_ODDS_FROM,
    OFFICIAL_WIN_PAYOUT_SOURCE_JRDB,
    RACE_ATOMIC,
    Counts,
    _insert_results,
    apply_final_odds,
    lock_race,
    replace_exotic_dividends,
    upsert_laps,
    upsert_official_win_payouts,
    write_entries,
)
from . import normalize as nz
from .ids import horse_id_from_blood_no, race_id_from_key
from .models import (
    JrdbEntry,
    JrdbHorse,
    JrdbPayouts,
    JrdbPreRace,
    JrdbRaceProgram,
    JrdbRaceResult,
    JrdbResult,
)
from .profile import check_payload, profile_from_arg

JRDB = P.JRDB
JST = datetime.timezone(datetime.timedelta(hours=9))

#: 条件コード → the DB spelling the model's majority vocabulary (JRA-VAN) uses
RACE_CLASS_DB = {
    "A1": "新馬",
    "A2": "未出走",
    "A3": "未勝利",
    "04": "1勝",
    "05": "1勝",
    "08": "2勝",
    "09": "2勝",
    "10": "2勝",
    "15": "3勝",
    "16": "3勝",
    "OP": "オープン",
}
GRADE_DB = {1: "G1", 2: "G2", 3: "G3", 6: "L"}  # 4 重賞 / 5 特別 are not grades in the DB


# --------------------------------------------------------------------------- race status


@dataclass
class RaceStatus:
    """Per-race outcome of one file ingest (→ ``jrdb_files.race_status``)."""

    applied: dict[str, dict] = field(default_factory=dict)

    def set(self, race_id: str, status: str, reason: str | None, now: datetime.datetime) -> None:
        self.applied[race_id] = {"status": status, "reason": reason, "applied_at": now.isoformat()}

    def file_status(self) -> str:
        values = {v["status"] for v in self.applied.values()}
        if not values:
            return "fetched"
        if values == {"applied"} or values <= {"applied", "skipped"}:
            return "applied"
        return "partially_applied"


def merge_race_status(existing: Mapping[str, dict] | None, new: RaceStatus) -> dict:
    out = dict(existing or {})
    out.update(new.applied)
    return out


def unapplied_races(existing: Mapping[str, dict] | None, race_ids: Iterable[str]) -> list[str]:
    """Same sha re-ingest: only races not yet ``applied`` are processed (INV-F4)."""

    done = {rid for rid, v in (existing or {}).items() if v.get("status") == "applied"}
    return [r for r in race_ids if r not in done]


# --------------------------------------------------------------------------- person mapping


PersonMap = Mapping[tuple[str, str], str]  # (kind, JRDB code) → canonical id


def load_person_map(session: Session) -> dict[tuple[str, str], str]:
    rows = session.execute(
        select(IdMapping.entity_type, IdMapping.source_id, IdMapping.canonical_id).where(
            IdMapping.source == JRDB,
            IdMapping.mapping_status == MappingStatus.MAPPED,
            IdMapping.canonical_id.is_not(None),
        )
    ).all()
    return {(str(k), str(code)): str(cid) for k, code, cid in rows}


def _person(pm: PersonMap, kind: str, code: str | None, c: Counts, rid: str) -> str | None:
    if code is None:
        return None
    cid = pm.get((kind, code))
    if cid is None:
        c.bump("unresolved_person")
        c.error_messages.append(f"{rid}: {kind} code {code} unresolved (left NULL)")
    return cid


# --------------------------------------------------------------------------- era / vocabulary


def era_ok(race_date: datetime.date | None, profile) -> bool:
    """On the current DB the JRDB writer never touches a JRA-VAN-era race (the repair CLI does,
    column by column); the canonical DB (段 3b) takes every year."""

    if race_date is None:
        return False
    if profile_from_arg(profile).name == "jrdb_canonical":
        return True
    return race_date >= NETKEIBA_FINAL_ODDS_FROM


def race_values_of(p: JrdbRaceProgram, race_id: str) -> dict:
    post_time = (
        datetime.datetime.combine(p.race_date, p.post_time, tzinfo=JST) if p.post_time else None
    )
    return {
        "race_date": p.race_date,
        "race_number": p.key.r,
        "venue_code": p.key.venue,
        "distance": p.distance,
        "track_type": nz.track_from_code(p.track_code),
        "going": None,  # BAC has no going/weather; SED (results) carries them
        "weather": None,
        "race_class": RACE_CLASS_DB.get(p.joken_code or "", None),
        "race_name": p.race_name,
        "race_name_short": p.race_name_short,
        "grade": GRADE_DB.get(p.grade_code) if p.grade_code is not None else None,
        "post_time": post_time,
        "prize_money": p.prizes_man[0],
    }


def _rid(key, year: int) -> str | None:
    try:
        return race_id_from_key(key, file_year=year)
    except Exception:  # noqa: BLE001
        return None


def _hid(blood: str, race_year: int, birth: datetime.date | None = None) -> str | None:
    try:
        return horse_id_from_blood_no(blood, race_year=race_year, birth_date=birth)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- masters (UKC)


def write_horse_master(
    session: Session,
    hid: str,
    h: JrdbHorse | None,
    *,
    profile,
    name: str | None,
    sex_code: int | None,
) -> int:
    """INSERT the horse master when absent; otherwise fill NULL columns only (never overwrite).
    Pedigree names / owner / breeder come from UKC; the line codes only under ``jrdb_canonical``."""

    prof = profile_from_arg(profile)
    values: dict = {
        "horse_name": (h.horse_name if h else None) or name,
        "sex": nz.sex_from_code(h.sex_code if h else sex_code),
        "birth_year": h.birth_date.year if h and h.birth_date else int(hid[:4]),
        "sire_name": h.sire_name if h else None,
        "dam_name": h.dam_name if h else None,
        "damsire_name": h.damsire_name if h else None,
        "owner_name": h.owner_name if h else None,
        "breeder_name": h.breeder_name if h else None,
        "data_source": JRDB,
    }
    if prof.includes_canonical and h is not None:
        values["sire_line"] = h.sire_line_code
        values["damsire_line"] = h.damsire_line_code
    values = {k: v for k, v in values.items() if v is not None}
    check_payload(prof, "horses", values)
    existing = session.get(Horse, hid)
    if existing is None:
        session.add(Horse(horse_id=hid, **values))
        session.flush()
        return 1
    fills = {k: v for k, v in values.items() if k != "data_source" and getattr(existing, k) is None}
    if fills:
        session.execute(update(Horse).where(Horse.horse_id == hid).values(**fills))
        return 1
    return 0


# --------------------------------------------------------------------------- entries


def upsert_jrdb_entries(
    session: Session,
    programs: Sequence[JrdbRaceProgram],
    entries: Sequence[JrdbEntry],
    horses: Sequence[JrdbHorse],
    *,
    profile,
    year: int,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None,
    person_map: PersonMap,
    race_status: RaceStatus,
    only_races: Iterable[str] | None = None,
) -> Counts:
    """BAC + KYI (+ UKC) → races / race_horses / horses / provenance, one savepoint per race."""

    prof = profile_from_arg(profile)
    c = Counts()
    ukc = {}
    for h in horses:
        hid = _hid(h.blood_reg_no, year, h.birth_date)
        if hid:
            ukc[hid] = h
    by_race: dict[str, list[JrdbEntry]] = {}
    for e in entries:
        rid = _rid(e.key, year)
        if rid is not None:
            by_race.setdefault(rid, []).append(e)
    prog_by_rid = {rid: p for p in programs if (rid := _rid(p.key, year))}
    wanted = set(only_races) if only_races is not None else None
    for rid in sorted(by_race):
        if wanted is not None and rid not in wanted:
            continue
        rows = by_race[rid]
        prog = prog_by_rid.get(rid)
        race_date = prog.race_date if prog else datetime.date(year, 1, 1)
        if prog is None:
            race_status.set(rid, "skipped", "no_bac_program", observed_at)
            c.skipped += len(rows)
            c.bump("skipped_no_program")
            continue
        if not era_ok(race_date, prof):
            race_status.set(rid, "skipped", "pre_netkeiba_era", observed_at)
            c.skipped += len(rows)
            c.bump("skipped_pre_netkeiba_era")
            continue
        try:
            with session.begin_nested():
                snap = lock_race(
                    session,
                    rid,
                    race_defaults={
                        "race_date": race_date,
                        "race_number": prog.key.r,
                        "venue_code": prog.key.venue,
                    },
                )
                resolved: list[ResolvedEntryHorse] = []
                official = []
                hids = {}
                for e in rows:
                    base = _hid(e.blood_reg_no, year)
                    birth_known = ukc[base].birth_date if base in ukc else None
                    hid = _hid(e.blood_reg_no, year, birth_known)
                    if hid is None:
                        raise ValueError(
                            f"{rid}: unresolvable blood number for 馬番 {e.horse_number}"
                        )
                    hids[id(e)] = hid
                    official.append(A.OfficialHorse(hid, e.horse_number, e.horse_name))
                stored_id = {}
                if snap.horses:
                    aliases = A.resolve_horses(session, snap.horses, official, require_all=False)
                    if aliases.reason is not None and aliases.reason != A.MISSING:
                        c.bump(aliases.reason)
                        c.bump("quarantined_collision")
                        c.error_messages.append(
                            f"quarantined {rid}: {aliases.reason} {aliases.problems}"
                        )
                        race_status.set(rid, "failed", aliases.reason, observed_at)
                        c.skipped += len(rows)
                        continue
                    if aliases.aliased:
                        c.bump("alias_horses", aliases.aliased)
                    stored_id = {x: rh.horse_id for x, rh in aliases.rows.items()}
                for e in rows:
                    hid = hids[id(e)]
                    sid = stored_id.get(hid, hid)
                    if sid == hid:  # a canonical row: the master is ours to create / fill
                        write_horse_master(
                            session,
                            hid,
                            ukc.get(hid),
                            profile=prof,
                            name=e.horse_name,
                            sex_code=e.sex_code,
                        )
                    jockey_id = _person(person_map, EntityType.JOCKEY, e.jockey_code, c, rid)
                    trainer_id = _person(person_map, EntityType.TRAINER, e.trainer_code, c, rid)
                    birth = ukc[hid].birth_date if hid in ukc and ukc[hid].birth_date else None
                    check_payload(
                        prof,
                        "race_horses",
                        {
                            "frame": e.frame,
                            "horse_number": e.horse_number,
                            "jockey_id": jockey_id,
                            "trainer_id": trainer_id,
                            "weight": e.weight,
                            "weight_diff": e.weight_diff,
                            "jockey_weight": e.carried_weight,
                            "sex": None,
                            "age": None,
                            "entry_status": None,
                        },
                    )
                    resolved.append(
                        ResolvedEntryHorse(
                            horse_id=sid,
                            horse_name=e.horse_name,
                            frame=e.frame,
                            horse_number=e.horse_number,
                            jockey_id=jockey_id,
                            jockey_name=e.jockey_name,
                            trainer_id=trainer_id,
                            trainer_name=e.trainer_name,
                            weight=e.weight,
                            weight_diff=e.weight_diff,
                            jockey_weight=float(e.carried_weight)
                            if e.carried_weight is not None
                            else None,
                            sex=nz.sex_from_code(e.sex_code),
                            age=(race_date.year - birth.year)
                            if birth
                            else (race_date.year - int(hid[:4])),
                            entry_status=EntryStatus.CANCELLED
                            if e.cancelled
                            else EntryStatus.STARTED,
                            birth_year=birth.year if birth else int(hid[:4]),
                            data_source=JRDB,
                        )
                    )
                values = race_values_of(prog, rid)
                check_payload(
                    prof,
                    "races",
                    {
                        k: v
                        for k, v in values.items()
                        if k not in ("race_date", "race_number", "venue_code")
                    },
                )
                out = write_entries(
                    session,
                    rid,
                    values,
                    resolved,
                    source=JRDB,
                    observed_at=observed_at,
                    job_id=job_id,
                    page_kind=P.PAGE_ENTRIES,
                )
                c.merge(out)
                if out.extra.get("quarantined_collision"):
                    race_status.set(rid, "failed", "collision", observed_at)
                else:
                    race_status.set(rid, "applied", None, observed_at)
                    c.bump("races_written")
        except Exception as exc:  # noqa: BLE001 — one race's failure never aborts the file
            c.errors += 1
            c.error_messages.append(f"{rid}: {type(exc).__name__}: {exc}")
            race_status.set(rid, "failed", type(exc).__name__, observed_at)
    return c


# --------------------------------------------------------------------------- 直前 (TYB)


def upsert_jrdb_prerace(
    session: Session,
    rows: Sequence[JrdbPreRace],
    *,
    profile,
    year: int,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None,
    race_status: RaceStatus,
) -> Counts:
    """TYB → body weight (pair rule, timed) and 取消 (one-way) through the transition table.
    Odds on the TYB are never read (layout ``skip``); nothing else is written."""

    prof = profile_from_arg(profile)
    c = Counts()
    by_race: dict[str, list[JrdbPreRace]] = {}
    for r in rows:
        rid = _rid(r.key, year)
        if rid:
            by_race.setdefault(rid, []).append(r)
    for rid in sorted(by_race):
        try:
            with session.begin_nested():
                snap = lock_race(session, rid)
                if snap.race is None or not snap.horses:
                    race_status.set(rid, "skipped", "race_not_in_db", observed_at)
                    c.skipped += len(by_race[rid])
                    continue
                if not era_ok(snap.race.race_date, prof):
                    race_status.set(rid, "skipped", "pre_netkeiba_era", observed_at)
                    c.skipped += len(by_race[rid])
                    continue
                by_number = snap.by_number()
                for r in by_race[rid]:
                    c.processed += 1
                    rh = by_number.get(r.horse_number)
                    if rh is None:
                        c.skipped += 1
                        continue
                    if r.cancelled:
                        ctx = P.TransitionContext(
                            JRDB,
                            observed_at,
                            P.PAGE_CHANGES,
                            snap.prov.horse_source(rh.horse_id, P.GROUP_ENTRY_STATUS),
                            None,
                        )
                        t = P.field_transition(
                            P.GROUP_ENTRY_STATUS, rh.entry_status, EntryStatus.CANCELLED, ctx
                        )
                        if t.decision == "write":
                            check_payload(
                                prof, "race_horses", {"entry_status": EntryStatus.CANCELLED}
                            )
                            session.execute(
                                update(RaceHorse)
                                .where(RaceHorse.race_id == rid, RaceHorse.horse_id == rh.horse_id)
                                .values(entry_status=EntryStatus.CANCELLED)
                            )
                            write_horse_provenance(
                                session,
                                rid,
                                rh.horse_id,
                                P.GROUP_ENTRY_STATUS,
                                source=JRDB,
                                observed_at=observed_at,
                                job_id=job_id,
                            )
                            c.bump("status_transitions")
                            c.written += 1
                    if r.weight is not None or r.weight_diff is not None:
                        ctx = P.TransitionContext(
                            JRDB,
                            observed_at,
                            P.PAGE_ODDS,
                            snap.prov.horse_source(rh.horse_id, P.GROUP_BODY_WEIGHT),
                            snap.prov.horse_observed_at(rh.horse_id, P.GROUP_BODY_WEIGHT),
                        )
                        t = P.field_transition(
                            P.GROUP_BODY_WEIGHT,
                            (rh.weight, rh.weight_diff),
                            (r.weight, r.weight_diff),
                            ctx,
                        )
                        same = (rh.weight, rh.weight_diff) == (r.weight, r.weight_diff)
                        if (
                            t.decision == "write" and not same
                        ):  # a same-value re-ingest is not a write
                            check_payload(
                                prof,
                                "race_horses",
                                {"weight": r.weight, "weight_diff": r.weight_diff},
                            )
                            session.execute(
                                update(RaceHorse)
                                .where(RaceHorse.race_id == rid, RaceHorse.horse_id == rh.horse_id)
                                .values(weight=r.weight, weight_diff=r.weight_diff)
                            )
                            write_horse_provenance(
                                session,
                                rid,
                                rh.horse_id,
                                P.GROUP_BODY_WEIGHT,
                                source=JRDB,
                                observed_at=observed_at,
                                job_id=job_id,
                            )
                            c.bump("body_weight_written")
                            c.written += 1
                race_status.set(rid, "applied", None, observed_at)
        except Exception as exc:  # noqa: BLE001
            c.errors += 1
            c.error_messages.append(f"{rid}: {type(exc).__name__}: {exc}")
            race_status.set(rid, "failed", type(exc).__name__, observed_at)
    return c


# --------------------------------------------------------------------------- 結果 (SED 正式版・SRB)


def _time_text(td: datetime.timedelta | None) -> str | None:
    """timedelta → the common writer's ``M:SS.s`` spelling (``parse_netkeiba_time``)."""

    if td is None:
        return None
    total = round(td.total_seconds(), 1)
    m, s = divmod(total, 60)
    return f"{int(m)}:{s:04.1f}" if m else f"{s:.1f}"


def _result_status(abnormal: int | None) -> str | None:
    if abnormal in (None, 0, 5, 6):  # 降着 / 再騎乗 still finished
        return ResultStatus.FINISHED
    if abnormal == 3:
        return ResultStatus.STOPPED
    if abnormal == 4:
        return ResultStatus.DISQUALIFIED
    return None  # 1 取消 / 2 除外: no result row (entry_status instead)


def _write_canonical_only(session: Session, rid: str, rows, sid: dict, prof) -> None:
    """段 3b (``jrdb_canonical``) only: JRDB's own 前3F・通過順・脚質 on the rows just inserted.
    Under
    ``current`` this function is never reached (the profile check above), and ``check_payload``
    is the second guard."""

    from horseracing_db.models import RaceResult

    for r in rows:
        hid = sid.get(r.horse_number)
        if hid is None or _result_status(r.abnormal_code) is None:
            continue
        corners = [str(x) for x in (r.corner_orders or ()) if x is not None] or None
        res_vals = {"first_3f": r.first_3f, "corner_orders": corners}
        check_payload(prof, "race_results", res_vals)
        session.execute(
            update(RaceResult)
            .where(RaceResult.race_id == rid, RaceResult.horse_id == hid)
            .values(**res_vals)
        )
        if r.running_style_code is not None:
            check_payload(prof, "race_horses", {"running_style": r.running_style_code})
            session.execute(
                update(RaceHorse)
                .where(RaceHorse.race_id == rid, RaceHorse.horse_id == hid)
                .values(running_style=r.running_style_code)
            )


def _laps_differ(session: Session, rid: str, srb: JrdbRaceResult) -> bool:
    from horseracing_db.models import RaceLaps

    row = session.get(RaceLaps, rid)
    if row is None:
        return True
    return [float(x) for x in row.lap_times] != [
        float(x) for x in srb.lap_times
    ] or row.source != JRDB


def _laps_of(srb: JrdbRaceResult, rid: str, distance: int | None) -> ScrapedLaps:
    laps = tuple(float(x) for x in srb.lap_times)
    first3 = (
        round(sum(laps[:3]), 1) if distance and distance % 200 == 0 and len(laps) >= 3 else None
    )
    last3 = round(sum(laps[-3:]), 1) if len(laps) >= 3 else None
    return ScrapedLaps(
        ScrapedRaceKey(int(rid[0:4]), rid[4:6], int(rid[6:8]), int(rid[8:10]), int(rid[10:12])),
        laps,
        pace_first_3f=first3,
        pace_last_3f=last3,
    )


def upsert_jrdb_results(
    session: Session,
    results: Sequence[JrdbResult],
    race_results: Sequence[JrdbRaceResult],
    *,
    profile,
    edition: str | None,
    year: int,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None,
    race_status: RaceStatus,
) -> Counts:
    """SED 正式版 → race_results (race-atomic), 確定単勝オッズ (``apply_final_odds`` rules), body
    weight,
    取消/除外 corrections; SRB → race_laps. The 速報版 is refused before any write."""

    prof = profile_from_arg(profile)
    c = Counts()
    ok, reason = P.results_input_allowed(JRDB, edition)
    if not ok:
        c.bump(reason)
        c.skipped += len(results)
        return c
    by_race: dict[str, list[JrdbResult]] = {}
    for r in results:
        rid = _rid(r.key, year)
        if rid:
            by_race.setdefault(rid, []).append(r)
    srb_by_race = {rid: s for s in race_results if (rid := _rid(s.key, year))}
    for rid in sorted(by_race):
        rows = by_race[rid]
        try:
            with session.begin_nested():
                snap = lock_race(session, rid)
                if snap.race is None or not snap.horses:
                    race_status.set(rid, "skipped", "race_not_in_db", observed_at)
                    c.skipped += len(rows)
                    c.bump("results_race_not_in_db")
                    continue
                if not era_ok(snap.race.race_date, prof):
                    race_status.set(rid, "skipped", "pre_netkeiba_era", observed_at)
                    c.skipped += len(rows)
                    continue
                race_year = rows[0].race_date.year
                official = [
                    A.OfficialHorse(
                        _hid(r.blood_reg_no, race_year) or "?", r.horse_number, r.horse_name
                    )
                    for r in rows
                ]
                aliases = A.resolve_horses(session, snap.horses, official, require_all=True)
                if aliases.reason is not None:
                    c.bump(aliases.reason)
                    c.bump("quarantined_collision")
                    c.error_messages.append(
                        f"quarantined {rid}: {aliases.reason} {aliases.problems}"
                    )
                    race_status.set(rid, "failed", aliases.reason, observed_at)
                    c.skipped += len(rows)
                    continue
                if aliases.aliased:
                    c.bump("alias_horses", aliases.aliased)
                sid = {
                    r.horse_number: aliases.stored_id(_hid(r.blood_reg_no, race_year) or "?")
                    for r in rows
                }
                # 1) 取消/除外 carried by the result file
                for r in rows:
                    if r.abnormal_code not in (1, 2):
                        continue
                    rh = snap.horses.get(sid[r.horse_number])
                    target = EntryStatus.CANCELLED if r.abnormal_code == 1 else EntryStatus.EXCLUDED
                    if rh is None:
                        continue
                    ctx = P.TransitionContext(
                        JRDB,
                        observed_at,
                        P.PAGE_RESULTS,
                        snap.prov.horse_source(rh.horse_id, P.GROUP_ENTRY_STATUS),
                        None,
                    )
                    t = P.field_transition(P.GROUP_ENTRY_STATUS, rh.entry_status, target, ctx)
                    if t.decision == "write" and rh.entry_status != target:
                        check_payload(prof, "race_horses", {"entry_status": target})
                        session.execute(
                            update(RaceHorse)
                            .where(RaceHorse.race_id == rid, RaceHorse.horse_id == rh.horse_id)
                            .values(entry_status=target)
                        )
                        write_horse_provenance(
                            session,
                            rid,
                            rh.horse_id,
                            P.GROUP_ENTRY_STATUS,
                            source=JRDB,
                            observed_at=observed_at,
                            job_id=job_id,
                        )
                        c.bump("status_transitions")
                snap = lock_race(session, rid)
                # 2) race-atomic insert; the four JRDB-defined columns never reach ``current``
                result_rows = []
                for r in rows:
                    st = _result_status(r.abnormal_code)
                    if st is None:
                        continue
                    corners = None
                    if prof.includes_canonical and r.corner_orders:
                        corners = tuple(str(x) for x in r.corner_orders if x is not None) or None
                    check_payload(
                        prof,
                        "race_results",
                        {
                            "finish_order": r.finish_order,
                            "finish_time": None,
                            "finish_time_diff": None,
                            "last_3f": r.last_3f,
                            "result_status": st,
                            **({"corner_orders": corners} if corners is not None else {}),
                        },
                    )
                    result_rows.append(
                        ResolvedResultRow(
                            horse_id=sid[r.horse_number],
                            finish_order=r.finish_order or None,
                            result_status=st,
                            finish_time=_time_text(r.finish_time),
                            last_3f=float(r.last_3f) if r.last_3f is not None else None,
                            corner_orders=corners,
                        )
                    )
                ins = _insert_results(
                    session,
                    rid,
                    result_rows,
                    mode=RACE_ATOMIC,
                    source=JRDB,
                    observed_at=observed_at,
                    job_id=job_id,
                    snapshot=snap,
                )
                c.merge(ins)
                if prof.includes_canonical and ins.extra.get("results_inserted_races"):
                    _write_canonical_only(session, rid, rows, sid, prof)
                # 3) 確定単勝オッズ・人気 (the result-page repair's rules; ids already canonical)
                fo = apply_final_odds(
                    session,
                    rid,
                    [
                        ScrapedResultRow(
                            netkeiba_horse_id=sid[r.horse_number],
                            finish_order=r.finish_order,
                            result_status="finished",
                            finish_time=None,
                            win_odds=float(r.final_win_odds)
                            if r.final_win_odds is not None
                            else None,
                            popularity=r.final_win_popularity or None,
                        )
                        for r in rows
                        if r.abnormal_code not in (1, 2)
                    ],
                    observed_at=observed_at,
                    horse_id_of=lambda row: row.netkeiba_horse_id,
                    # the canonical DB (段 3b) holds every year; the current DB keeps the era guard
                    era_from=None if prof.includes_canonical else NETKEIBA_FINAL_ODDS_FROM,
                )
                for k in ("updated", "unchanged", "unreadable", "missing", "skipped_pre_netkeiba"):
                    v = getattr(fo, k)
                    if v:
                        c.bump(f"final_odds_{k}", v)
                # 4) body weight (確定値, page_kind=results)
                for r in rows:
                    rh = snap.horses.get(sid[r.horse_number])
                    if rh is None or (r.weight is None and r.weight_diff is None):
                        continue
                    ctx = P.TransitionContext(
                        JRDB,
                        observed_at,
                        P.PAGE_RESULTS,
                        snap.prov.horse_source(rh.horse_id, P.GROUP_BODY_WEIGHT),
                        snap.prov.horse_observed_at(rh.horse_id, P.GROUP_BODY_WEIGHT),
                    )
                    t = P.field_transition(
                        P.GROUP_BODY_WEIGHT,
                        (rh.weight, rh.weight_diff),
                        (r.weight, r.weight_diff),
                        ctx,
                    )
                    same = (rh.weight, rh.weight_diff) == (r.weight, r.weight_diff)
                    if t.decision == "write" and not same:  # a same-value re-ingest is not a write
                        check_payload(
                            prof, "race_horses", {"weight": r.weight, "weight_diff": r.weight_diff}
                        )
                        session.execute(
                            update(RaceHorse)
                            .where(RaceHorse.race_id == rid, RaceHorse.horse_id == rh.horse_id)
                            .values(weight=r.weight, weight_diff=r.weight_diff)
                        )
                        write_horse_provenance(
                            session,
                            rid,
                            rh.horse_id,
                            P.GROUP_BODY_WEIGHT,
                            source=JRDB,
                            observed_at=observed_at,
                            job_id=job_id,
                        )
                        c.bump("body_weight_written")
                # 5) laps (SRB) — JRDB writes even over netkeiba laps (same definition, contract §3)
                srb = srb_by_race.get(rid)
                if srb is not None and srb.lap_times and _laps_differ(session, rid, srb):
                    check_payload(
                        prof,
                        "race_laps",
                        {
                            "lap_times": None,
                            "pace_first_3f": None,
                            "pace_last_3f": None,
                            "source": JRDB,
                        },
                    )
                    lc = upsert_laps(
                        session,
                        rid,
                        _laps_of(srb, rid, snap.race.distance),
                        source=JRDB,
                        only_if_absent=False,
                        observed_at=observed_at,
                        job_id=job_id,
                    )
                    if lc.written:
                        c.bump("laps_written", lc.written)
                race_status.set(rid, "applied", None, observed_at)
                c.bump("races_written")
        except Exception as exc:  # noqa: BLE001
            c.errors += 1
            c.error_messages.append(f"{rid}: {type(exc).__name__}: {exc}")
            race_status.set(rid, "failed", type(exc).__name__, observed_at)
    return c


# --------------------------------------------------------------------------- 払戻 (HJC)

_EXOTIC = (
    ("place", BetType.PLACE),
    ("quinella", BetType.QUINELLA),
    ("wide", BetType.WIDE),
    ("exacta", BetType.EXACTA),
    ("trio", BetType.TRIO),
    ("trifecta", BetType.TRIFECTA),
)


def upsert_jrdb_payouts(
    session: Session,
    payouts: Sequence[JrdbPayouts],
    *,
    profile,
    year: int,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None,
    race_status: RaceStatus,
    html_sha256: str | None = None,
) -> Counts:
    """HJC → ``official_win_payouts`` (source ``jrdb_result``) + ``exotic_odds`` (6 券種・source
    ``jrdb``) as sets per bet type; 枠連 is not read. Official-owned same-value sets stay
    official."""

    prof = profile_from_arg(profile)
    c = Counts()
    for p in payouts:
        rid = _rid(p.key, year)
        if rid is None:
            continue
        try:
            with session.begin_nested():
                snap = lock_race(session, rid)
                if snap.race is None:
                    race_status.set(rid, "skipped", "race_not_in_db", observed_at)
                    c.skipped += 1
                    c.bump("payout_race_not_in_db")
                    continue
                if not era_ok(snap.race.race_date, prof):
                    race_status.set(rid, "skipped", "pre_netkeiba_era", observed_at)
                    c.skipped += 1
                    continue
                c.processed += 1
                if p.win:
                    check_payload(
                        prof,
                        "official_win_payouts",
                        {
                            "horse_number": None,
                            "payout_yen": None,
                            "source": OFFICIAL_WIN_PAYOUT_SOURCE_JRDB,
                            "observed_at": observed_at,
                        },
                    )
                    out = upsert_official_win_payouts(
                        session,
                        rid,
                        [ScrapedWinPayout(n, yen) for n, yen in p.win],
                        observed_at=observed_at,
                        html_sha256=html_sha256,
                        source=OFFICIAL_WIN_PAYOUT_SOURCE_JRDB,
                        job_id=job_id,
                    )
                    c.written += out.written
                    if out.confirmed:
                        c.bump("win_payout_confirmed", out.confirmed)
                    if out.skipped_official:
                        c.bump("win_payout_skipped_official")
                for attr, bet_type in _EXOTIC:
                    if attr == "place":
                        lines = {(n,): yen for n, yen in p.place}
                    else:
                        lines = {tuple(combo): yen for combo, yen in getattr(p, attr)}
                    if not lines:
                        continue
                    check_payload(
                        prof,
                        "exotic_odds",
                        {"bet_type": bet_type, "selection": None, "odds": None, "source": JRDB},
                    )
                    c.merge(
                        replace_exotic_dividends(
                            session,
                            rid,
                            bet_type,
                            lines,
                            source=JRDB,
                            observed_at=observed_at,
                            job_id=job_id,
                            snapshot=snap,
                        )
                    )
                race_status.set(rid, "applied", None, observed_at)
                c.bump("races_written")
        except Exception as exc:  # noqa: BLE001
            c.errors += 1
            c.error_messages.append(f"{rid}: {type(exc).__name__}: {exc}")
            race_status.set(rid, "failed", type(exc).__name__, observed_at)
    return c


__all__ = [
    "GRADE_DB",
    "RACE_CLASS_DB",
    "PersonMap",
    "RaceStatus",
    "era_ok",
    "load_person_map",
    "merge_race_status",
    "race_values_of",
    "unapplied_races",
    "upsert_jrdb_entries",
    "upsert_jrdb_payouts",
    "upsert_jrdb_prerace",
    "upsert_jrdb_results",
    "write_horse_master",
]
