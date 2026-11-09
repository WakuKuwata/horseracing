"""緊急モード (Feature 178, T050; FR-008): JRDB の当日ファイルが来ないとき、JRA 公式の出馬表から
出走行とマスタ行を正規 ID で作る.

The 176 official route never creates rows (FR-011a §1) because netkeiba came first. When netkeiba is
stopped and JRDB is late, the operator flips the policy flag ``jra_emergency_entries`` and the
``jra-entries`` command calls :func:`create_entries_from_official` BEFORE the normal official
writer (tasks T050a): a race whose stored field is EMPTY is created here from the official page
(horse ids = ``pw01dud00`` 10 桁, jockey/trainer = official 5 桁, 来歴
``entry_static=jra_official``),
a race that already has rows is left to the normal writer. Static text columns are written in the
DB vocabulary (``ダ``/``障``, ``2勝クラス`` → ``2勝``), since a new row with NULL track/class would
break the features more than a spelling would. A KYI arriving later keeps these values (先着保持 —
the official source owns them, precedence-jrdb §3).

With the flag off (the default) nothing is created and the 176 behaviour is byte-identical.
"""

from __future__ import annotations

import datetime
import json
import re
import uuid
from collections.abc import Sequence
from pathlib import Path

from horseracing_db.enums import Source
from sqlalchemy.orm import Session

from .. import precedence as P
from ..jrdb import normalize as nz
from ..models import ResolvedEntryHorse
from ..upsert import Counts, lock_race, write_entries
from .models import JraChange, JraDayPage, JraEntry
from .upsert_jra import CancelUnresolved, era_ok, race_values_of, resolve_cancelled_numbers

POLICY_KEY = "jra_emergency_entries"
JRA = Source.JRA_OFFICIAL
_CLASS_SUFFIX = re.compile(r"クラス$")


def emergency_enabled(policy_path: Path | str | None) -> bool:
    """The policy flag, read at call time (CLI start); missing file / key → False."""

    if policy_path is None:
        return False
    try:
        doc = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return doc.get(POLICY_KEY) is True


def _db_vocabulary(values: dict) -> dict:
    out = dict(values)
    out["track_type"] = nz.track_norm(values.get("track_type"))
    rc = values.get("race_class")
    out["race_class"] = _CLASS_SUFFIX.sub("", rc) if rc else None
    return out


def create_entries_from_official(
    session: Session,
    day_page: JraDayPage[JraEntry],
    changes: Sequence[JraChange] = (),
    *,
    policy: bool,
    observed_at: datetime.datetime,
    job_id: uuid.UUID | None = None,
    race_ids: Sequence[str] | None = None,
) -> Counts:
    """Create the field of every published race whose stored field is empty. ``policy`` is the
    flag's value; False → ``Counts()`` with nothing done (byte-identical 176 path)."""

    c = Counts()
    if not policy:
        return c
    for block in sorted(
        (b for b in day_page.races if b.state == "published"), key=lambda b: b.race_id or ""
    ):
        entry: JraEntry = block.payload
        rid = entry.race.race_id
        if race_ids is not None and rid not in race_ids:
            continue
        if not era_ok(entry.race.race_date):
            c.bump("skipped_pre_netkeiba_era")
            continue
        if any(h.horse_id is None or "horse" in h.unknown_id_kinds for h in entry.horses):
            c.bump("emergency_skipped_unknown_id")
            c.error_messages.append(f"emergency {rid}: a horse has no official id; not created")
            continue
        with session.begin_nested():
            snap = lock_race(session, rid)
            if snap.horses:
                c.bump("emergency_skipped_existing_field")
                continue
            try:  # 取消 horses lose their 馬番 on the page; the changes page gives it back
                numbers = resolve_cancelled_numbers(entry, changes, {})
            except CancelUnresolved:
                c.bump("emergency_skipped_blank_number")
                c.error_messages.append(f"emergency {rid}: a 取消 horse has no 馬番; not created")
                continue
            year = entry.race.race_date.year
            rows = [
                ResolvedEntryHorse(
                    horse_id=h.horse_id,  # type: ignore[arg-type]
                    horse_name=h.horse_name,
                    frame=h.frame,
                    horse_number=(
                        h.horse_number if h.horse_number is not None else numbers.get(h.horse_id)
                    ),
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
                for h in entry.horses
            ]
            out = write_entries(
                session,
                rid,
                _db_vocabulary(race_values_of(entry.race)),
                rows,
                source=JRA,
                observed_at=observed_at,
                job_id=job_id,
                page_kind=P.PAGE_ENTRIES,
            )
            c.merge(out)
            if not out.extra.get("quarantined_collision"):
                c.bump("emergency_rows_created", len(rows))
                c.bump("emergency_races_created")
    return c


__all__ = ["POLICY_KEY", "create_entries_from_official", "emergency_enabled"]
