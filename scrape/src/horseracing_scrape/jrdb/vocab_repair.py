"""One-off repair of the current DB's running style / bloodline lines from stored JRDB files
(2026-11-10, ``jrdb-vocab-repair``).

From ``vocab.STYLE_OVERWRITE_FROM`` (2025-10-11) the current DB's ``race_horses.running_style``
was written only by netkeiba's first-corner heuristic; the displayed model was trained on the
JRA-VAN-vocabulary label translated from JRDB (vocab.py). This repair puts the same label the
daily writer now writes onto the past rows, and fills NULL ``horses.sire_line/damsire_line``
with the current families. Nothing else is touched.

Two steps (codex 2026-11-10 #8):

* :func:`build_plan` — read only: the stored SED / UKC files of the days → a plan of
  ``(race, horse, old → new)`` changes, with the input file sha256s and the translator pins;
* :func:`apply_plan` — writes exactly the plan's rows whose CURRENT value still equals the
  planned old value (anything else is skipped and counted); a re-run plan is empty.
"""

from __future__ import annotations

import datetime
import hashlib
from collections import defaultdict
from pathlib import Path

from horseracing_db.enums import EntryStatus
from horseracing_db.models import Horse, RaceHorse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import vocab
from .decode import decode_file
from .files import build_meta
from .ids import horse_id_from_blood_no, race_id_from_key
from .profile import check_payload
from .upsert_jrdb import _current_lines, _result_status

PLAN_SCHEMA = "jrdb-vocab-repair-v1"


def _days(artifacts: Path, kind: str, date_from: datetime.date, date_to: datetime.date):
    folder = artifacts / kind
    if not folder.is_dir():
        return []
    out = []
    for d in sorted(folder.iterdir()):
        try:
            day = datetime.datetime.strptime(d.name, "%Y%m%d").date()
        except ValueError:
            continue
        if date_from <= day <= date_to and d.is_dir():
            paths = sorted(d.glob("*.txt"))
            if len(paths) > 1:  # a corrected version beside the first: never guess which applies
                raise ValueError(f"{kind} {day}: {len(paths)} versions — resolve them first")
            out.append((day, paths))
    return out


def _decode(path: Path, day: datetime.date):
    raw = path.read_bytes()
    plain = path.name.split(".")[0] + ".txt"
    meta = build_meta(
        plain, raw, variant="daily", fetched_at=datetime.datetime.now(datetime.UTC), data_date=day
    )
    return decode_file(raw, meta, profile="current"), hashlib.sha256(raw).hexdigest()


def build_plan(
    session: Session, artifacts: Path, date_from: datetime.date, date_to: datetime.date
) -> dict:
    tr = vocab.load_translator()  # fail-closed here: a repair never runs without the tables
    if date_from < vocab.STYLE_OVERWRITE_FROM:
        raise ValueError(f"--from must be >= {vocab.STYLE_OVERWRITE_FROM} (JRA-VAN values stay)")
    style_changes: list[dict] = []
    counts: dict[str, int] = defaultdict(int)
    inputs: list[dict] = []
    for day, paths in _days(artifacts, "SED", date_from, date_to):
        for path in paths:
            decoded, sha = _decode(path, day)
            inputs.append({"kind": "SED", "file": path.name, "day": day.isoformat(), "sha256": sha})
            by_race: dict[str, list] = defaultdict(list)
            for r in decoded.rows:
                by_race[race_id_from_key(r.key, file_year=r.race_date.year)].append(r)
            for rid, rows in sorted(by_race.items()):
                horses = {
                    rh.horse_number: rh
                    for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == rid))
                }
                if not horses:
                    counts["race_missing"] += 1
                    continue
                started = sum(1 for h in horses.values() if h.entry_status == EntryStatus.STARTED)
                for r in rows:
                    if _result_status(r.abnormal_code) is None or r.style_inputs is None:
                        continue
                    rh = horses.get(r.horse_number)
                    if rh is None:
                        counts["horse_missing"] += 1
                        continue
                    code, corners = r.style_inputs
                    label = tr.style_label(code, corners, r.finish_order or None, started)
                    if label is None:
                        counts["no_code"] += 1
                    elif rh.running_style == label:
                        counts["unchanged"] += 1
                    else:
                        style_changes.append(
                            {
                                "race_id": rid,
                                "horse_id": rh.horse_id,
                                "horse_number": r.horse_number,
                                "old": rh.running_style,
                                "new": label,
                            }
                        )
    line_changes: list[dict] = []
    seen: set[str] = set()
    for day, paths in _days(artifacts, "UKC", date_from, date_to):
        for path in paths:
            decoded, sha = _decode(path, day)
            inputs.append({"kind": "UKC", "file": path.name, "day": day.isoformat(), "sha256": sha})
            for h in decoded.rows:
                hid = horse_id_from_blood_no(h.blood_reg_no, race_year=day.year)
                if hid in seen:
                    continue
                seen.add(hid)
                row = session.get(Horse, hid)
                if row is None or (row.sire_line is not None and row.damsire_line is not None):
                    continue
                for col, value in _current_lines(session, h, None).items():
                    if getattr(row, col) is None:
                        line_changes.append({"horse_id": hid, "column": col, "old": None,
                                             "new": value})
    session.rollback()
    return {
        "schema": PLAN_SCHEMA,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "translator": {"tree_sha256": vocab.TREE_SHA256, "line_sha256": vocab.LINE_SHA256},
        "inputs": inputs,
        "counts": dict(counts) | {"style_changes": len(style_changes),
                                  "line_changes": len(line_changes)},
        "style_changes": style_changes,
        "line_changes": line_changes,
    }


def apply_plan(session: Session, plan: dict) -> dict:
    if plan.get("schema") != PLAN_SCHEMA:
        raise ValueError("not a jrdb-vocab-repair plan")
    if plan["translator"] != {"tree_sha256": vocab.TREE_SHA256, "line_sha256": vocab.LINE_SHA256}:
        raise ValueError("the plan was built with other translation tables")
    out = {"style_written": 0, "line_written": 0, "skipped_changed_since_plan": 0}
    for ch in plan["style_changes"]:
        check_payload("current", "race_horses", {"running_style": ch["new"]})
        res = session.execute(
            update(RaceHorse)
            .where(
                RaceHorse.race_id == ch["race_id"],
                RaceHorse.horse_id == ch["horse_id"],
                RaceHorse.running_style.is_not_distinct_from(ch["old"]),
            )
            .values(running_style=ch["new"])
        )
        if res.rowcount == 1:
            out["style_written"] += 1
        else:
            out["skipped_changed_since_plan"] += 1
    for ch in plan["line_changes"]:
        check_payload("current", "horses", {ch["column"]: ch["new"]})
        col = getattr(Horse, ch["column"])
        res = session.execute(
            update(Horse)
            .where(Horse.horse_id == ch["horse_id"], col.is_(None))
            .values({ch["column"]: ch["new"]})
        )
        if res.rowcount == 1:
            out["line_written"] += 1
        else:
            out["skipped_changed_since_plan"] += 1
    return out


__all__ = ["PLAN_SCHEMA", "apply_plan", "build_plan"]
