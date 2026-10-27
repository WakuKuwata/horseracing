"""Pure official-dividend settlement for a sealed manual observation.

No fetch, DB, clock read, final-odds parser or guessed horse-ID transformation is used.
Any non-exact source ID requires the identity map frozen in the decision session.
"""

from __future__ import annotations

from scripts.roi_explore.model_connection_163.contract import (
    ARMS,
    Rejected,
    field,
    keys,
    require,
    sha_bytes,
    time,
)
from scripts.roi_explore.prospective_rehearsal_162.settlement import parse_official

MODE = "manual_observation"
ORIGINS = ("manual_live", "fixture_rehearsal")


def _mapped_results(raw_results, expected, identity_map):
    require(isinstance(identity_map, list), "identity_map_schema_invalid")
    expected_by_id = {horse_id: number for horse_id, number in expected}
    aliases, targets = {}, set()
    for mapping in identity_map:
        keys(mapping, ["source_id", "canonical_id"], "identity_mapping")
        source, target = mapping["source_id"], mapping["canonical_id"]
        require(
            isinstance(source, str) and source and isinstance(target, str) and target,
            "identity_map_invalid",
        )
        require(
            source not in aliases
            and target not in targets
            and target in expected_by_id,
            "identity_map_ambiguous_or_outside_field",
        )
        if source in expected_by_id:
            require(source == target, "identity_map_conflicts_with_exact_id")
        aliases[source] = target
        targets.add(target)
    result = []
    for row in raw_results:
        source_id, number = row["horse_id"], row["horse_number"]
        if (source_id, number) in expected:
            canonical_id = source_id
        else:
            require(source_id in aliases, "source_horse_id_mapping_missing")
            canonical_id = aliases[source_id]
            require(
                (canonical_id, number) in expected, "identity_map_horse_number_mismatch"
            )
        result.append(dict(row, horse_id=canonical_id))
    if result:
        field(result)  # Reject duplicates introduced by mapping; never collapse them.
    return result


def settle(decision, evidence, *, observed_now):
    """Value both arms only from complete, matching printed official win dividends.

    ``observed_now`` is supplied by the real-clock store; tests use an explicitly fixed clock.
    A missing result or dividend leaves both arms unvalued, including no-bet arms.
    """
    require(
        decision["mode"] == MODE
        and decision["origin"] in ORIGINS
        and type(decision["evidence_admissible"]) is bool
        and decision["can_adopt_model"] is False,
        "manual_decision_required",
    )
    require(
        decision["origin"] != "fixture_rehearsal"
        or decision["evidence_admissible"] is False,
        "fixture_cannot_be_admissible",
    )
    expected_field = field(decision["field"])
    require(set(decision["arms"]) == set(ARMS), "invalid_settlement_arms")
    for arm in ARMS:
        tickets = decision["arms"][arm]["tickets"]
        require(
            decision["arms"][arm]["budget_yen"] == 1000 and isinstance(tickets, list),
            "invalid_budget_or_tickets",
        )
        selected = set()
        for ticket in tickets:
            keys(ticket, ["horse_id", "horse_number", "stake_yen"], "ticket")
            pair = (ticket["horse_id"], ticket["horse_number"])
            require(
                pair in expected_field
                and pair not in selected
                and type(ticket["stake_yen"]) is int
                and ticket["stake_yen"] == 100,
                "invalid_ticket",
            )
            selected.add(pair)
        require(len(tickets) <= 10, "invalid_ticket_count")
    result = dict(
        mode=MODE,
        origin=decision["origin"],
        evidence_admissible=decision["evidence_admissible"],
        can_adopt_model=False,
        race_id=decision["race_id"],
        revision=evidence.get("revision"),
        observed_at=evidence.get("observed_at"),
        status="inconsistent",
        reason=None,
        primary_eligible=False,
        dead_heat=None,
        arms={
            a: dict(
                stake_yen=sum(t["stake_yen"] for t in decision["arms"][a]["tickets"]),
                payout_yen=None,
                profit_yen=None,
            )
            for a in ARMS
        },
    )
    try:
        keys(
            evidence,
            [
                "race_id",
                "revision",
                "observed_at",
                "source",
                "raw_html",
                "raw_sha256",
                "results",
                "payouts",
            ],
            "settlement_evidence",
        )
        require(
            type(evidence["revision"]) is int and evidence["revision"] >= 1,
            "invalid_revision",
        )
        require(evidence["race_id"] == decision["race_id"], "settlement_race_mismatch")
        require(evidence["source"] == "netkeiba_result", "unverified_official_source")
        observed = time(evidence["observed_at"])
        require(observed <= time(observed_now), "official_observation_from_future")
        require(observed > time(decision["sealed_at"]), "settlement_before_seal")
        require(observed > time(decision["post_time"]), "settlement_before_post")
        require(
            sha_bytes(evidence["raw_html"].encode()) == evidence["raw_sha256"],
            "official_raw_sha_mismatch",
        )
        raw_results, raw_payouts = parse_official(
            evidence["raw_html"], evidence["race_id"]
        )
        rows = evidence["results"]
        require(isinstance(rows, list), "official_results_schema_invalid")
        if not rows:
            require(not raw_results, "normalized_results_missing")
            result.update(status="pending", reason="results_pending")
            return result
        actual = field(rows)
        if actual != expected_field:
            result.update(
                status="incomplete", reason="field_changed_or_results_incomplete"
            )
            return result
        mapped = _mapped_results(
            raw_results, expected_field, decision.get("identity_map", [])
        )
        require(
            sorted(mapped, key=lambda r: r["horse_id"])
            == sorted(rows, key=lambda r: r["horse_id"]),
            "official_raw_results_mismatch",
        )
        winners = set()
        for row in rows:
            keys(
                row,
                ["horse_id", "horse_number", "result_status", "finish_order"],
                "official_result_row",
            )
            if row["result_status"] == "finished":
                rank = row["finish_order"]
                require(
                    type(rank) is int and 1 <= rank <= len(rows), "invalid_finish_order"
                )
                if rank == 1:
                    winners.add(row["horse_number"])
            else:
                require(
                    row["result_status"] in ("stopped", "disqualified")
                    and row["finish_order"] is None,
                    "invalid_result_status",
                )
        require(bool(winners), "official_winner_missing")
        require(isinstance(evidence["payouts"], list), "official_payout_schema_invalid")
        payouts = {}
        for row in evidence["payouts"]:
            keys(row, ["horse_number", "payout_yen"], "official_payout_row")
            number, value = row["horse_number"], row["payout_yen"]
            require(
                type(number) is int
                and number >= 1
                and type(value) is int
                and value >= 100
                and number not in payouts,
                "invalid_official_payout",
            )
            payouts[number] = value
        if not payouts and not raw_payouts:
            result.update(status="incomplete", reason="official_payout_missing")
            return result
        require(
            payouts == raw_payouts and set(payouts) == winners,
            "official_payout_winner_mismatch",
        )
        for arm in ARMS:
            account = result["arms"][arm]
            account["payout_yen"] = sum(
                ticket["stake_yen"] // 100 * payouts.get(ticket["horse_number"], 0)
                for ticket in decision["arms"][arm]["tickets"]
            )
            account["profit_yen"] = account["payout_yen"] - account["stake_yen"]
        result.update(
            status="settled",
            reason=None,
            dead_heat=len(winners) > 1,
            primary_eligible=len(winners) == 1 and decision["evidence_admissible"],
        )
        return result
    except (Rejected, KeyError, TypeError, ValueError) as exc:
        result["reason"] = str(exc)
        return result
