"""段 1 parity 報告 (Feature 178, T028/T034; contracts/parity.md). READ ONLY.

Inputs: the year's decoded JRDB files (BAC / KYI / UKC / SED / SRB / HJC, decoded under
``profile="jrdb_canonical"`` so the four JRDB-defined columns are REPORTED — never written) and a
:class:`Reference` of the DB rows for the same year (loaded through a read-only transaction).

Output: the JSON of contracts/parity.md — per column the human-written ``meaning`` (transcribed
from the contract, never inferred by code), the denominator, the agreement rate by
距離帯 × 芝ダ障, up to ten disagreement examples — plus ``uncovered_reference_cells``: for every
non-null DB cell of the reference columns, whether a uniquely corresponding JRDB value exists
(``row_missing`` / ``id_unresolved`` / ``value_missing`` / ``definition_mismatch``). The four
JRDB-defined columns are listed as ``definition_mismatch`` only and 0 is not required of them.

Pure Python (no pandas): the report is a handful of dict passes over a year of rows.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from . import normalize as nz
from .ids import horse_id_from_blood_no, race_id_from_key
from .models import JrdbEntry, JrdbHorse, JrdbPayouts, JrdbRaceProgram, JrdbResult

# --------------------------------------------------------------------------- contract tables


def _rule(meaning, tolerance, threshold, table, note=""):
    return {
        "meaning": meaning,
        "tolerance": tolerance,
        "threshold": threshold,
        "table": table,
        "note": note,
    }


#: contracts/parity.md「判定(列ごと・事前固定)」 — meaning / tolerance / threshold, transcribed.
COLUMN_RULES: dict[str, dict[str, Any]] = {
    "finish_order": _rule("same", 0, 0.999, "race_results"),
    "finish_time": _rule("same", 0, 0.999, "race_results"),
    "finish_time_diff": _rule("normalized", 0.1, 0.99, "race_results"),
    "last_3f": _rule("same", 0.1, 0.99, "race_results"),
    "first_3f": _rule(
        "different",
        0.1,
        0.99,
        "race_results",
        "1200m のみ恒等式で一致・他距離は位置取りからの推定(別定義)",
    ),
    "corner_orders": _rule(
        "different",
        0,
        None,
        "race_results",
        "JRDB は 4 枠に先頭側から詰める・値も先頭の角で ±1(比較だけ)",
    ),
    "horse_number": _rule("same", 0, 0.999, "race_horses"),
    "frame": _rule("same", 0, 0.999, "race_horses"),
    "sex": _rule("same", 0, 0.999, "race_horses"),
    "odds": _rule("same", 0, 0.999, "race_horses", "確定単勝オッズ(正式版のみ)"),
    "popularity": _rule("same", 0, 0.999, "race_horses"),
    "weight": _rule("same", 0, 0.999, "race_horses"),
    "weight_diff": _rule("same", 0, 0.999, "race_horses", "JRDB 空 = DB 0 (初出走) を同値"),
    "jockey_weight": _rule("normalized", 0.1, 0.999, "race_horses", "見習い減量 5 行差 → 要確認"),
    "running_style": _rule("different", 0, None, "race_horses", "語彙が違う(比較だけ)"),
    "jockey_id": _rule("different", 0, None, "race_horses", "JRDB 独自コード → id_mappings"),
    "trainer_id": _rule("different", 0, None, "race_horses", "JRDB 独自コード → id_mappings"),
    "horse_name": _rule("same", 0, 0.999, "horses"),
    "sire_name": _rule("normalized", 0, 0.995, "horses"),
    "dam_name": _rule("normalized", 0, 0.995, "horses"),
    "damsire_name": _rule("normalized", 0, 0.995, "horses"),
    "birth_year": _rule("normalized", 0, 0.999, "horses"),
    "owner_name": _rule("normalized", 0, 0.995, "horses"),
    "breeder_name": _rule("normalized", 0, 0.995, "horses"),
    "sire_line": _rule("different", 0, None, "horses", "分類体系が違う(比較だけ)"),
    "damsire_line": _rule("different", 0, None, "horses", "分類体系が違う(比較だけ)"),
    "race_date": _rule("same", 0, 0.999, "races"),
    "race_class": _rule("normalized", 0, 0.995, "races"),
    "grade": _rule("normalized", 0, 0.995, "races"),
    "track_type": _rule("normalized", 0, 0.995, "races"),
    "distance": _rule("same", 0, 0.995, "races"),
    "going": _rule("normalized", 0, 0.995, "races"),
    "weather": _rule("normalized", 0, 0.995, "races"),
    "race_name_short": _rule("normalized", 0, 0.99, "races"),
    "prize_money": _rule("same", 0, 0.999, "races"),
    "post_time": _rule("jrdb_only", 0, 0.999, "races", "DB は 2026 年以前 NULL (分母外)"),
    "win_payout": _rule("same", 0, 0.999, "official_win_payouts"),
}

#: the four JRDB-defined columns (five physical): reported as definition_mismatch, 0 not required
DEFINITION_MISMATCH_COLUMNS: frozenset[str] = frozenset(
    {"first_3f", "corner_orders", "running_style", "sire_line", "damsire_line"}
)
#: JRA-VAN-derived DB columns whose non-null cells must have a JRDB counterpart (FR-004 / SC-009).
#: The JRA-VAN CSV's 48 columns collapse to these DB columns (race key → race_id, 4 corners →
#: one array, 生年月日 → birth_year, 18 桁 ID unused); jockey_id/trainer_id are counted as
#: id_unresolved through the person mapping.
REFERENCE_CELL_COLUMNS: tuple[str, ...] = tuple(
    c for c in COLUMN_RULES if c not in ("post_time", "win_payout")
)


def distance_band(distance: int | None) -> str:
    if distance is None:
        return "?"
    if distance <= 1400:
        return "~1400"
    if distance <= 1800:
        return "~1800"
    if distance <= 2200:
        return "~2200"
    return "2201~"


def segment_for(name: str, distance: int | None, track: str | None) -> str:
    """``<距離帯>/<芝ダ障>``; ``first_3f`` additionally isolates 1200 m, where the identity
    タイム − 後3F holds and JRDB's value is exact (contracts/parity.md)."""

    t = track or "?"
    if name == "first_3f" and distance == 1200:
        return f"1200/{t}"
    return f"{distance_band(distance)}/{t}"


# --------------------------------------------------------------------------- reference (DB side)


@dataclass
class Reference:
    """The DB rows of one year, keyed for joining. Built by :func:`load_reference` or by tests."""

    races: dict[str, dict[str, Any]] = field(default_factory=dict)  # race_id → columns
    entries: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    results: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    horses: dict[str, dict[str, Any]] = field(default_factory=dict)  # horse_id → columns
    win_payouts: dict[tuple[str, int], int] = field(default_factory=dict)


_REF_SQL = {
    "races": """
        SELECT race_id, race_date, race_class, grade, track_type, distance, going, weather,
               race_name_short, prize_money, post_time, venue_code
        FROM races WHERE extract(year FROM race_date) = :year""",
    "entries": """
        SELECT rh.race_id, rh.horse_id, rh.horse_number, rh.frame, rh.sex, rh.odds, rh.popularity,
               rh.weight, rh.weight_diff, rh.jockey_weight, rh.running_style, rh.jockey_id,
               rh.trainer_id, rh.entry_status
        FROM race_horses rh JOIN races r USING (race_id)
        WHERE extract(year FROM r.race_date) = :year""",
    "results": """
        SELECT rr.race_id, rr.horse_id, rr.finish_order, rr.finish_time, rr.finish_time_diff,
               rr.last_3f, rr.first_3f, rr.corner_orders, rr.result_status
        FROM race_results rr JOIN races r USING (race_id)
        WHERE extract(year FROM r.race_date) = :year""",
    "horses": """
        SELECT DISTINCT h.horse_id, h.horse_name, h.sire_name, h.dam_name, h.damsire_name,
               h.birth_year, h.owner_name, h.breeder_name, h.sire_line, h.damsire_line
        FROM horses h JOIN race_horses rh USING (horse_id) JOIN races r USING (race_id)
        WHERE extract(year FROM r.race_date) = :year""",
    "win_payouts": """
        SELECT o.race_id, o.horse_number, o.payout_yen
        FROM official_win_payouts o JOIN races r USING (race_id)
        WHERE extract(year FROM r.race_date) = :year""",
}


def load_reference(session, year: int) -> Reference:
    """Read-only load (the caller opens the transaction with ``SET TRANSACTION READ ONLY``)."""

    from sqlalchemy import text

    ref = Reference()
    for name, sql in _REF_SQL.items():
        for r in session.execute(text(sql), {"year": year}).mappings().all():
            d = dict(r)
            if name == "races":
                ref.races[d["race_id"]] = d
            elif name == "entries":
                ref.entries[(d["race_id"], d["horse_id"])] = d
            elif name == "results":
                ref.results[(d["race_id"], d["horse_id"])] = d
            elif name == "horses":
                ref.horses[d["horse_id"]] = d
            else:
                ref.win_payouts[(d["race_id"], int(d["horse_number"]))] = int(d["payout_yen"])
    return ref


# --------------------------------------------------------------------------- JRDB side


@dataclass
class JrdbYear:
    programs: list[JrdbRaceProgram] = field(default_factory=list)
    entries: list[JrdbEntry] = field(default_factory=list)
    horses: list[JrdbHorse] = field(default_factory=list)
    results: list[JrdbResult] = field(default_factory=list)
    payouts: list[JrdbPayouts] = field(default_factory=list)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)


def _rid(key, file_year: int) -> str | None:
    try:
        return race_id_from_key(key, file_year=file_year)
    except Exception:  # noqa: BLE001 — a malformed key is "unjoined", not a crash of the report
        return None


def _hid(blood: str, race_year: int, birth: datetime.date | None = None) -> str | None:
    try:
        return horse_id_from_blood_no(blood, race_year=race_year, birth_date=birth)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- comparison core


@dataclass
class ColumnStats:
    name: str
    denominator: dict[str, int] = field(
        default_factory=lambda: {"both_present": 0, "jrdb_only": 0, "db_only": 0, "unjoined": 0}
    )
    by_segment: dict[str, dict[str, Any]] = field(default_factory=dict)
    examples: list[dict[str, Any]] = field(default_factory=list)

    def add(self, segment: str, *, agree: bool, tolerance, example: dict | None):
        seg = self.by_segment.setdefault(
            segment, {"n": 0, "agree": 0, "rate": None, "tolerance": tolerance}
        )
        seg["n"] += 1
        if agree:
            seg["agree"] += 1
        elif example is not None and len(self.examples) < 10:
            self.examples.append(example)

    def finish(self) -> dict[str, Any]:
        rule = COLUMN_RULES[self.name]
        for seg in self.by_segment.values():
            seg["rate"] = (seg["agree"] / seg["n"]) if seg["n"] else None
        total_n = sum(s["n"] for s in self.by_segment.values())
        total_agree = sum(s["agree"] for s in self.by_segment.values())
        return {
            "meaning": rule["meaning"],
            "note": rule.get("note", ""),
            "threshold": rule["threshold"],
            "denominator": self.denominator,
            "rate": (total_agree / total_n) if total_n else None,
            "by_segment": self.by_segment,
            "examples_disagree": self.examples,
        }


def _agree(a, b, tolerance) -> bool:
    if a is None or b is None:
        return False
    if isinstance(a, int | float | Decimal) and isinstance(b, int | float | Decimal):
        if tolerance:
            return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal(str(tolerance))
        return Decimal(str(a)) == Decimal(str(b))
    return a == b


class _Cells:
    """Collects one column's stats and uncovered-cell counts for every recorded cell."""

    def __init__(self):
        self.stats = {name: ColumnStats(name) for name in COLUMN_RULES}
        self.uncovered: dict[str, dict[str, int]] = {
            name: {
                "row_missing": 0,
                "id_unresolved": 0,
                "value_missing": 0,
                "definition_mismatch": 0,
            }
            for name in REFERENCE_CELL_COLUMNS
        }

    def record(self, name, *, distance, track, jrdb_value, db_value, joined, key):
        rule = COLUMN_RULES[name]
        tol = rule["tolerance"]
        stats = self.stats[name]
        unc = self.uncovered.get(name, defaultdict(int))
        if name in DEFINITION_MISMATCH_COLUMNS and db_value is not None:
            unc["definition_mismatch"] += 1  # uncovered by definition, joined or not
        if not joined:
            stats.denominator["unjoined"] += 1
            if db_value is not None and name not in DEFINITION_MISMATCH_COLUMNS:
                unc["row_missing"] += 1
            return
        if jrdb_value is None and db_value is None:
            return
        if jrdb_value is None:
            stats.denominator["db_only"] += 1
            if name not in DEFINITION_MISMATCH_COLUMNS:
                unc["value_missing"] += 1
            return
        if db_value is None:
            stats.denominator["jrdb_only"] += 1
            return
        stats.denominator["both_present"] += 1
        stats.add(
            segment_for(name, distance, track),
            agree=_agree(jrdb_value, db_value, tol),
            tolerance=tol,
            example={**key, "jrdb": str(jrdb_value), "db": str(db_value)},
        )


def _seg_of(race: Mapping[str, Any]) -> tuple[int | None, str | None]:
    return race.get("distance"), nz.track_norm(race.get("track_type"))


# --------------------------------------------------------------------------- the report


def build_parity(
    year: int,
    jrdb: JrdbYear,
    ref: Reference,
    *,
    person_mapping: Mapping[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """``person_mapping``: (kind, JRDB code) → "mapped" | "conflict" | "unmapped"."""

    pm = person_mapping or {}
    cells = _Cells()

    # --- JRDB indexes -------------------------------------------------------------------------
    jrdb_races: dict[str, JrdbRaceProgram] = {}
    for p in jrdb.programs:
        rid = _rid(p.key, year)
        if rid:
            jrdb_races[rid] = p
    jrdb_horses_by_id: dict[str, JrdbHorse] = {}
    for h in jrdb.horses:
        hid = _hid(h.blood_reg_no, year, h.birth_date)
        if hid:
            jrdb_horses_by_id[hid] = h
    sed_by_race: dict[str, JrdbResult] = {}
    jrdb_results: dict[tuple[str, str], JrdbResult] = {}
    for r in jrdb.results:
        rid = _rid(r.key, year)
        if rid is None:
            continue
        sed_by_race.setdefault(rid, r)
        hid = _hid(r.blood_reg_no, r.race_date.year)
        if hid:
            jrdb_results[(rid, hid)] = r
    jrdb_entries: dict[tuple[str, str], JrdbEntry] = {}
    for e in jrdb.entries:
        rid = _rid(e.key, year)
        hid = _hid(e.blood_reg_no, year) if rid else None
        if rid and hid:
            jrdb_entries[(rid, hid)] = e
    sed_name_by_hid: dict[str, str | None] = {}
    for (_, hid), r in jrdb_results.items():
        sed_name_by_hid.setdefault(hid, r.horse_name)

    # --- races (BAC static, SED going/weather) -----------------------------------------------
    for rid, race in ref.races.items():
        p = jrdb_races.get(rid)
        r = sed_by_race.get(rid)
        distance, track = _seg_of(race)
        key = {"race_id": rid}
        db_post = race.get("post_time")
        pairs = {
            "race_date": (p.race_date if p else None, race.get("race_date")),
            "race_class": (
                nz.class_from_joken(p.joken_code) if p else None,
                nz.class_norm(race.get("race_class")),
            ),
            "grade": (
                nz.grade_from_code(p.grade_code) if p else None,
                nz.grade_norm(race.get("grade")),
            ),
            "track_type": (
                nz.track_from_code(p.track_code) if p else None,
                nz.track_norm(race.get("track_type")),
            ),
            "distance": (p.distance if p else None, race.get("distance")),
            "race_name_short": (
                nz.norm_name(p.race_name_short) if p else None,
                nz.norm_name(race.get("race_name_short")),
            ),
            "prize_money": (p.prizes_man[0] if p else None, race.get("prize_money")),
        }
        for name, (jv, dv) in pairs.items():
            cells.record(
                name,
                distance=distance,
                track=track,
                jrdb_value=jv,
                db_value=dv,
                joined=p is not None,
                key=key,
            )
        # post_time: DB NULL is outside the denominator (contract) — compare HH:MM in JST
        if db_post is not None:
            jst = datetime.timezone(datetime.timedelta(hours=9))
            dv_t = db_post.astimezone(jst).time().replace(second=0, microsecond=0)
            cells.record(
                "post_time",
                distance=distance,
                track=track,
                jrdb_value=p.post_time if p else None,
                db_value=dv_t,
                joined=p is not None,
                key=key,
            )
        elif p is not None and p.post_time is not None:
            cells.stats["post_time"].denominator["jrdb_only"] += 1
        for name, jv, dv in (
            (
                "going",
                nz.going_from_code(r.going_code) if r else None,
                nz.going_norm(race.get("going")),
            ),
            (
                "weather",
                nz.weather_from_code(r.weather_code) if r else None,
                nz.weather_norm(race.get("weather")),
            ),
        ):
            cells.record(
                name,
                distance=distance,
                track=track,
                jrdb_value=jv,
                db_value=dv,
                joined=r is not None,
                key=key,
            )

    # --- entries (KYI + SED) and results (SED) ----------------------------------------------
    joined_horses = 0
    unjoined: list[dict[str, Any]] = []
    for (rid, hid), rh in ref.entries.items():
        race = ref.races.get(rid, {})
        distance, track = _seg_of(race)
        key = {"race_id": rid, "horse_id": hid}
        e = jrdb_entries.get((rid, hid))
        r = jrdb_results.get((rid, hid))
        src = r or e  # SED carries the entry columns too (the 2008 sample has no KYI)
        joined = src is not None
        if joined:
            joined_horses += 1
        elif len(unjoined) < 50:
            unjoined.append(key)
        rr = ref.results.get((rid, hid), {})
        if r is not None and r.weight_diff is None and r.weight is not None:
            jrdb_wdiff: int | None = 0  # JRDB blank = first start = DB 0 (contract)
        else:
            jrdb_wdiff = r.weight_diff if r else None
        db_corners = rr.get("corner_orders")
        pairs = {
            "horse_number": (src.horse_number if src else None, rh.get("horse_number")),
            "frame": (e.frame if e else None, rh.get("frame")),
            "sex": (nz.sex_from_code(e.sex_code) if e else None, nz.sex_norm(rh.get("sex"))),
            "odds": (r.final_win_odds if r else None, rh.get("odds")),
            "popularity": ((r.final_win_popularity or None) if r else None, rh.get("popularity")),
            "weight": (r.weight if r else (e.weight if e else None), rh.get("weight")),
            "weight_diff": (jrdb_wdiff, rh.get("weight_diff")),
            "jockey_weight": (
                r.jockey_weight if r else (e.carried_weight if e else None),
                rh.get("jockey_weight"),
            ),
            "running_style": (r.running_style_code if r else None, rh.get("running_style")),
            "finish_order": ((r.finish_order or None) if r else None, rr.get("finish_order")),
            "finish_time": (
                nz.seconds(r.finish_time) if r else None,
                nz.seconds(rr.get("finish_time")),
            ),
            "finish_time_diff": (
                r.finish_time_diff if r else None,
                nz.seconds(rr.get("finish_time_diff")),
            ),
            "last_3f": (r.last_3f if r else None, rr.get("last_3f")),
            "first_3f": (r.first_3f if r else None, rr.get("first_3f")),
            "corner_orders": (
                [c for c in (r.corner_orders or ()) if c is not None] if r else None,
                ([int(c) for c in db_corners] or None) if db_corners is not None else None,
            ),
        }
        for name, (jv, dv) in pairs.items():
            cells.record(
                name,
                distance=distance,
                track=track,
                jrdb_value=jv,
                db_value=dv,
                joined=joined,
                key=key,
            )
        # persons: a code is "resolved" only when the mapping says mapped
        for name, code, kind in (
            ("jockey_id", src.jockey_code if src else None, "jockey"),
            ("trainer_id", src.trainer_code if src else None, "trainer"),
        ):
            if rh.get(name) is None:
                continue
            if not joined:
                cells.uncovered[name]["row_missing"] += 1
            elif pm.get((kind, code)) != "mapped":
                cells.uncovered[name]["id_unresolved"] += 1

    # --- horses (UKC; the name also from SED) ------------------------------------------------
    for hid, h in ref.horses.items():
        j = jrdb_horses_by_id.get(hid)
        key = {"horse_id": hid}
        name_src = j.horse_name if j else sed_name_by_hid.get(hid)
        cells.record(
            "horse_name",
            distance=None,
            track="all",
            jrdb_value=nz.norm_name(name_src),
            db_value=nz.norm_name(h.get("horse_name")),
            joined=(j is not None) or hid in sed_name_by_hid,
            key=key,
        )
        pairs = {
            "sire_name": (
                nz.norm_name(j.sire_name) if j else None,
                nz.norm_name(h.get("sire_name")),
            ),
            "dam_name": (nz.norm_name(j.dam_name) if j else None, nz.norm_name(h.get("dam_name"))),
            "damsire_name": (
                nz.norm_name(j.damsire_name) if j else None,
                nz.norm_name(h.get("damsire_name")),
            ),
            "birth_year": (j.birth_date.year if j and j.birth_date else None, h.get("birth_year")),
            "owner_name": (
                nz.norm_name(j.owner_name, strip_shi=True) if j else None,
                nz.norm_name(h.get("owner_name"), strip_shi=True),
            ),
            "breeder_name": (
                nz.norm_name(j.breeder_name) if j else None,
                nz.norm_name(h.get("breeder_name")),
            ),
            "sire_line": (j.sire_line_code if j else None, h.get("sire_line")),
            "damsire_line": (j.damsire_line_code if j else None, h.get("damsire_line")),
        }
        for name, (jv, dv) in pairs.items():
            cells.record(
                name,
                distance=None,
                track="all",
                jrdb_value=jv,
                db_value=dv,
                joined=j is not None,
                key=key,
            )

    # --- payouts (HJC) ----------------------------------------------------------------------
    jrdb_pay: dict[tuple[str, int], int] = {}
    for pay in jrdb.payouts:
        rid = _rid(pay.key, year)
        if rid is None:
            continue
        for num, yen in pay.win:
            jrdb_pay[(rid, num)] = yen
    for (rid, num), yen in ref.win_payouts.items():
        jv = jrdb_pay.get((rid, num))
        distance, track = _seg_of(ref.races.get(rid, {}))
        cells.record(
            "win_payout",
            distance=distance,
            track=track,
            jrdb_value=jv,
            db_value=yen,
            joined=jv is not None,
            key={"race_id": rid, "horse_number": num},
        )

    # --- counts / ids -----------------------------------------------------------------------
    jrdb_race_ids = (
        set(jrdb_races) | {rid for rid, _ in jrdb_results} | {rid for rid, _ in jrdb_entries}
    )
    jrdb_entry_keys = set(jrdb_entries) | set(jrdb_results)

    def _counts(a: set, b: set, *, cap: int = 50) -> dict[str, Any]:
        return {
            "jrdb": len(a),
            "db": len(b),
            "both": len(a & b),
            "jrdb_only": sorted(_fmt(k) for k in list(a - b)[:cap]),
            "db_only": sorted(_fmt(k) for k in list(b - a)[:cap]),
        }

    codes_j = {e.jockey_code for e in jrdb.entries if e.jockey_code}
    codes_j |= {r.jockey_code for r in jrdb.results if r.jockey_code}
    codes_t = {e.trainer_code for e in jrdb.entries if e.trainer_code}
    codes_t |= {r.trainer_code for r in jrdb.results if r.trainer_code}

    def _pm_counts(kind: str, codes: set) -> dict[str, int]:
        out = {"mapped": 0, "unmapped": 0, "conflict": 0}
        for c in codes:
            st = pm.get((kind, c), "unmapped")
            out[st if st in out else "unmapped"] += 1
        return out

    unc = cells.uncovered
    return {
        "year": year,
        "files": jrdb.files,
        "counts": {
            "races": _counts(jrdb_race_ids, set(ref.races)),
            "entries": _counts(jrdb_entry_keys, set(ref.entries)),
            "results": _counts(set(jrdb_results), set(ref.results)),
            "payout_rows": _counts(set(jrdb_pay), set(ref.win_payouts)),
        },
        "ids": {
            "horse": {"joined": joined_horses, "unjoined": unjoined},
            "jockey_codes": _pm_counts("jockey", codes_j),
            "trainer_codes": _pm_counts("trainer", codes_t),
        },
        "columns": {name: s.finish() for name, s in cells.stats.items()},
        "uncovered_reference_cells": {
            "columns": REFERENCE_CELL_COLUMNS,
            "by_column": unc,
            "total_excluding_definition_mismatch": sum(
                v
                for name, d in unc.items()
                if name not in DEFINITION_MISMATCH_COLUMNS
                for k, v in d.items()
                if k != "definition_mismatch"
            ),
            "definition_mismatch_total": sum(d["definition_mismatch"] for d in unc.values()),
        },
    }


def _fmt(k) -> str:
    if isinstance(k, tuple):
        return "/".join(str(x) for x in k)
    return str(k)


def passes(report: dict[str, Any]) -> dict[str, bool | None]:
    """Per column: every segment's rate ≥ threshold; None for comparison-only columns and for
    columns with no compared cell (undecided, not failed)."""

    out: dict[str, bool | None] = {}
    for name, col in report["columns"].items():
        thr = col["threshold"]
        segs = [s for s in col["by_segment"].values() if s["n"]]
        if thr is None or not segs:
            out[name] = None
            continue
        out[name] = all((s["rate"] or 0) >= thr for s in segs)
    return out


def jrdb_year_from_decoded(decoded: Iterable) -> JrdbYear:
    """Group DecodedFile objects into a :class:`JrdbYear` (files summary included)."""

    y = JrdbYear()
    for df in decoded:
        kind = df.kind
        entry = y.files.setdefault(kind, {"count": 0, "rows": 0, "rejected_rows": 0})
        entry["count"] += 1
        entry["rows"] += df.n_rows
        entry["rejected_rows"] += len(df.rejected_rows)
        if kind == "BAC":
            y.programs.extend(df.rows)
        elif kind == "KYI":
            y.entries.extend(df.rows)
        elif kind == "UKC":
            y.horses.extend(df.rows)
        elif kind == "SED":
            y.results.extend(df.rows)
        elif kind == "HJC":
            y.payouts.extend(df.rows)
    return y


__all__ = [
    "COLUMN_RULES",
    "DEFINITION_MISMATCH_COLUMNS",
    "REFERENCE_CELL_COLUMNS",
    "ColumnStats",
    "JrdbYear",
    "Reference",
    "build_parity",
    "distance_band",
    "jrdb_year_from_decoded",
    "load_reference",
    "passes",
    "segment_for",
]
