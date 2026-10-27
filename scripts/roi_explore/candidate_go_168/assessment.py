"""Saved evidence assessment only; this module cannot register, fetch or predict a race."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path

from scripts.roi_explore.prospective_rehearsal_162.store import publish, read
from .snapshot import ROOT, JST


def technical_exclusions(race, entries, *, now):
    """Sort/filter only identity, dates, card shape and result presence; ignore all signals."""
    reasons = []
    rid = race.get("race_id")
    if not isinstance(rid, str) or re.fullmatch(r"\d{12}", rid) is None:
        reasons.append("invalid_race_id")
    try:
        post = dt.datetime.fromisoformat(race.get("post_time") or "")
        if post.tzinfo is None:
            raise ValueError("naive_post")
    except (TypeError, ValueError):
        post = None
        reasons.append("post_time_missing_or_invalid")
    if post is not None:
        day = post.astimezone(JST).date()
        if day.year != 2026:
            reasons.append("unsupported_model_year")
        if race.get("race_date") != day.isoformat():
            reasons.append("race_date_post_mismatch")
        if post <= now:
            reasons.append("race_already_started")
        elif post - now < dt.timedelta(minutes=45):
            reasons.append("selection_45_minute_cutoff_elapsed")
    if race.get("track_type") not in ("芝", "ダ"):
        reasons.append("unsupported_track_type")
    if race.get("venue_code") not in {f"{n:02d}" for n in range(1, 11)}:
        reasons.append("jra_venue_metadata_not_proven")
    if type(race.get("result_rows")) is not int or race["result_rows"] != 0:
        reasons.append("results_present_or_unknown")
    if not entries or any(
        r.get("entry_status") not in ("started", "cancelled", "excluded")
        for r in entries
    ):
        reasons.append("entries_missing_or_unknown_status")
    ids = [r.get("horse_id") for r in entries]
    numbers = [r.get("horse_number") for r in entries]
    if (
        len(ids) != len(set(ids))
        or any(not isinstance(h, str) or not h for h in ids)
        or len(numbers) != len(set(numbers))
        or any(type(n) is not int or n < 1 for n in numbers)
    ):
        reasons.append("field_identity_invalid")
    active = [r for r in entries if r.get("entry_status") == "started"]
    if not 2 <= len(active) <= 18:
        reasons.append("active_field_outside_initial_pilot_scope")
    if len(active) != race.get("started_rows") or len(entries) != race.get(
        "entry_rows"
    ):
        reasons.append("entry_count_disagrees")
    return reasons


def rank_technical_candidates(races, entries, *, now):
    ledger = []
    for race in races:
        field = [r for r in entries if r.get("race_id") == race["race_id"]]
        reasons = technical_exclusions(race, field, now=now)
        ledger.append(
            dict(
                race_id=race["race_id"],
                race_date=race.get("race_date"),
                post_time=race.get("post_time"),
                started_rows=race.get("started_rows"),
                technical_checks_passed=not reasons,
                exclusion_reasons=reasons,
                full_card_original_identity_operator_and_go_not_assessed=True,
            )
        )
    ledger.sort(
        key=lambda r: (
            dt.datetime.fromisoformat(r["post_time"]).timestamp()
            if r.get("post_time")
            and "post_time_missing_or_invalid" not in r["exclusion_reasons"]
            else float("inf"),
            r["race_id"],
        )
    )
    return ledger, [r for r in ledger if r["technical_checks_passed"]]


def gate(status, reason, refs=()):
    return dict(status=status, reason=reason, evidence_refs=list(refs))


def access_ready_now(access):
    """Current state only; a fresh disallow or excessive backlog is never a pass."""
    robots = access["robots"]
    state = access["canonical_throttle_state_now"]
    throttle = False
    if (
        isinstance(state, dict)
        and state["blocked_now"] is False
        and access.get("source_interval_seconds") is not None
    ):
        try:
            observed = dt.datetime.fromisoformat(access["as_of_utc"])
            slot = dt.datetime.fromisoformat(state["next_allowed_at"])
            throttle = 0 <= max(0, (slot - observed).total_seconds()) <= 3
        except (TypeError, ValueError):
            pass
    return dict(
        robots=robots["fresh_now"] is True
        and robots.get("parsed_rules_allow_odds_api") is True,
        throttle=throttle,
    )


def build(output):
    root = Path(output).resolve()
    snapshot, access = read(root / "snapshot.json"), read(root / "access.json")
    local_path = ROOT / "specs/168-saved-candidate-go/evidence/local-inventory.json"
    local = read(local_path)
    now = dt.datetime.fromisoformat(snapshot["as_of_utc"])
    access_ready = access_ready_now(access)
    ledger, candidates = rank_technical_candidates(
        snapshot["upcoming_races"], snapshot["upcoming_entries"], now=now
    )
    old = read(ROOT / "artifacts/165-manual-observation/run-02/session/plan.json")
    pin_sets = dict(
        source=old["source_pins"], models=old["model_protocol"]["model_files"]
    )
    pin_check = {
        kind: dict(
            files_checked=len(pins),
            mismatches=[
                name
                for name, sha in pins.items()
                if not (ROOT / name).is_file()
                or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha
            ],
        )
        for kind, pins in pin_sets.items()
    }
    registry = ROOT / "artifacts/manual_observation_registry_165"
    reservations = (
        [str(p.relative_to(ROOT)) for p in registry.glob("*.json")]
        if registry.exists()
        else []
    )
    # Metadata passing is only provisional; original card completeness, identity map,
    # operator availability and launch-only facts are never inferred from row counts.
    gates = {
        "eligible_saved_race": gate(
            "BLOCKED" if not candidates else "PROVISIONAL_ONLY",
            "No saved current/upcoming DB race passes the metadata checks."
            if not candidates
            else "Metadata-only candidates; original card and operator gates remain unconfirmed.",
            ("snapshot.json",),
        ),
        "stored_original_future_card": gate(
            "BLOCKED"
            if local["stored_card_races_at_or_after_today"] == 0
            else "UNCONFIRMED",
            "No current/future race card in the inspected archive; navigation links are not saved cards.",
            (str(local_path.relative_to(ROOT)),),
        ),
        "card_original_age_within_5_minutes_at_launch": gate(
            "NOT_ASSESSABLE_NO_TARGET",
            "No target or launch time. Historical card capture stamps are not replaced by current read time.",
            (str(local_path.relative_to(ROOT)),),
        ),
        "complete_same_day_card_and_all_horse_identity": gate(
            "UNCONFIRMED_NO_TARGET",
            "DB card counts alone are not proof of the complete published card or all-horse source mapping.",
        ),
        "operator_available_interval": gate(
            "UNCONFIRMED",
            "No human availability window was supplied; none was assumed.",
        ),
        "live_database_configuration": gate(
            "NOT_CONFIGURED"
            if not snapshot["live_database_url_configured"]
            else "UNCONFIRMED",
            "The current shell has no live DATABASE_URL; read-only fallback capability is not live-role verification."
            if not snapshot["live_database_url_configured"]
            else "Read-only inspection does not prove the future live connection and operational-state permissions.",
            ("snapshot.json",),
        ),
        "robots_cache": gate(
            "PASS_NOW_ONLY" if access_ready["robots"] else "BLOCKED_NOW",
            "Existing race-site robots cache is expired/invalid now; no refresh was attempted. Recheck at actual launch.",
            ("access.json",),
        ),
        "source_throttle": gate(
            "PASS_NOW_ONLY" if access_ready["throttle"] else "UNCONFIRMED_OR_BLOCKED",
            "Canonical key netkeiba.com inspected read-only. No launch slot is reserved and future contention is unknown.",
            ("access.json",),
        ),
        "frozen_code_and_models": gate(
            "PASS_NOW_ONLY"
            if all(not row["mismatches"] for row in pin_check.values())
            else "BLOCKED",
            "Dependency/model files hashed without loading models. No target live plan has been created.",
            ("dependency-pins.json",),
        ),
        "clock_power_storage_and_target_path": gate(
            "PARTIAL_NOW_ONLY",
            "UTC/JST and DB snapshot clocks recorded; current output filesystem is writable. Launch power/sleep, clock sync, capacity and selected session path remain unconfirmed.",
        ),
        "registry": gate(
            "NO_LIVE_REGISTRY_NOW" if not reservations else "UNCONFIRMED_NO_TARGET",
            "Existing reservation files were read only; no plan or reservation created.",
            ("dependency-pins.json",),
        ),
        "official_raw_settlement_path_and_candidate_mapping": gate(
            "PARTIAL_EXISTING_DESIGN_ONLY",
            "165 official settlement and existing saved-result archives are available as prior evidence; no target-specific future original results or map have been verified.",
        ),
        "quote_age_at_decision": gate(
            "NOT_YET_DUE",
            "Required 0..1800 seconds from original receipt, also provider time if present. No quote fetched.",
        ),
        "both_predictions_durable_before_decision": gate(
            "NOT_YET_DUE",
            "Both durable receipts must precede decision; no models inferred or predictions saved now.",
        ),
        "three_guards_and_decision_window": gate(
            "NOT_YET_DUE",
            "Live before/after/final and [T-10,T-9) sealing remain mandatory. No observe command executed.",
        ),
        "official_results_and_payout": gate(
            "NOT_YET_DUE",
            "Actual official raw results/all winners are due after the future race; missing payouts remain null.",
        ),
    }
    result = dict(
        mode="saved_candidate_go",
        state="NO_GO_SKIP",
        as_of_utc=snapshot["as_of_utc"],
        as_of_jst=snapshot["as_of_jst"],
        selected_race_id=None,
        selected_post_time=None,
        metadata_candidates=len(candidates),
        metadata_provisional_candidates=candidates,
        upcoming_db_rows=len(snapshot["upcoming_races"]),
        saved_future_card_count=local["stored_card_races_at_or_after_today"],
        sort_keys=["post_time_utc", "race_id"],
        operator_available_interval=None,
        candidate_signal_values_used=False,
        new_collection=False,
        database_write=False,
        live_plan_created=False,
        observe_invocations=0,
        model_inference=False,
        official_settlement_executed=False,
        slot_reserved=False,
        comparison_data_added=0,
        evidence_admissible=False,
        can_adopt_model=False,
        conditions=dict(
            quote_max_age_seconds=1800,
            both_predictions_durable_before_decision=True,
            decision_window_minutes_before_post=[10, 9],
            official_payout_only=True,
        ),
        go_gates=gates,
        real_world_meeting_calendar_inferred=False,
    )
    publish(root / "candidate-ledger.json", ledger)
    publish(
        root / "dependency-pins.json",
        dict(
            pin_checks=pin_check,
            source_pins=old["source_pins"],
            model_pins=old["model_protocol"]["model_files"],
            live_registry_exists=registry.exists(),
            reservations=reservations,
        ),
    )
    publish(root / "assessment.json", result)
    operator = read(
        ROOT / "specs/167-first-manual-observation/operator-record.template.json"
    )
    operator.update(template_only=False, state="NO_GO_SKIP")
    operator["selection"].update(
        metadata_observed_at=snapshot["as_of_utc"],
        candidate_universe_source_refs=[
            dict(
                path=str((root / "snapshot.json").relative_to(ROOT)),
                sha256=hashlib.sha256(
                    (root / "snapshot.json").read_bytes()
                ).hexdigest(),
            ),
            dict(
                path=str(local_path.relative_to(ROOT)),
                sha256=hashlib.sha256(local_path.read_bytes()).hexdigest(),
            ),
        ],
        candidates_and_exclusion_reasons=ledger,
    )
    operator["stop_or_failure"].update(
        reason="NO_ELIGIBLE_SAVED_RACE_AND_UNCONFIRMED_GO",
        observed_at=snapshot["read_finished_at"],
        disposition="skip_without_starting",
    )
    operator["go_assessment_ref"] = str((root / "assessment.json").relative_to(ROOT))
    publish(root / "operator-record.json", operator)
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = build(args.out)
    print(
        {
            k: result[k]
            for k in (
                "state",
                "metadata_candidates",
                "saved_future_card_count",
                "selected_race_id",
            )
        }
    )
