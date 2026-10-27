"""Pre-registered manual plan and fail-closed collection preflight. No external I/O."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from scripts.roi_explore.model_connection_163 import contract as c
from scripts.roi_explore.prospective_rehearsal_162.store import publish, read

ROOT = Path(__file__).resolve().parents[3]
MODE = "manual_observation"
ORIGINS = ("manual_live", "fixture_rehearsal")


def source_pins():
    paths = list(Path(__file__).parent.glob("*.py"))
    paths += list((ROOT / "scripts/roi_explore/model_connection_163").glob("*.py"))
    paths += [
        ROOT / "scripts/roi_explore/prospective_rehearsal_162" / name
        for name in ("contract.py", "store.py", "settlement.py", "rehearsal.py")
    ]
    for package in (
        "features",
        "training",
        "serving",
        "eval",
        "db",
        "probability",
        "scrape",
        "live",
    ):
        paths += list((ROOT / package / "src").rglob("*.py"))
    paths += [
        ROOT / "live/src/horseracing_live/chaos_politeness.py",
        ROOT / "scrape/src/horseracing_scrape/fetch.py",
    ]
    return {
        str(p.relative_to(ROOT)): c.sha_bytes(p.read_bytes())
        for p in sorted(set(paths))
    }


def create_plan(directory, *, race_id, post_time, clock, origin="manual_live"):
    c.require(origin in ORIGINS, "invalid_plan_origin")
    c.require(
        isinstance(race_id, str) and len(race_id) == 12 and race_id.isdigit(),
        "invalid_race_id",
    )
    now, post = clock.now(), c.time(post_time)
    c.require(
        post.astimezone(dt.timezone(dt.timedelta(hours=9))).year == 2026,
        "unsupported_model_year",
    )
    if origin == "manual_live":
        c.require(
            c.time(now) <= post - dt.timedelta(seconds=900),
            "register_before_preparation",
        )
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=False)
    plan = dict(
        schema_version=1,
        mode=MODE,
        origin=origin,
        race_id=race_id,
        post_time=post_time,
        race_date=post.astimezone(dt.timezone(dt.timedelta(hours=9)))
        .date()
        .isoformat(),
        registered_at=now,
        collection_default=False,
        manual_only=True,
        can_adopt_model=False,
        model_protocol=c.make_protocol(),
        source_pins=source_pins(),
        preparation_seconds=900,
        decision_open_seconds=600,
        decision_close_seconds=540,
        quote_max_age_seconds=1800,
        guard_max_age_seconds=5,
    )
    checksum = publish(path / "plan.json", plan)
    publish(
        path / "registration-ready.json",
        dict(
            mode=MODE, origin=origin, plan_sha256=checksum, available_by_at=clock.now()
        ),
    )
    (path / "plan.sha256").write_text(checksum + "\n")
    return path / "plan.json", checksum


def preflight(plan_path, expected_sha, *, enable_collection, origin, clock):
    path = Path(plan_path)
    c.require(c.sha_bytes(path.read_bytes()) == expected_sha, "plan_sha_mismatch")
    plan = read(path)
    c.require(
        plan["schema_version"] == 1
        and plan["mode"] == MODE
        and plan["origin"] == origin
        and origin in ORIGINS
        and plan["manual_only"] is True
        and plan["collection_default"] is False
        and plan["can_adopt_model"] is False,
        "plan_mode_or_origin_mismatch",
    )
    c.require(
        type(enable_collection) is bool
        and (enable_collection if origin == "manual_live" else not enable_collection),
        "collection_not_explicitly_enabled"
        if origin == "manual_live"
        else "fixture_cannot_enable_collection",
    )
    receipt = read(path.with_name("registration-ready.json"))
    c.require(
        receipt["plan_sha256"] == expected_sha
        and receipt["origin"] == origin
        and receipt["mode"] == MODE,
        "plan_registration_mismatch",
    )
    c.require(
        plan["model_protocol"] == c.make_protocol()
        and plan["source_pins"] == source_pins(),
        "pinned_code_or_models_changed",
    )
    c.require(
        (
            plan["preparation_seconds"],
            plan["decision_open_seconds"],
            plan["decision_close_seconds"],
            plan["quote_max_age_seconds"],
            plan["guard_max_age_seconds"],
        )
        == (900, 600, 540, 1800, 5),
        "policy_changed",
    )
    now, post = c.time(clock.now()), c.time(plan["post_time"])
    c.require(
        post.astimezone(dt.timezone(dt.timedelta(hours=9))).year
        == plan["model_protocol"]["year"],
        "unsupported_model_year",
    )
    c.require(
        c.time(plan["registered_at"]) <= c.time(receipt["available_by_at"]) <= now,
        "future_plan_registration",
    )
    if origin == "manual_live":
        c.require(
            c.time(receipt["available_by_at"]) <= post - dt.timedelta(seconds=900),
            "plan_registered_too_late",
        )
        c.require(post - dt.timedelta(seconds=900) <= now, "outside_preparation_window")
    c.require(now < post - dt.timedelta(seconds=540), "observation_deadline_elapsed")
    c.require(
        plan["race_date"]
        == post.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat(),
        "plan_race_date_mismatch",
    )
    return plan
