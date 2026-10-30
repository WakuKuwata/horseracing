"""Official manual observer entry point while collection is paused. No live opt-in path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.source_operations_172 import (  # noqa: E402
    CONFIG,
    apply_policy,
    load_policy,
    manual_preflight,
    require,
    runtime_settings,
    save_report,
)
from scripts.roi_explore.live_preflight_169.config import (  # noqa: E402
    load_profile,
    profile_environment,
)


def observe_paused(policy, profile_path, report):
    profile = load_profile(profile_path)
    require(
        not profile.problems and profile.interval == policy["source_interval_seconds"],
        "manual_profile_common_interval_mismatch",
    )
    # Keep the dedicated role / confirmed target binding, with no DB probe or owner fallback.
    require(
        profile.public["endpoint"]["login"] == "hr_manual_observer"
        and profile.public["endpoint"]["database"] == "horseracing"
        and profile.public["endpoint"]["host"] == "127.0.0.1"
        and profile.public["endpoint"]["port"] == 15432,
        "manual_observer_endpoint_changed",
    )
    with profile_environment(profile):
        settings = runtime_settings(policy, "manual-observe")
        settings.update(
            manual_profile_interval_seconds=profile.interval,
            state="PAUSED_NOT_STARTED",
            collection_enabled=False,
            manual_observer_imported="scripts.roi_explore.manual_observation_165.cli"
            in sys.modules,
            manual_observer_called=False,
            database_access=False,
            source_slot_reserved=False,
            official_settlement_invoked=False,
            can_collect=False,
        )
        require(
            settings["manual_observer_imported"] is False,
            "observer_imported_before_pause_gate",
        )
        save_report(report, settings)
    return settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "observe"))
    parser.add_argument("--policy", type=Path, default=CONFIG)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    policy = load_policy(args.policy)
    # The manual-observation entry point (165-172) is frozen on the paused policy: it never ran
    # with collection enabled and the 2026-10-31 active mode is for ops/worker only.
    require(policy["collection_paused"] is True, "collection_pause_required")
    apply_policy(policy)
    if args.command == "observe":
        result = observe_paused(policy, args.profile, args.report)
        print(json.dumps(dict(state=result["state"], can_collect=False)))
        # An observation request is explicitly refused, never reported as an executed observation.
        return 3
    require(args.out is not None, "preflight_output_required")
    result = manual_preflight(policy, args.profile, args.out, args.report)
    print(json.dumps(dict(state=result["state"], can_collect=False)))
    return 0


if __name__ == "__main__":
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
