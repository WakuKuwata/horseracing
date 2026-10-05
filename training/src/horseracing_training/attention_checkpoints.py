"""Feature 138 (plan 0.4・T016a, D18): record the 300/600-point checkpoint decisions.

The stage shown for a rule follows these append-only records, so a late result import, a dead-heat
correction or a re-import can never flip a decision after it was made (the ratchet is stored,
not re-derived). Called at the end of every ensemble market-ev run (``compute_and_persist``, in a
separate transaction whose failure never undoes the computed rows) and by the CLI
``attention-checkpoints``.

Material = the rule's picks (current rule set, voids resolved) × ``race_results`` ×
``official_win_payouts``, plus the 馬番 (``race_horses.horse_number``, fixed at the draw) of each
race's 1st-place finishers for the race-level payout check — never ``race_horses``' odds or
statuses, which keep changing. Each
pick is classified by ``attention_rules.classify_pick`` (the same function the API uses); a
decision uses only the ``counted`` picks whose post time is at least ``CHECKPOINT_SETTLEMENT_LAG``
old, and ``attention_rules.decide_checkpoint`` orders them and takes the first N itself.

Selection policy v2 (feature 139 D3/D11/D12): settlement is at the official win payout
(``attention_rules.official_payout``, per 100 yen) — what a win bet actually pays — instead of
v1's judged odds (``odds_used`` × 100 yen, 138 D13). A race with results but no official win
payout at all is ``payout_race_missing`` and a race whose payout rows disagree with its result
(the 馬番 of its winners are not exactly the paid 馬番) is ``payout_inconsistent`` — both at race
level, so every pick of such a race is left out (never only its winner): neither is material.
Only v2 records are read and written, and v2 counts from its own start date (the v1-period picks
are ``before_start``).
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from decimal import Decimal

from horseracing_db.enums import ResultStatus
from horseracing_db.models import (
    AttentionCheckpoint,
    AttentionPick,
    OfficialWinPayout,
    RaceHorse,
    RaceResult,
)
from horseracing_eval import attention_rules as ar
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

LOCK_KEY = "attention_checkpoints"
MISMATCH = "CheckpointStartDateMismatch"


@dataclass(frozen=True)
class _Record:
    decision: str
    prospective_start_date: datetime.date


#: the decision's own post order (``attention_rules.order_key`` over CHECKPOINT_ORDER)
_order_key = ar.order_key
#: policy v2 settles every decision at the official win payout (passed explicitly: the record's
#: ``bootstrap["settlement"]`` names it, so a changed default can never relabel a decision)
SETTLEMENT = ar.official_payout
#: classes of picks that are not settleable YET (their material may still arrive): counted in
#: ``skipped_pending_before_last`` when they precede the decision's last pick
_AWAITING_SETTLEMENT = frozenset({"pending_result", "payout_race_missing"})


def load_pick_facts(session: Session) -> dict[str, list[ar.PickFacts]]:
    """rule_id → PickFacts of every ``kind='pick'`` row of the current rule set.

    The official win payout is looked up by the pick's 馬番 (``official_payout_yen``),
    ``race_payout_known`` is whether the race has any payout row and ``race_payout_consistent``
    is ``attention_rules.race_payout_consistent`` over the 馬番 of the race's 1st-place finishers
    (``race_horses.horse_number`` — a winner without one never matches) and its paid 馬番. A pick
    of a race without a payout keeps the defaults (no payout, race unknown), so it can never be
    counted."""
    pick = aliased(AttentionPick)
    void = aliased(AttentionPick)
    voided = set(
        session.scalars(
            select(void.voids_pick_id).where(
                void.kind == "void", void.rule_set_version == ar.RULE_SET_VERSION
            )
        )
    )
    rows = session.execute(
        select(
            pick.pick_id, pick.rule_id, pick.race_id, pick.horse_id, pick.horse_number,
            pick.computed_at, pick.post_time, pick.odds_observed_at,
            pick.result_pending_at_compute, pick.odds_used,
        ).where(pick.kind == "pick", pick.rule_set_version == ar.RULE_SET_VERSION)
    ).all()
    picked_races = (
        select(AttentionPick.race_id)
        .where(AttentionPick.kind == "pick", AttentionPick.rule_set_version == ar.RULE_SET_VERSION)
        .distinct()
    )
    results = session.execute(
        select(
            RaceResult.race_id, RaceResult.horse_id, RaceResult.result_status,
            RaceResult.finish_order, RaceHorse.horse_number,
        )
        .select_from(RaceResult)
        .outerjoin(
            RaceHorse,
            (RaceHorse.race_id == RaceResult.race_id) & (RaceHorse.horse_id == RaceResult.horse_id),
        )
        .where(RaceResult.race_id.in_(picked_races))
    ).all()
    payouts = session.execute(
        select(
            OfficialWinPayout.race_id, OfficialWinPayout.horse_number, OfficialWinPayout.payout_yen,
        ).where(OfficialWinPayout.race_id.in_(picked_races))
    ).all()
    payout_by_number: dict[tuple[str, int], float] = {
        (p.race_id, int(p.horse_number)): float(p.payout_yen) for p in payouts
    }
    paid_numbers: dict[str, list[int]] = {}
    for p in payouts:
        paid_numbers.setdefault(p.race_id, []).append(int(p.horse_number))
    has_result: set[str] = set()
    winners: dict[str, int] = {}
    winner_numbers: dict[str, list[int | None]] = {}
    horse_result: dict[tuple[str, str], bool] = {}
    for r in results:
        won = r.result_status == ResultStatus.FINISHED and r.finish_order == 1
        has_result.add(r.race_id)
        winners[r.race_id] = winners.get(r.race_id, 0) + int(won)
        if won:
            winner_numbers.setdefault(r.race_id, []).append(
                None if r.horse_number is None else int(r.horse_number)
            )
        horse_result[(r.race_id, r.horse_id)] = won
    consistent = {
        race_id: ar.race_payout_consistent(winner_numbers.get(race_id, ()), numbers)
        for race_id, numbers in paid_numbers.items()
    }
    by_rule: dict[str, list[ar.PickFacts]] = {}
    for row in rows:
        key = (row.race_id, row.horse_id)
        by_rule.setdefault(row.rule_id, []).append(
            ar.PickFacts(
                pick_id=str(row.pick_id),
                race_id=row.race_id,
                horse_number=int(row.horse_number),
                voided=row.pick_id in voided,
                computed_at=row.computed_at,
                post_time=row.post_time,
                odds_observed_at=row.odds_observed_at,
                result_pending_at_compute=bool(row.result_pending_at_compute),
                has_race_result=row.race_id in has_result,
                horse_has_result=key in horse_result,
                won=horse_result.get(key, False),
                # race level, zero winners included (the frozen backtest's n_winners != 1)
                dead_heat=row.race_id in has_result and winners.get(row.race_id, 0) != 1,
                odds_used=float(row.odds_used),
                official_payout_yen=payout_by_number.get((row.race_id, int(row.horse_number))),
                race_payout_known=row.race_id in paid_numbers,
                race_payout_consistent=consistent.get(row.race_id, False),
            )
        )
    return by_rule


def _existing_records(session: Session) -> dict[tuple[str, int], _Record]:
    rows = session.execute(
        select(
            AttentionCheckpoint.rule_id, AttentionCheckpoint.checkpoint,
            AttentionCheckpoint.decision, AttentionCheckpoint.prospective_start_date,
        ).where(
            AttentionCheckpoint.selection_policy_version == ar.SELECTION_POLICY_VERSION,
            AttentionCheckpoint.rule_set_version == ar.RULE_SET_VERSION,
        )
    ).all()
    return {(r.rule_id, int(r.checkpoint)): _Record(r.decision, r.prospective_start_date)
            for r in rows}


def _num(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(repr(float(value)))


def evaluate_checkpoints(
    session: Session,
    *,
    now: datetime.datetime,
    run_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> dict:
    """Decide and record every checkpoint that is due; commits (or rolls back on ``dry_run``).

    Returns ``{"written": [(rule, checkpoint, decision)], "pending": [(rule, checkpoint)],
    "error": str | None, "prospective_start_date": iso | None, "dry_run": bool}``. ``pending``
    = the live count reached a checkpoint that has no record yet (waiting for the settlement
    lag). A 600 decision is written only after a 300 ``continue`` taken under the CURRENT
    prospective start date; a record taken under another start date is never extended and is
    reported as ``error`` (fail-closed). With no start date nothing is counted or written.

    Decisions settle at the official win payout (policy v2) and are recorded with
    ``selection_policy_version = attention_rules.SELECTION_POLICY_VERSION``.
    ``skipped_pending_before_last`` counts the picks before the last material one that were not
    settleable yet (``pending_result`` or ``payout_race_missing``).
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    start = ar.PROSPECTIVE_START_DATE  # read at call time: the go-live date is set later
    out: dict = {
        "written": [],
        "pending": [],
        "error": None,
        "prospective_start_date": None if start is None else start.isoformat(),
        "dry_run": dry_run,
    }
    if start is None:
        return out  # every pick is before_start: nothing is counted
    cutoff = now - ar.CHECKPOINT_SETTLEMENT_LAG
    errors: list[str] = []
    try:
        session.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": LOCK_KEY})
        records = _existing_records(session)
        facts_by_rule = load_pick_facts(session)
        for rule_id in ar.RULE_IDS:
            facts = facts_by_rule.get(rule_id, [])
            classes = [(f, ar.classify_pick(f, start_date=start)) for f in facts]
            counted = [f for f, c in classes if c == "counted"]
            material = [f for f in counted if f.post_time <= cutoff]
            awaiting = [f for f, c in classes if c in _AWAITING_SETTLEMENT]
            for checkpoint in ar.CHECKPOINTS:
                rec = records.get((rule_id, checkpoint))
                if rec is not None:
                    if rec.prospective_start_date != start:
                        errors.append(
                            f"{rule_id}/{checkpoint} recorded under "
                            f"{rec.prospective_start_date.isoformat()} != current "
                            f"{start.isoformat()}"
                        )
                    continue
                if checkpoint != ar.CHECKPOINTS[0]:
                    first = records.get((rule_id, ar.CHECKPOINTS[0]))
                    if (
                        first is None
                        or first.decision != "continue"
                        or first.prospective_start_date != start
                    ):
                        break
                if len(material) < checkpoint:
                    if len(counted) >= checkpoint:
                        out["pending"].append((rule_id, checkpoint))
                    break
                decision, rec_values = ar.decide_checkpoint(
                    material, checkpoint, payout_of=SETTLEMENT
                )
                last = next(f for f in material if f.pick_id == rec_values["last_pick_id"])
                skipped = sum(1 for f in awaiting if _order_key(f) < _order_key(last))
                row = {
                    "checkpoint_id": uuid.uuid4(),
                    "rule_id": rule_id,
                    "checkpoint": checkpoint,
                    "selection_policy_version": ar.SELECTION_POLICY_VERSION,
                    "rule_set_version": ar.RULE_SET_VERSION,
                    "decision": decision,
                    "n_counted": rec_values["n_counted"],
                    "n_hits": rec_values["n_hits"],
                    "roi_frozen": _num(rec_values["roi_frozen"]),
                    "ci_low": _num(rec_values["ci_low"]),
                    "ci_high": _num(rec_values["ci_high"]),
                    "bootstrap": rec_values["bootstrap"],
                    "counted_pick_ids_sha256": rec_values["counted_pick_ids_sha256"],
                    "last_pick_id": uuid.UUID(rec_values["last_pick_id"]),
                    "settlement_cutoff": cutoff,
                    "prospective_start_date": start,
                    "skipped_pending_before_last": skipped,
                    "decided_at": now,
                    "run_id": run_id,
                }
                if not dry_run:
                    inserted = session.execute(
                        pg_insert(AttentionCheckpoint)
                        .values(**row)
                        .on_conflict_do_nothing(constraint="uq_attention_checkpoints_decision")
                        .returning(AttentionCheckpoint.checkpoint_id)
                    ).first()
                    if inserted is None:
                        break  # already decided by someone else: the stored record stands
                records[(rule_id, checkpoint)] = _Record(decision, start)
                out["written"].append((rule_id, checkpoint, decision))
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    if errors:
        out["error"] = f"{MISMATCH}: " + "; ".join(errors)
    return out
