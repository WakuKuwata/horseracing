"""Manual plan/observe/rehearse/verify/settle. No scheduled or implicit collection."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.roi_explore.model_connection_163 import contract as c  # noqa: E402
from scripts.roi_explore.model_connection_163.clock import RealClock  # noqa: E402
from scripts.roi_explore.model_connection_163.replay import historical_official_replay  # noqa: E402
from scripts.roi_explore.model_connection_163.run import connection_disabled  # noqa: E402
from scripts.roi_explore.prospective_rehearsal_162.store import publish, read  # noqa: E402
from scripts.roi_explore.manual_observation_165.backends import (  # noqa: E402
    FixtureBackend,
    LiveBackend,
)  # noqa: E402
from scripts.roi_explore.manual_observation_165.control import (  # noqa: E402
    MODE,
    create_plan,
    preflight,
)  # noqa: E402
from scripts.roi_explore.manual_observation_165.session import execute, verify, abort  # noqa: E402
from scripts.roi_explore.manual_observation_165.settlement import settle  # noqa: E402


def rehearse(directory, *, scenario="normal"):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    clock = RealClock()
    post = (c.time(clock.now()) + dt.timedelta(seconds=595)).isoformat()
    plan_path, checksum = create_plan(
        root / "registration",
        race_id="202699990001",
        post_time=post,
        clock=clock,
        origin="fixture_rehearsal",
    )
    plan = read(plan_path)
    status = execute(
        plan_path,
        checksum,
        root / "session",
        backend=FixtureBackend(plan, scenario=scenario),
        clock=clock,
    )
    publish(root / "historical-official-replay.json", historical_official_replay())
    result = verify(root / "session")
    publish(
        root / "report.json",
        dict(
            mode=MODE,
            origin="fixture_rehearsal",
            new_collection=False,
            database_access=False,
            evidence_admissible=False,
            can_adopt_model=False,
            state=status["state"],
            verification=result,
            historical_official_replay_races=9,
            actual_model_members=dict(legacy=6, mev=15),
        ),
    )
    return read(root / "report.json")


def observe(plan_path, checksum, output, *, enable_collection=False):
    clock = RealClock()
    # Enforce authorization/origin/pins/time BEFORE creating even lazy enabled adapters.
    preflight(
        plan_path,
        checksum,
        enable_collection=enable_collection,
        origin="manual_live",
        clock=clock,
    )
    return execute(
        plan_path,
        checksum,
        output,
        backend=LiveBackend(),
        enable_collection=enable_collection,
        clock=clock,
    )


def add_official(session, evidence_path):
    root = Path(session)
    verify(root)
    decision = read(root / "decision.json")
    decision["sealed_at"] = read(root / "accepted.json")["acknowledged_at"]
    evidence = read(evidence_path)
    revision = evidence.get("revision")
    c.require(
        type(revision) is int and 1 <= revision <= 9999, "invalid_official_revision"
    )
    previous = (
        read(root / "settlements" / f"{revision - 1:04}" / "result.json")
        if revision > 1
        else None
    )
    folder = root / "settlements" / f"{revision:04}"
    c.require(
        folder.exists()
        or revision
        == len(list((root / "settlements").glob("[0-9][0-9][0-9][0-9]"))) + 1,
        "official_revision_gap",
    )
    if previous:
        c.require(
            c.time(evidence["observed_at"]) > c.time(previous["observed_at"]),
            "official_revision_clock_order",
        )
    now = RealClock().now()
    result = settle(decision, evidence, observed_now=now)
    result.update(
        decision_sha256=c.digest(read(root / "decision.json")),
        evidence_sha256=c.digest(evidence),
        previous_sha256=c.digest(previous) if previous else None,
        accounted_at=now,
    )
    publish(folder / "evidence.json", evidence)
    publish(folder / "result.json", result)
    return result


def verify_official(session):
    root = Path(session)
    decision = read(root / "decision.json")
    decision["sealed_at"] = read(root / "accepted.json")["acknowledged_at"]
    previous = None
    for path in (root / "settlements").rglob("*"):
        if path.is_file():
            c.require(
                re.fullmatch(
                    r"[0-9]{4}/(?:evidence|result)\.json",
                    path.relative_to(root / "settlements").as_posix(),
                )
                is not None,
                "unexpected_settlement_file",
            )
    for revision, folder in enumerate(
        sorted((root / "settlements").glob("[0-9][0-9][0-9][0-9]")), 1
    ):
        c.require(folder.name == f"{revision:04}", "official_revision_gap")
        evidence, result = read(folder / "evidence.json"), read(folder / "result.json")
        c.require(evidence["revision"] == revision, "official_revision_mismatch")
        if previous:
            c.require(
                c.time(evidence["observed_at"]) > c.time(previous["observed_at"]),
                "official_revision_clock_order",
            )
        expected = settle(decision, evidence, observed_now=result["accounted_at"])
        expected.update(
            decision_sha256=c.digest(read(root / "decision.json")),
            evidence_sha256=c.digest(evidence),
            previous_sha256=c.digest(previous) if previous else None,
            accounted_at=result["accounted_at"],
        )
        c.require(result == expected, "official_accounting_changed")
        previous = result
    return dict(
        revisions=len(list((root / "settlements").glob("[0-9][0-9][0-9][0-9]"))),
        status=previous["status"] if previous else "pending",
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--race-id", required=True)
    plan.add_argument("--post-time", required=True)
    plan.add_argument("--out", type=Path, required=True)
    obs = sub.add_parser("observe")
    obs.add_argument("--plan", type=Path, required=True)
    obs.add_argument("--plan-sha", required=True)
    obs.add_argument("--out", type=Path, required=True)
    obs.add_argument("--enable-collection", action="store_true", default=False)
    rep = sub.add_parser("rehearse")
    rep.add_argument("--out", type=Path, required=True)
    rep.add_argument(
        "--scenario",
        choices=["normal", "field_changed", "result_appeared", "feature_failure"],
        default="normal",
    )
    val = sub.add_parser("verify")
    val.add_argument("--session", type=Path, required=True)
    stop = sub.add_parser("abort")
    stop.add_argument("--session", type=Path, required=True)
    official = sub.add_parser("settle")
    official.add_argument("--session", type=Path, required=True)
    official.add_argument("--evidence", type=Path, required=True)
    args = p.parse_args()
    if args.command == "observe":
        result = observe(
            args.plan, args.plan_sha, args.out, enable_collection=args.enable_collection
        )
    else:
        with connection_disabled():
            if args.command == "plan":
                path, checksum = create_plan(
                    args.out,
                    race_id=args.race_id,
                    post_time=args.post_time,
                    clock=RealClock(),
                )
                result = dict(plan=str(path), sha256=checksum, collection_enabled=False)
            elif args.command == "rehearse":
                result = rehearse(args.out, scenario=args.scenario)
            elif args.command == "verify":
                result = verify(args.session)
                if result["status"] == "verified":
                    result["settlement"] = verify_official(args.session)
            elif args.command == "abort":
                result = abort(args.session)
            else:
                result = add_official(args.session, args.evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
