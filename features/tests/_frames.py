"""Build in-memory Frames from concise specs for DB-free unit tests."""

from __future__ import annotations

import datetime

import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus

from horseracing_features.loader import Frames

# race-level defaults
_RACE_DEFAULTS = dict(
    venue_code="05", distance=1600, track_type="芝", going="良", weather="晴",
    race_class="未勝利", race_number=1,
    prize_money=None,  # Feature 056: opt-in per spec
)
# horse-level (race_horses) defaults
_HORSE_DEFAULTS = dict(
    age=3, sex="牡", frame=1, horse_number=1, jockey_id="J1", trainer_id="T1",
    weight=460, weight_diff=0, jockey_weight=56.0, running_style=None,  # 030: 斤量
    popularity=None,  # Feature 058: past market rank (as-of only); per-horse override in specs
    odds=None,  # Feature 069 (F02): past market odds (as-of only); per-horse override in specs
)


def make_frames(specs: list[dict]) -> Frames:
    """specs: list of race dicts: {race_id, race_date(ISO), horses: [horse dicts]}.

    horse dict keys: horse_id (req), entry_status='started', result_status='finished',
    finish_order=1, last_3f=35.0, + optional race_horse attrs.
    """
    race_rows, rh_rows, rr_rows = [], [], []
    pedigree: dict[str, dict] = {}  # Feature 026: horse_id -> pedigree (first spec wins, stable)
    for spec in specs:
        rid = spec["race_id"]
        rdate = datetime.date.fromisoformat(spec["race_date"])
        race_rows.append({"race_id": rid, "race_date": rdate,
                          **{k: spec.get(k, v) for k, v in _RACE_DEFAULTS.items()}})
        for h in spec["horses"]:
            pedigree.setdefault(h["horse_id"], {
                "horse_id": h["horse_id"],
                "sire_name": h.get("sire_name"), "dam_name": h.get("dam_name"),
                "damsire_name": h.get("damsire_name"),
                "sire_id": h.get("sire_id"), "dam_id": h.get("dam_id"),
                "damsire_id": h.get("damsire_id"),
                # Feature 056: owner/breeder keys + bloodline lines
                "owner_name": h.get("owner_name"), "breeder_name": h.get("breeder_name"),
                "sire_line": h.get("sire_line"), "damsire_line": h.get("damsire_line"),
            })
            entry = h.get("entry_status", EntryStatus.STARTED)
            rh_rows.append({
                "race_id": rid, "horse_id": h["horse_id"], "entry_status": entry,
                **{k: h.get(k, v) for k, v in _HORSE_DEFAULTS.items()},
            })
            result_status = h.get("result_status", ResultStatus.FINISHED if entry == EntryStatus.STARTED else None)
            if result_status is not None:  # DNS (cancel/exclude) -> no race_results row
                finished = result_status == ResultStatus.FINISHED
                ft = h.get("finish_time", 95.0)  # seconds (Feature 023); stored as timedelta like DB
                fd = h.get("finish_time_diff", 0.0)
                rr_rows.append({
                    "race_id": rid, "horse_id": h["horse_id"],
                    "finish_order": h.get("finish_order", 1) if finished else None,
                    "last_3f": h.get("last_3f", 35.0) if finished else None,
                    "first_3f": h.get("first_3f") if finished else None,  # Feature 056 (opt-in)
                    "finish_time": datetime.timedelta(seconds=ft) if finished else None,
                    "finish_time_diff": datetime.timedelta(seconds=fd) if finished else None,
                    "corner_orders": h.get("corner_orders") if finished else None,
                    "result_status": result_status,
                })
    return Frames(
        races=pd.DataFrame(race_rows),
        race_horses=pd.DataFrame(rh_rows),
        race_results=pd.DataFrame(rr_rows),
        horses=pd.DataFrame(list(pedigree.values())),
    )
