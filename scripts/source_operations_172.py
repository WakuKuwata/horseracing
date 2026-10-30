"""Shared source settings with an explicit collection pause for local service launches."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/source-operations.json"


def require(ok, code):
    if not ok:
        raise ValueError(code)


#: Floor for the per-request interval owed to the source. 172 (2026-10-27) launched at 60 s;
#: on 2026-10-31 the user set the operating value to 10 s ("netkeiba は 1 req/10 s・外部でない
#: ものは制限不要"). The floor is the smallest value the policy may ask for; the shared DB-backed
#: limiter still serialises every caller, and internal (non-source) work is never throttled.
MIN_SOURCE_INTERVAL_S = 10.0


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_policy_key")
        result[key] = value
    return result


#: Feature 174 (capture-only days): an explicit, approved list of race days on which the manual
#: near-post capture may run. All three keys travel together. The capture itself only starts while
#: ``collection_paused`` is true (live/near_post.py), so the list stays inert in active mode.
CAPTURE_ONLY_KEYS = frozenset(
    {"capture_only_dates", "capture_only_approved_by", "capture_only_approved_at"}
)


def validate_capture_only(result):
    keys = CAPTURE_ONLY_KEYS & set(result)
    if not keys:
        return
    require(keys == CAPTURE_ONLY_KEYS, "capture_only_keys_incomplete")
    dates = result["capture_only_dates"]
    require(isinstance(dates, list), "capture_only_dates_not_list")
    seen = set()
    for value in dates:
        require(isinstance(value, str), "capture_only_date_not_string")
        try:
            day = dt.date.fromisoformat(value)
        except ValueError:
            raise ValueError("capture_only_date_not_iso") from None
        require(day not in seen, "capture_only_date_duplicate")
        seen.add(day)
    by = result["capture_only_approved_by"]
    require(isinstance(by, str) and by.strip() != "", "capture_only_approved_by_empty")
    at = result["capture_only_approved_at"]
    require(isinstance(at, str), "capture_only_approved_at_not_string")
    try:
        dt.datetime.fromisoformat(at)
    except ValueError:
        raise ValueError("capture_only_approved_at_not_iso") from None


def load_policy(path=CONFIG):
    require(not path.is_symlink() and path.is_file(), "policy_missing_or_symlink")
    result = json.loads(path.read_text(), object_pairs_hook=unique)
    required = {
        "schema_version",
        "source_interval_seconds",
        "ops_fetch_min_interval_seconds",
        "shared_limiter_enabled",
        "collection_paused",
    }
    present = set(result)
    require(required <= present and present <= required | CAPTURE_ONLY_KEYS, "policy_keys_invalid")
    validate_capture_only(result)
    require(result["schema_version"] == "172-v1", "policy_version_invalid")
    for key in ("source_interval_seconds", "ops_fetch_min_interval_seconds"):
        value = result[key]
        require(
            type(value) in (int, float)
            and math.isfinite(value)
            and value >= MIN_SOURCE_INTERVAL_S,
            "source_interval_must_be_finite_at_least_10",
        )
    require(
        result["source_interval_seconds"] == result["ops_fetch_min_interval_seconds"],
        "source_intervals_not_equal",
    )
    require(result["shared_limiter_enabled"] is True, "shared_limiter_must_be_enabled")
    # 2026-10-31 (user decision): ``false`` is the explicit "active" mode — ops accepts writes and
    # the worker runs. Everything else in the policy (60 s interval, shared limiter, auto-capture
    # off) is enforced the same way in both modes; only the pause itself is a switch.
    require(type(result["collection_paused"]) is bool, "collection_paused_must_be_bool")
    return result


def collection_mode(policy):
    return "paused" if policy["collection_paused"] else "active"


def apply_policy(policy):
    for key, value in (
        ("HORSERACING_NETKEIBA_MIN_INTERVAL_S", policy["source_interval_seconds"]),
        ("OPS_FETCH_MIN_INTERVAL", policy["ops_fetch_min_interval_seconds"]),
    ):
        inherited = os.getenv(key)
        if inherited is not None:
            try:
                previous = float(inherited)
            except (ValueError, OverflowError):
                raise ValueError("inherited_interval_invalid") from None
            require(
                math.isfinite(previous) and previous >= 0, "inherited_interval_invalid"
            )
            require(previous <= value, "would_shorten_existing_interval")
    require(
        os.getenv("HORSERACING_SHARED_LIMITER", "1").strip().lower()
        not in ("0", "false", "no"),
        "inherited_shared_limiter_disabled",
    )
    os.environ.update(
        HORSERACING_NETKEIBA_MIN_INTERVAL_S=str(policy["source_interval_seconds"]),
        OPS_FETCH_MIN_INTERVAL=str(policy["ops_fetch_min_interval_seconds"]),
        HORSERACING_SHARED_LIMITER="1",
        HORSERACING_COLLECTION_MODE=collection_mode(policy),
        OPS_CAPTURE_ON_AUTO_PREDICT="0",
    )


def source_paths():
    for package in ("scrape", "ops"):
        path = str(ROOT / package / "src")
        if path not in sys.path:
            sys.path.insert(0, path)


def runtime_settings(policy, component):
    source_paths()
    from horseracing_scrape.politeness import RequestPoliteness, source_interval_s
    from horseracing_ops.config import CONFIG as ops

    def forbidden_session():
        raise AssertionError("runtime_policy_check_must_not_access_database")

    intervals = dict(
        source=source_interval_s("netkeiba.com"),
        ops_config=ops.fetch_min_interval,
        api_caller=RequestPoliteness(forbidden_session, min_interval_s=1)._interval_for(
            "netkeiba.com"
        ),
        ops_caller=RequestPoliteness(
            forbidden_session, min_interval_s=ops.fetch_min_interval
        )._interval_for("netkeiba.com"),
    )
    require(
        all(v == policy["source_interval_seconds"] for v in intervals.values()),
        "runtime_interval_not_reflected",
    )
    return dict(
        checked_at=dt.datetime.now(dt.UTC).isoformat(),
        pid=os.getpid(),
        component=component,
        source_interval_seconds=policy["source_interval_seconds"],
        effective_intervals=intervals,
        collection_paused=policy["collection_paused"],
        collection_mode=collection_mode(policy),
        shared_limiter_enabled=True,
        worker_main_imported="horseracing_ops.worker" in sys.modules,
        worker_main_called=False,
        recovery_called=False,
        race_data_collection=bool(component == "worker" and not policy["collection_paused"]),
        external_http_requests=0,
        model_inference=False,
        secret_values_saved=False,
        quote_max_age_seconds=1800,
        both_predictions_durable_before_decision_required=True,
    )


@contextlib.contextmanager
def no_outbound_python_connections():
    """Block HTTP client sockets while native libpq's confirmed DB connection remains usable."""
    saved = socket.socket.connect, socket.socket.connect_ex, socket.create_connection

    def blocked(*args, **kwargs):
        raise RuntimeError("source_collection_paused")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = (
            saved
        )


class PausedOps:
    """Reject mutating HTTP methods outside FastAPI, before body/dependencies/enqueue."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in (
            "GET",
            "HEAD",
            "OPTIONS",
        ):
            body = json.dumps(
                dict(
                    status=503,
                    code="collection_paused",
                    detail="データ取得を停止中です。",
                ),
                ensure_ascii=False,
            ).encode()
            await send(
                dict(
                    type="http.response.start",
                    status=503,
                    headers=[
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                )
            )
            await send(dict(type="http.response.body", body=body))
            return
        await self.app(scope, receive, send)


def save_report(path, result):
    # Public policy facts only; no DSN, environment dump or credential-bearing exceptions.
    from scripts.roi_explore.prospective_rehearsal_162.store import publish

    publish(path, result)


def spawn_detached(args):
    """Separate the OS session: nohup alone still belongs to a tool's process group."""
    require(args.log is not None and args.pidfile is not None, "detach_paths_required")
    require(not args.report.exists(), "runtime_report_already_exists")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        args.component,
        "--policy",
        str(args.policy),
        "--report",
        str(args.report),
        "--policy-loaded",
    ]
    with args.log.open("w") as log:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
            env=dict(os.environ),
        )
    deadline = time.monotonic() + 15
    while (
        proc.poll() is None and not args.report.exists() and time.monotonic() < deadline
    ):
        time.sleep(0.05)
    if proc.poll() is not None or not args.report.exists():
        if proc.poll() is None:
            proc.terminate()
        raise ValueError("detached_service_start_failed")
    args.pidfile.write_text(str(proc.pid) + "\n")
    return 0


def manual_preflight(policy, profile_path, output, report):
    from scripts.roi_explore.live_preflight_169.config import (
        load_profile,
        profile_environment,
    )
    from scripts.roi_explore.live_preflight_169.run import check

    profile = load_profile(profile_path)
    require(
        not profile.problems and profile.interval == policy["source_interval_seconds"],
        "manual_profile_common_interval_mismatch",
    )
    with profile_environment(profile):
        settings = runtime_settings(policy, "manual-preflight")
        settings["manual_profile_interval_seconds"] = profile.interval
        settings["collection_enabled"] = False
        save_report(report, settings)
    return check(profile_path, output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "component", choices=("api", "ops-api", "worker", "manual-preflight")
    )
    parser.add_argument("--policy", type=Path, default=CONFIG)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--log", type=Path)
    parser.add_argument("--pidfile", type=Path)
    parser.add_argument("--policy-loaded", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    policy = load_policy(args.policy)
    apply_policy(policy)
    if args.detach:
        # A paused worker exits before main; detaching it would only record a dead PID.
        require(
            args.component in ("api", "ops-api")
            or (args.component == "worker" and not policy["collection_paused"]),
            "only_paused_apis_detach",
        )
        return spawn_detached(args)
    if args.component in ("api", "ops-api", "worker") and not args.policy_loaded:
        # exec (same PID) exposes the applied policy in the new process's initial environment,
        # as well as the runtime receipt. No credential is placed in command arguments.
        forwarded = list(sys.argv[1:] if argv is None else argv)
        os.execve(
            sys.executable,
            [
                sys.executable,
                str(Path(__file__).resolve()),
                *forwarded,
                "--policy-loaded",
            ],
            dict(os.environ),
        )
    if args.component == "manual-preflight":
        require(
            args.profile is not None and args.out is not None, "manual_paths_required"
        )
        result = manual_preflight(policy, args.profile, args.out, args.report)
        print(json.dumps(dict(state=result["state"], can_collect=False)))
        return 0
    paused = policy["collection_paused"]
    # The read-only api never talks to a source, in either mode; ops-api in active mode must
    # (its enqueue path confirms robots/limiter state through the scrape layer) and the worker always.
    block_outbound = args.component == "api" or (paused and args.component == "ops-api")
    settings = runtime_settings(policy, args.component)
    settings["outbound_python_connections_blocked"] = block_outbound
    settings["ops_mutations_blocked_before_app"] = paused and args.component == "ops-api"
    if args.component == "worker":
        settings["worker_state"] = "PAUSED_NOT_STARTED" if paused else "ACTIVE_STARTING"
    else:
        settings["worker_state"] = "NOT_A_WORKER"
    save_report(args.report, settings)
    if args.component == "worker":
        if paused:
            print("[paused] worker: no recovery, claim, fetch or model subprocess started")
            return 0
        # Active: the real daemon, in this process, with the policy already in its environment
        # (60 s interval, shared limiter, auto-capture off). Blocks until SIGTERM.
        from horseracing_ops.worker import main as worker_main

        print("[active] worker: starting horseracing_ops.worker.main", flush=True)
        worker_main()
        return 0
    serve = no_outbound_python_connections() if block_outbound else contextlib.nullcontext()
    with serve:
        import uvicorn

        if args.component == "api":
            from horseracing_api.app import app

            port = 8000
        else:
            from horseracing_ops.app import app as base

            app = PausedOps(base) if paused else base
            port = 8001
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    # Running the script from api/ops still imports the repo's shared helpers explicitly.
    sys.path.insert(0, str(ROOT))
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps(
                dict(
                    status="STOPPED",
                    error_class=type(error).__name__,
                    private_details_not_printed=True,
                    race_data_collection=False,
                )
            )
        )
        raise SystemExit(1) from None
