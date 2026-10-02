"""Feature 138 (plan 0.4・T016a, D18): record the 300/600-point checkpoint decisions.

The stage shown for a rule follows these append-only records, so a late result import, a dead-heat
correction or a re-import can never flip a decision after it was made (the ratchet is stored,
not re-derived). Called at the end of every ensemble market-ev run (``compute_and_persist``, in a
separate transaction whose failure never undoes the computed rows) and by the CLI
``attention-checkpoints``.

Material = the rule's picks (current rule set, voids resolved) × ``race_results`` only — never
``race_horses``, whose odds and statuses keep changing. Each pick is classified by
``attention_rules.classify_pick`` (the same function the API uses); a decision uses only the
``counted`` picks whose post time is at least ``CHECKPOINT_SETTLEMENT_LAG`` old, and
``attention_rules.decide_checkpoint`` orders them and takes the first N itself. Settlement is at
the judged odds (``odds_used`` × 100 yen, D13).
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from decimal import Decimal

from horseracing_db.enums import ResultStatus
from horseracing_db.models import AttentionCheckpoint, AttentionPick, RaceResult
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


def load_pick_facts(session: Session) -> dict[str, list[ar.PickFacts]]:
    """rule_id → PickFacts of every ``kind='pick'`` row of the current rule set."""
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
            RaceResult.finish_order,
        ).where(RaceResult.race_id.in_(picked_races))
    ).all()
    has_result: set[str] = set()
    winners: dict[str, int] = {}
    horse_result: dict[tuple[str, str], bool] = {}
    for r in results:
        won = r.result_status == ResultStatus.FINISHED and r.finish_order == 1
        has_result.add(r.race_id)
        winners[r.race_id] = winners.get(r.race_id, 0) + int(won)
        horse_result[(r.race_id, r.horse_id)] = won
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
            pending = [f for f, c in classes if c == "pending_result"]
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
                decision, rec_values = ar.decide_checkpoint(material, checkpoint)
                last = next(f for f in material if f.pick_id == rec_values["last_pick_id"])
                skipped = sum(1 for f in pending if _order_key(f) < _order_key(last))
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
