"""ΔR² orchestration: join an OOF bundle to the market and score Benter's increment.

The pure statistics live in ``horseracing_eval.delta_r2``. This module only supplies them with a
population: OOF model probabilities from a content-addressed bundle, market vote shares built on
the COMPLETE started field, the single winner, and the race-day used for both the prequential cut
and the cluster bootstrap.

Population and eligibility are deliberately NOT reinvented here — ``assemble_inputs`` (082) already
settles them (single winner, no partial ingest, p renormalised over the started field, and `q`
present only when EVERY started horse has odds). Reusing it keeps ΔR² on the same races as the
segment-accuracy instrument, so the two readouts are comparable.

Reported, never gated on: this is an evidence instrument. See the module docstring of
``horseracing_eval.delta_r2`` and ``docs/plan/accuracy-roi-decoupling-investigation.md``.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from horseracing_eval.delta_r2 import DeltaR2Race, evaluate_delta_r2
from horseracing_eval.hashing import race_set_hash, stable_hash
from sqlalchemy.orm import Session

from .oof_generate import code_sha
from .segment_accuracy_run import assemble_inputs, resolve_active

#: Prequential block = calendar year. Yearly is the finest cut the OOF bundle itself supports
#: (its folds are annual), and it leaves each block large enough to fit two coefficients.
BLOCK_GRAIN = "year"

DELTA_R2_CONTRACT_VERSION = "delta-r2-v1"


class DeltaR2Error(RuntimeError):
    """Refuse to emit a readout rather than emit a misleading one."""


def build_delta_r2_races(inputs) -> tuple[list[DeltaR2Race], dict[str, int]]:
    """RaceInput (082) -> DeltaR2Race, dropping market-incomplete races with a counted reason."""
    races: list[DeltaR2Race] = []
    excl = {"market_incomplete": 0}
    for r in inputs:
        if r.q is None:
            excl["market_incomplete"] += 1
            continue
        races.append(DeltaR2Race(
            race_id=r.race_id, day=r.day, block=r.day[:4],
            winner_idx=r.winner_idx, p=r.p, q=r.q,
        ))
    return races, excl


def run_delta_r2(
    session: Session,
    *,
    bundle_path: Path,
    eval_from: datetime.date,
    eval_to: datetime.date,
    seed: int,
    bootstrap_b: int,
    delta_min: float = 0.0,
) -> dict[str, Any]:
    bundle = json.loads(Path(bundle_path).read_text())
    model_version, active_dir = resolve_active(session)
    metadata = json.loads((active_dir / "metadata.json").read_text())

    inputs, excl = assemble_inputs(session, bundle, eval_from=eval_from, eval_to=eval_to)
    races, excl2 = build_delta_r2_races(inputs)
    excl = {**excl, **excl2}
    if not races:
        raise DeltaR2Error("no market-complete eligible races — refusing to emit a readout")

    report = evaluate_delta_r2(races, b=bootstrap_b, seed=seed, delta_min=delta_min)

    return {
        "instrument_contract": {
            "kind": "delta_r2",
            "secondary": True,
            "can_adopt": False,
            "estimand": "prequential OOS pseudo-R² increment of a two-stage model over the "
                        "market alone (Benter 1994), on closing-leaning market odds",
            "primary_metric": "delta_r2_model_given_market",
            "note": "the LITERAL increment (vs raw q) also credits the market's own power "
                    "recalibration to the model; the conditional one does not",
            "gate_note": "evidence instrument — winner NLL / calibration / top2-top3 remain the "
                         "adoption gate. ΔR² answers whether a change can move ROI at all, "
                         "not whether it improves the product.",
            "known_confounds": [
                "q is closing-leaning (race_horses.odds), not a decision-time snapshot — this is "
                "the HARDEST market benchmark, and not the price a bettor could act on",
                "coefficients are fit prequentially on data already used for other analysis; "
                "a confirmatory reading needs a fixed forward holdout",
            ],
        },
        "provenance": {
            "base_model_version": model_version,
            "feature_version": metadata.get("feature_version"),
            "bundle_digest": bundle.get("bundle_digest"),
            "attestation_digest": bundle.get("attestation_digest"),
            "prediction_checksum": bundle.get("prediction_checksum"),
            "oof_race_set_hash": bundle.get("oof_race_set_hash"),
            "scored_race_set_hash": race_set_hash([r.race_id for r in races]),
            "market_snapshot_hash": stable_hash(
                sorted((r.race_id, tuple(round(float(x), 12) for x in r.q)) for r in races)
            ),
            "probability_stage": "model-internal calibrated win prob (pre-two-gamma), "
                                 "renormalised over the started field",
            "market_definition": "q_i = (1/O_i) / Σ_j (1/O_j) over the COMPLETE started field",
            "block_grain": BLOCK_GRAIN,
            "eval_window": [eval_from.isoformat(), eval_to.isoformat()],
            "contract_version": DELTA_R2_CONTRACT_VERSION,
            "code_sha": code_sha(),
            "seed": seed,
            "bootstrap_b": bootstrap_b,
            "delta_min": delta_min,
        },
        "exclusions": excl,
        "result": report.to_dict(),
    }
