"""Feature 138 (plan 0.4・T016): record the 注目条件 (attention-condition) picks of a market-ev run.

Called by ``market_ev.compute_and_persist`` inside its write transaction, after both versions'
rows are written and while the per-date advisory locks are held. The inputs are the in-memory
predictions of that SAME run (one run_id / computed_at); nothing is read back from the
prediction table, so a pick can never mix values of two runs.

Three steps (spec 「前向き検証の規約」, plan D16 / D26):

1. **void pass** — every still-valid pick of a race dated in the run's range whose horse now has a
   race_horses row marked 出走取消 / 競走除外 gets one ``kind='void'`` row (``scratched``). A pick
   whose (race, horse) row no longer exists (an ID re-key, 067) is NOT voided: it cannot join a
   result and is excluded from the tally as ``unsettled_horse``. A changed field is never a void
   (a win pick settles on its own horse only — D11); the API flags it from ``field_digest``.
2. **first computation** — one ``attention_race_scans`` row per computed race via
   ``INSERT … ON CONFLICT DO NOTHING RETURNING``. Only a run that inserted the row is the race's
   first computation; later runs never add picks, even for a race that had none.
3. **picks** — in a first computation, every (horse, rule) that ``attention_rules`` says applies,
   matched on the values exactly as stored (``market_ev.stored_values``).

The rules themselves live in ``horseracing_eval.attention_rules`` (the single definition shared
with the API and the freeze script). This module is a writer of display/audit rows only; the main
win model's feature path never imports it (features leak guard).
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
from horseracing_db.enums import EntryStatus
from horseracing_db.models import AttentionPick, AttentionRaceScan, Race, RaceHorse
from horseracing_eval import attention_rules as ar
from sqlalchemy import and_, exists, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from .market_ev import ENSEMBLE_LOGIC_VERSION, stored_values

#: recorded on every pick / void row: the ensemble logic + the selection policy it was taken under
PICK_LOGIC_VERSION = f"{ENSEMBLE_LOGIC_VERSION};policy={ar.SELECTION_POLICY_VERSION}"
VOID_SCRATCHED = "scratched"


@dataclass(frozen=True)
class PickContext:
    """What one run shares across its scan / pick / void rows."""

    run_id: uuid.UUID
    computed_at: datetime.datetime
    ensemble_model_version: str
    single_model_version: str
    #: races that had a race_results row when the run read its odds
    with_results: frozenset[str]
    #: races.post_time at compute time (races missing here are treated as unknown)
    post_times: Mapping[str, datetime.datetime | None]
    #: started horse ids per race at compute time (field_digest)
    started: Mapping[str, tuple[str, ...]]

    def result_pending(self, race_id: str, post_time: datetime.datetime | None) -> bool:
        """No race_results row at compute time AND (post time unknown OR computed before it)."""
        return race_id not in self.with_results and (
            post_time is None or self.computed_at < post_time
        )

    def seconds_to_post(self, post_time: datetime.datetime | None) -> int | None:
        if post_time is None:
            return None
        return int(round((post_time - self.computed_at).total_seconds()))

    def field_digest(self, race_id: str) -> str:
        return ar.field_digest(self.started.get(race_id, ()))

    def row_columns(self, race_id: str, post_time: datetime.datetime | None) -> dict:
        """Columns every pick / void row of this run carries."""
        return {
            "post_time": post_time,
            "seconds_to_post": self.seconds_to_post(post_time),
            "result_pending_at_compute": self.result_pending(race_id, post_time),
            "field_digest": self.field_digest(race_id),
            "ensemble_model_version": self.ensemble_model_version,
            "single_model_version": self.single_model_version,
            "logic_version": PICK_LOGIC_VERSION,
            "run_id": self.run_id,
            "selection_policy_version": ar.SELECTION_POLICY_VERSION,
            "rule_set_version": ar.RULE_SET_VERSION,
            "computed_at": self.computed_at,
        }


def _decimal_or_none(value) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    return Decimal(repr(float(value)))


def candidate_picks(
    pred_ens: pd.DataFrame, pred_single: pd.DataFrame, feats: pd.DataFrame
) -> tuple[dict[str, list[dict]], int]:
    """(race_id → pick values of every applicable (horse, rule)), number of (horse, rule) pairs
    skipped because the horse has no number (a pick row requires it).

    ``pred_ens`` / ``pred_single`` are the two versions' predictions of the same rows;
    ``days_since_last`` comes from the features those predictions were made from."""
    single_ev: dict[tuple[str, str], Decimal] = {
        (str(r), str(h)): stored_values(p, o)[2]
        for r, h, p, o in zip(
            pred_single["race_id"], pred_single["horse_id"], pred_single["win_prob"],
            pred_single["odds_used"], strict=True,
        )
    }
    days: dict[tuple[str, str], object] = dict(
        zip(
            zip(feats["race_id"].astype(str), feats["horse_id"].astype(str), strict=True),
            feats["days_since_last"],
            strict=True,
        )
    )
    by_race: dict[str, list[dict]] = {}
    skipped = 0
    for rec in pred_ens.to_dict("records"):
        key = (str(rec["race_id"]), str(rec["horse_id"]))
        _, odds_used, ens_ev = stored_values(rec["win_prob"], rec["odds_used"])
        single = single_ev[key]  # same rows by construction (market_ev checks it, fail-closed)
        gap = _decimal_or_none(days.get(key))
        rules = ar.applicable_rules(
            ens_ev=float(ens_ev),
            single_ev=float(single),
            odds=float(odds_used),
            days_since_last=None if gap is None else float(gap),
        )
        if not rules:
            continue
        number = rec["horse_number"]
        if pd.isna(number):
            skipped += len(rules)
            continue
        observed = pd.Timestamp(rec["odds_observed_at"]).to_pydatetime()
        for rule_id in rules:
            by_race.setdefault(key[0], []).append({
                "race_id": key[0],
                "horse_id": key[1],
                "horse_number": int(number),
                "rule_id": rule_id,
                "ens_expected_return": ens_ev,
                "single_expected_return": single,
                "odds_used": odds_used,
                "odds_observed_at": observed,
                "days_since_last": gap,
            })
    return by_race, skipped


def void_scratched(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    ctx: PickContext,
) -> int:
    """Append one ``void(scratched)`` per still-valid pick (current rule set) of a race dated in
    [date_from, date_to] whose horse's race_horses row is a non-starter. Idempotent (partial
    UNIQUE on ``voids_pick_id`` + ``ON CONFLICT DO NOTHING``). Returns the voids written."""
    pick = aliased(AttentionPick)
    void = aliased(AttentionPick)
    targets = session.execute(
        select(
            pick.pick_id, pick.race_id, pick.horse_id, pick.rule_id, pick.rule_set_version,
            pick.kind, Race.post_time,
        )
        .join(Race, Race.race_id == pick.race_id)
        .join(
            RaceHorse,
            and_(RaceHorse.race_id == pick.race_id, RaceHorse.horse_id == pick.horse_id),
        )
        .where(
            pick.kind == "pick",
            pick.rule_set_version == ar.RULE_SET_VERSION,
            Race.race_date >= date_from,
            Race.race_date <= date_to,
            RaceHorse.entry_status.in_(EntryStatus.NON_STARTERS),
            ~exists().where(void.kind == "void", void.voids_pick_id == pick.pick_id),
        )
        .order_by(pick.race_id, pick.horse_id, pick.rule_id)
    ).all()
    written = 0
    for t in targets:
        # the void targets a pick row of the same key (a cross-row CHECK is not expressible)
        if t.kind != "pick" or t.rule_set_version != ar.RULE_SET_VERSION:
            raise RuntimeError(f"void target {t.pick_id} is not a current-rule-set pick row")
        values = {
            "pick_id": uuid.uuid4(),
            "race_id": t.race_id,
            "horse_id": t.horse_id,
            "horse_number": None,
            "rule_id": t.rule_id,
            "kind": "void",
            "void_reason": VOID_SCRATCHED,
            "voids_pick_id": t.pick_id,
            "ens_expected_return": None,
            "single_expected_return": None,
            "odds_used": None,
            "odds_observed_at": None,
            "days_since_last": None,
            **ctx.row_columns(t.race_id, t.post_time),
        }
        inserted = session.execute(
            pg_insert(AttentionPick)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["voids_pick_id"], index_where=text("kind = 'void'")
            )
            .returning(AttentionPick.pick_id)
        ).first()
        written += int(inserted is not None)
    return written


def record_attention(
    session: Session,
    *,
    pred_ens: pd.DataFrame,
    pred_single: pd.DataFrame,
    feats: pd.DataFrame,
    ctx: PickContext,
    void_date_from: datetime.date,
    void_date_to: datetime.date,
) -> dict:
    """The void pass, then the scan row of every computed race and the picks of the races whose
    scan row this run inserted. The caller owns the transaction (and the advisory locks)."""
    voided = void_scratched(session, date_from=void_date_from, date_to=void_date_to, ctx=ctx)
    by_race, skipped = candidate_picks(pred_ens, pred_single, feats)
    first = already = written = 0
    for race_id in sorted(pred_ens["race_id"].astype(str).unique()):
        post_time = ctx.post_times.get(race_id)
        picks = by_race.get(race_id, [])
        inserted = session.execute(
            pg_insert(AttentionRaceScan)
            .values(
                race_id=race_id,
                rule_set_version=ar.RULE_SET_VERSION,
                run_id=ctx.run_id,
                computed_at=ctx.computed_at,
                post_time=post_time,
                result_pending_at_compute=ctx.result_pending(race_id, post_time),
                field_digest=ctx.field_digest(race_id),
                # counted before the insert: a scan row cannot be updated afterwards
                n_picks=len(picks),
            )
            .on_conflict_do_nothing(index_elements=["race_id", "rule_set_version"])
            .returning(AttentionRaceScan.race_id)
        ).first()
        if inserted is None:
            already += 1  # not the first computation: no picks, even if horses match now
            continue
        first += 1
        if not picks:
            continue
        common = ctx.row_columns(race_id, post_time)
        rows = [
            {
                **p,
                **common,
                "pick_id": uuid.uuid4(),
                "kind": "pick",
                "void_reason": None,
                "voids_pick_id": None,
            }
            for p in picks
        ]
        session.execute(insert(AttentionPick.__table__), rows)
        written += len(rows)
    return {
        "races_first_computed": first,
        "races_already_scanned": already,
        "written": written,
        "voided_scratched": voided,
        "skipped_no_horse_number": skipped,
    }
