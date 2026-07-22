"""Feature 079: paired EV-weight gate RUNNER (walk-forward row collection + orchestration).

Ties the frozen OOF bundle (074), the two recipe-faithful arms (baseline unweighted vs
EV-weighted candidate), and the pure paired gate (eval.ev_weight_gate) together. Both arms are
re-fit on IDENTICAL expanding outer folds and scored on the same valid races; the candidate's
per-race training weight is built from the base model's frozen OOF win prob (strict-past).

This is the single pre-registered retrospective run. It writes an artifact-only evidence JSON (no
model_version row — codex #3 isolation) and reports ADOPT/REJECT/NO_DECISION.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from horseracing_eval.dataset import load_eval_races
from horseracing_eval.ev_weight_gate import evaluate_ev_weight_gate
from horseracing_eval.splits import expanding_folds
from sqlalchemy.orm import Session

from .legacy_attest import attestation_from_model_dir, factory_from_attestation
from .oof_generate import code_sha, generate_oof_bundle


def oof_p_from_payload(payload: dict) -> dict[tuple[str, str], float]:
    """{(race_id, horse_id) -> win} from an OOF bundle payload (074)."""
    out: dict[tuple[str, str], float] = {}
    for rid, horses in payload["predictions"].items():
        for hid, pr in horses.items():
            out[(rid, hid)] = float(pr["win"])
    return out


def collect_paired_rows(
    base_factory,
    cand_factory,
    eval_races,
    *,
    first_valid_year: int,
    jump_ids: frozenset[str] = frozenset(),
) -> tuple[list[dict], list[dict]]:
    """Walk-forward per-horse rows for both arms on identical folds.

    Each arm is fit on the fold's outer-train rows and predicts the valid races. Returns
    ``(base_rows, cand_rows)`` where each row is
    ``{race_id, year, race_day, p (calibrated win), odds (closing), won}``.
    """
    base_rows: list[dict] = []
    cand_rows: list[dict] = []
    for fold in expanding_folds(eval_races, first_valid_year):
        train_ctx = [er.context for er in fold.train]
        base_pred = base_factory.fit(train_ctx)
        cand_pred = cand_factory.fit(train_ctx)
        for er in fold.valid:
            rid = er.context.race_id
            if rid in jump_ids:
                continue
            winners = {sl.horse_id for sl in er.labels if sl.win == 1}
            day = er.context.race_date
            year = day.year
            bpred = base_pred.predict_race(er.context)
            cpred = cand_pred.predict_race(er.context)
            for h in er.context.started_horses:
                o = h.result_market.odds
                if o is None or o <= 0:
                    continue
                won = 1 if h.horse_id in winners else 0
                bp = bpred.get(h.horse_id)
                cp = cpred.get(h.horse_id)
                if bp is None or cp is None:
                    continue
                common = {
                    "race_id": rid, "year": year, "race_day": str(day),
                    "odds": float(o), "won": won,
                }
                base_rows.append({**common, "p": float(bp.win)})
                cand_rows.append({**common, "p": float(cp.win)})
    return base_rows, cand_rows


def _jump_ids(session: Session, include_jump: bool) -> frozenset[str]:
    if include_jump:
        return frozenset()
    from sqlalchemy import text
    rows = session.execute(text(
        "SELECT race_id FROM races WHERE track_type='障' OR race_name LIKE '%障害%'"
    ))
    return frozenset(r[0] for r in rows)


def run_ev_weight_gate(
    session: Session,
    *,
    active_dir: Path | str,
    out_root: Path | str,
    bundle_payload: dict | None = None,
    date_from=None,
    date_to=None,
    first_valid_year: int = 2008,
    include_jump: bool = False,
    num_threads: int = 1,
):
    """Orchestrate the single retrospective run. If ``bundle_payload`` is None the frozen OOF
    bundle is generated from ``active_dir`` (long); otherwise the supplied payload is reused."""
    att = attestation_from_model_dir(active_dir, code_sha=code_sha())
    if bundle_payload is None:
        _, bundle_payload = generate_oof_bundle(
            session, active_dir=active_dir, out_root=out_root,
            date_from=date_from, date_to=date_to, first_valid_year=first_valid_year,
            num_threads=num_threads,
        )
    oof_p = oof_p_from_payload(bundle_payload)

    base_factory = factory_from_attestation(session, att)
    cand_factory = factory_from_attestation(session, att)
    cand_factory.recipe = dataclasses.replace(cand_factory.recipe, ev_weight=True)
    cand_factory.oof_p = oof_p

    eval_races = load_eval_races(session, start_date=date_from, end_date=date_to)
    jump = _jump_ids(session, include_jump)
    base_rows, cand_rows = collect_paired_rows(
        base_factory, cand_factory, eval_races,
        first_valid_year=first_valid_year, jump_ids=jump,
    )
    return evaluate_ev_weight_gate(base_rows, cand_rows)
