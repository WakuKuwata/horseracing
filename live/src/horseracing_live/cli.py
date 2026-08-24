"""Operator CLI for live serving (Feature 019). live-serve / list-pending / refresh (050)."""

from __future__ import annotations

import argparse
import datetime
import json
import math
import time
from collections import Counter

from horseracing_betting.kelly_types import KellyConfig
from horseracing_db.models import Race
from horseracing_db.session import create_db_engine
from horseracing_probability.chaos_artifact import ChaosArtifactError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .chaos_capture import (
    ChaosCaptureReport,
    NetkeibaOddsFetcher,
    capture_chaos,
    load_current_chaos_artifact,
)
from .orchestrate import collect_prospective, list_pending, live_serve, refresh_range


def _default_scrape_fn(session: Session, race_id):
    """Fresh pre-race odds capture for the prospective collection. Returns the CAPTURE completion
    timestamp (now) on success, or None to SKIP (never fall back to stale/closing odds). Uses the
    008 polite netkeiba win-odds scrape; if it fails or is blocked, the race is skipped (the
    instrument only fills with genuinely captured pre-race odds — the operational dependency)."""
    from horseracing_scrape.fetch import PoliteFetcher
    from horseracing_scrape.pipeline import scrape_odds
    from horseracing_scrape.urls import win_odds_url

    try:
        with PoliteFetcher() as fetcher:
            summary = scrape_odds(session, urls=[win_odds_url(race_id)], fetcher=fetcher)
        if getattr(summary, "error_count", 0):
            return None
        return datetime.datetime.now(datetime.UTC)
    except Exception:  # noqa: BLE001 — scrape unavailable ⇒ skip, do not use stale odds
        return None


def _validate_and_calib(args) -> tuple[str, str | None] | None:
    """Feature 076: shared calib-arg validation for the live commands. Returns (mode, path) or None
    on a validation error (caller prints + returns 2)."""
    from horseracing_betting.cli import _validate_calib_args
    try:
        return _validate_calib_args(args)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return None


def _cmd_collect_prospective(session: Session, args) -> int:
    """Feature 065: record prospective win bets on freshly-captured pre-race odds for pending races
    on a date (thin bundle over collect_prospective; scrape/settle stay separate commands)."""
    from horseracing_probability.calib_activation import ActivationError
    from horseracing_probability.calib_manifest import ManifestError
    mp = _validate_and_calib(args)
    if mp is None:
        return 2
    mode, path = mp
    ids = list_pending(session, date=args.date)
    try:
        rep = collect_prospective(
            session, race_ids=ids, scrape_fn=_default_scrape_fn, win_odds_cap=args.win_odds_cap,
            calib_manifest=path, calib_mode=mode,
        )
    except (ActivationError, ManifestError) as exc:  # Feature 076: fail-closed before the loop
        print(f"ERROR: manifest activation failed: {type(exc).__name__}: {exc}")
        return 1
    print(f"collect-prospective {args.date}  pending={rep.n_races} generated={rep.generated} "
          f"weak_pretime={rep.weak_pretime}")
    print(f"  skip: not_pending={rep.skip_not_pending} no_odds={rep.skip_no_odds} "
          f"no_run={rep.skip_no_run} exists={rep.skip_exists} post_time={rep.skip_post_time} "
          f"errors={rep.errors}")
    return 0


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


_ELIGIBLE_SKIP_REASONS = frozenset(
    {
        "already_captured",
        "outside_primary_horizon",
        "result_settled",
        "post_time_unknown",
        "post_time_elapsed",
        "min_seconds_to_post",
        "concurrent_capture",
        "no_started_horses",
        "field_too_small",
    }
)


def _capture_json(report: ChaosCaptureReport, *, elapsed_s: float) -> str:
    return json.dumps(
        {
            "race_id": report.race_id,
            "outcome": report.status,
            "reason": report.reason,
            "capture_strength": report.capture_strength,
            "confirmation_eligible": report.confirmation_eligible,
            "seconds_to_post": report.seconds_to_post,
            "chaos_snapshot_id": (
                None
                if report.chaos_snapshot_id is None
                else str(report.chaos_snapshot_id)
            ),
            "elapsed_s": elapsed_s,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _daily_horizon_refused(
    session: Session,
    target_date: datetime.date,
    *,
    command_started_at: datetime.datetime,
) -> bool:
    """Judge the date once, using its latest known post time and the current horizon."""

    latest_post_time = session.scalar(
        select(func.max(Race.post_time)).where(Race.race_date == target_date)
    )
    if latest_post_time is None:
        return False
    try:
        artifact = load_current_chaos_artifact(target_date)
    except ChaosArtifactError:
        # Preserve capture's fail-closed, per-race artifact_unavailable result.
        return False
    upper_s = int(
        artifact.preregistration["primary_horizon"]["maximum_seconds_to_post"]
    )
    return (latest_post_time - command_started_at).total_seconds() > upper_s


def _cmd_live_serve(session: Session, args) -> int:
    from horseracing_probability.calib_activation import ActivationError
    from horseracing_probability.calib_manifest import ManifestError
    mp = _validate_and_calib(args)
    if mp is None:
        return 2
    mode, path = mp
    cfg = KellyConfig(bankroll=args.bankroll, allocation=args.allocation)
    try:
        rep = live_serve(
            session, race_id=args.race_id, model_version=args.model_version,
            recommend=not args.no_recommend, cfg=cfg, threshold=args.threshold, top_k=args.top_k,
            calib_manifest=path, calib_mode=mode,
        )
    except (ActivationError, ManifestError) as exc:  # Feature 076: fail-closed
        print(f"ERROR: manifest activation failed: {type(exc).__name__}: {exc}")
        return 1
    if rep.rejected:
        print(f"REJECTED race={rep.race_id}: {rep.reason}")
        for k, (ok, reason) in rep.guards.items():
            print(f"  guard {k:<16} {'ok' if ok else 'FAIL'}  {reason}")
        return 1
    print(f"LIVE race={rep.race_id} ({rep.race_date})  prediction_run={rep.prediction_run_id}")
    print(f"  horses={rep.n_horses}  recommendations={rep.n_recommendations} (Kelly, SHADOW)")
    if rep.recommend_skipped_reason:
        print(f"  recommendations skipped: {rep.recommend_skipped_reason}")
    print(f"  odds_as_of={rep.odds_as_of}  computed_at={rep.computed_at}")
    print("  ※ live Kelly は shadow（記録のみ・実資金執行なし）。cutoff=race_date（004 継承）")
    return 0


def _cmd_list_pending(session: Session, args) -> int:
    ids = list_pending(session, date=args.date)
    print(f"result-pending races on {args.date}: {len(ids)}")
    for rid in ids:
        print(f"  {rid}")
    return 0


def _cmd_capture_chaos(session: Session, args) -> int:
    """Feature 084: freeze fresh market observations with per-race isolation."""
    from .chaos_politeness import deadline_for, make_capture_fetcher

    command_started_at = _now()
    if args.min_seconds_to_post < 0:
        print("ERROR: --min-seconds-to-post must be non-negative")
        return 2

    capture_trigger = getattr(args, "trigger", None) or (
        "explicit_command" if args.race_id is not None else "daily_operational"
    )
    deadline_s = getattr(args, "capture_deadline_seconds", None)
    if deadline_s is None:
        deadline_s = deadline_for(capture_trigger)
    if not math.isfinite(deadline_s) or deadline_s <= 0:
        print("ERROR: --capture-deadline-seconds must be positive")
        return 2

    if (
        args.date is not None
        and not getattr(args, "allow_outside_horizon", False)
        and _daily_horizon_refused(
            session,
            args.date,
            command_started_at=command_started_at,
        )
    ):
        print(
            "ERROR: latest post_time is beyond the primary horizon; "
            "use --allow-outside-horizon to run intentionally"
        )
        return 2

    race_ids = (
        [args.race_id]
        if args.race_id is not None
        else list_pending(session, date=args.date)
    )
    raw_fetcher = make_capture_fetcher(database_url=getattr(args, "database_url", None))
    fetcher = NetkeibaOddsFetcher(raw_fetcher)
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    strength_counts: Counter[str] = Counter()
    skipped_eligible = 0
    skipped_unfetchable = 0
    json_report: ChaosCaptureReport | None = None
    json_elapsed_s = 0.0

    try:
        for race_id in race_ids:
            started = time.monotonic()
            deadline = started + float(deadline_s)
            set_deadline = getattr(raw_fetcher, "set_deadline", None)
            if set_deadline is not None:
                set_deadline(deadline)
            try:
                report = capture_chaos(
                    session,
                    race_id=race_id,
                    fetcher=fetcher,
                    artifact=None,
                    capture_trigger=capture_trigger,
                    capture_policy_version="capture_policy_v1",
                    deadline=deadline,
                    min_seconds_to_post=args.min_seconds_to_post,
                )
                if report.changed:
                    session.commit()
                else:
                    session.rollback()
            except Exception as exc:  # noqa: BLE001 — one bad race must not abort the date
                session.rollback()
                report = ChaosCaptureReport(
                    race_id,
                    "failed",
                    f"error:{type(exc).__name__}",
                )

            elapsed_s = time.monotonic() - started
            status_counts[report.status] += 1
            if report.captured:
                strength_counts[str(report.capture_strength)] += 1
            else:
                reason_counts[report.reason] += 1
            if report.status == "skipped":
                if report.reason in _ELIGIBLE_SKIP_REASONS:
                    skipped_eligible += 1
                else:
                    skipped_unfetchable += 1
            if args.race_id is not None:
                json_report = report
                json_elapsed_s = elapsed_s
    finally:
        close = getattr(raw_fetcher, "close", None)
        if close is not None:
            close()

    if getattr(args, "json", False):
        assert json_report is not None
        print(_capture_json(json_report, elapsed_s=json_elapsed_s))
        return 0

    print(
        f"capture-chaos races={len(race_ids)} captured={status_counts['captured']} "
        f"skipped_eligible={skipped_eligible} "
        f"skipped_unfetchable={skipped_unfetchable} "
        f"rejected={status_counts['rejected']} failed={status_counts['failed']}"
    )
    rejected_text = " ".join(
        f"{reason}={count}" for reason, count in sorted(reason_counts.items())
    ) or "none"
    strength_text = " ".join(
        f"{strength}={strength_counts[strength]}"
        for strength in ("confirmatory", "weak", "unknown")
    )
    print(f"  rejected by reason: {rejected_text}")
    print(f"  capture_strength: {strength_text}")
    return 0


def _cmd_refresh(session: Session, args) -> int:
    """Feature 050: one-command product update — predict backfill THEN recommend backfill."""
    from horseracing_betting.cli import _validate_calib_args
    from horseracing_probability.calib_activation import ActivationError
    from horseracing_probability.calib_manifest import ManifestError
    try:
        mode, path = _validate_calib_args(args)  # Feature 076: abs path / mode↔manifest contradiction
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    try:
        rep = refresh_range(
            session, date_from=args.from_, date_to=args.to, force=args.force,
            calib_manifest=path, calib_mode=mode,
            use_materialized=args.use_materialized,
            materialized_path=args.materialized_path if args.use_materialized else None,
        )
    except (ActivationError, ManifestError) as exc:  # Feature 076: preflight fail-closed (T018)
        print(f"ERROR: manifest activation failed: {type(exc).__name__}: {exc}")
        return 1
    print(f"refresh {rep.date_from}..{rep.date_to}")
    if rep.predict is not None:
        p = rep.predict
        print(f"  predict:   generated={p['generated']} skip_exists={p['skip_exists']} "
              f"skip_no_started={p['skip_no_started']} error_days={p['error_days']}")
    else:
        print(f"  predict:   FAILED — {rep.predict_error}")
    if rep.recommend is not None:
        r = rep.recommend
        print(f"  recommend: races={r['races']} generated={r['generated']} "
              f"topped_up={r['topped_up']} skip_exists={r['skip_exists']} "
              f"skip_no_run={r['skip_no_run']} skip_no_odds={r['skip_no_odds']} "
              f"error={r['error']}")
    else:
        print(f"  recommend: FAILED — {rep.recommend_error}")
    for e in (rep.predict or {}).get("errors", []):
        print(f"  predict error {e['day']}: {e['error']}")
    return 0 if rep.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="live")
    sub = parser.add_subparsers(dest="command", required=True)

    ls = sub.add_parser("live-serve", help="predict + recommend for an upcoming (pending) race")
    ls.add_argument("race_id")
    ls.add_argument("--model-version", default=None)
    ls.add_argument("--no-recommend", action="store_true")
    ls.add_argument("--bankroll", type=float, default=100.0)
    ls.add_argument("--allocation", choices=["exact", "heuristic"], default="exact")
    ls.add_argument("--threshold", type=float, default=1.0)
    ls.add_argument("--top-k", type=int, default=5)
    from horseracing_betting.cli import _add_calib_manifest_args
    _add_calib_manifest_args(ls)  # Feature 076: stage-λ (serving) + two-gamma (recommend)
    ls.add_argument("--database-url", default=None)

    lp = sub.add_parser("list-pending", help="list valid result-pending races on a date")
    lp.add_argument("--date", type=_parse_date, required=True)
    lp.add_argument("--database-url", default=None)

    rf = sub.add_parser("refresh",
                        help="one-command product update: predict backfill → recommend backfill "
                             "over a date range (050)")
    rf.add_argument("--from", dest="from_", type=_parse_date, required=True)
    rf.add_argument("--to", type=_parse_date, required=True)
    rf.add_argument("--force", action="store_true",
                    help="re-generate predictions (044 append-only); recommendations stay "
                         "group-wise idempotent")
    rf.add_argument("--use-materialized", action="store_true",
                    help="055: prediction stage reads as-of features from the 025 parquet "
                         "(bit-parity, fail-closed; recommend stage builds no features)")
    rf.add_argument("--materialized-path", default="../artifacts/features.parquet")
    from horseracing_betting.cli import _add_calib_manifest_args
    _add_calib_manifest_args(rf)  # Feature 076: --calib-manifest / --calib-mode (both stages)
    rf.add_argument("--database-url", default=None)

    cp = sub.add_parser("collect-prospective",
                        help="065: record prospective win bets on freshly-captured pre-race odds "
                             "for pending races on a date (fills the shadow-log)")
    cp.add_argument("--date", type=_parse_date, required=True)
    cp.add_argument("--win-odds-cap", dest="win_odds_cap", type=float, default=None,
                    help="064: optional win odds cap policy for the prospective bets")
    from horseracing_betting.cli import _add_calib_manifest_args as _acm
    _acm(cp)  # Feature 076: manifest two-gamma for prospective recommendations
    cp.add_argument("--database-url", default=None)

    cc = sub.add_parser(
        "capture-chaos",
        help="084: freeze fresh pre-race odds and the top-3 chaos readout",
    )
    target = cc.add_mutually_exclusive_group(required=True)
    target.add_argument("--race-id", default=None)
    target.add_argument("--date", type=_parse_date, default=None)
    cc.add_argument(
        "--min-seconds-to-post",
        type=int,
        default=0,
        help="skip a race when fewer than this many seconds remain (default: 0)",
    )
    cc.add_argument(
        "--trigger",
        choices=(
            "daily_operational",
            "predict_manual",
            "predict_auto",
            "explicit_command",
        ),
        default=None,
    )
    cc.add_argument("--json", action="store_true")
    cc.add_argument("--capture-deadline-seconds", type=float, default=None)
    cc.add_argument("--allow-outside-horizon", action="store_true")
    cc.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    if args.command == "capture-chaos":
        if args.json and args.date is not None:
            parser.error("--json is only valid with --race-id")
        if args.allow_outside_horizon and args.race_id is not None:
            parser.error("--allow-outside-horizon is only valid with --date")
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        if args.command == "live-serve":
            return _cmd_live_serve(session, args)
        if args.command == "list-pending":
            return _cmd_list_pending(session, args)
        if args.command == "refresh":
            return _cmd_refresh(session, args)
        if args.command == "collect-prospective":
            return _cmd_collect_prospective(session, args)
        if args.command == "capture-chaos":
            return _cmd_capture_chaos(session, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
