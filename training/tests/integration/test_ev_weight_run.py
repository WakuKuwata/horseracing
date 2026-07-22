"""Feature 079 (step 5): paired-gate row collection + gate wiring (injected fast factories).

Exercises the two-arm walk-forward collection end-to-end on a synth DB without the heavy legacy
attestation / OOF-bundle generation: a plain unweighted RecipeFactory (baseline) vs an
ev_weight RecipeFactory fed a synthetic frozen OOF-p (candidate). Proves both arms produce
row-aligned OOS rows across folds and the pure gate returns a well-formed verdict.
"""

from __future__ import annotations

import pytest
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.ev_weight_gate import evaluate_ev_weight_gate

from horseracing_training.ev_weight_run import collect_paired_rows, oof_p_from_payload
from horseracing_training.recipe import ModelRecipe, RecipeFactory
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration


def test_collect_paired_rows_and_gate(session):
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=8, field_size=6)
    races = load_eval_races(session)

    # synthetic frozen OOF p, varied by race so EV-weights are non-neutral (exercises the
    # candidate's weighted fit through the fold loop)
    oof_p: dict = {}
    for k, er in enumerate(races):
        hs = er.context.started_horses
        p_top = 0.2 + 0.5 * (k / max(len(races) - 1, 1))
        rest = (1.0 - p_top) / (len(hs) - 1)
        for j, h in enumerate(hs):
            oof_p[(er.context.race_id, h.horse_id)] = p_top if j == 0 else rest

    # fast, robust recipe for tiny fold data (matches the proven 074 OOF integration setup); this
    # test proves COLLECTION + GATE wiring across two arms/folds — the pl_topk weighted fit itself
    # is exercised in test_ev_weight_predictor. ev_weight works on the binary path too (step 1).
    fast = dict(objective="binary", calibration="none", target_encode_cols=())
    base = RecipeFactory(session, ModelRecipe(**fast))
    cand = RecipeFactory(session, ModelRecipe(**fast, ev_weight=True), oof_p=oof_p)

    base_rows, cand_rows = collect_paired_rows(base, cand, races, first_valid_year=2008)

    assert len(base_rows) == len(cand_rows) > 0
    assert {"race_id", "year", "race_day", "p", "odds", "won"} <= set(base_rows[0])
    # both arms saw the SAME valid races (paired)
    assert [r["race_id"] for r in base_rows] == [r["race_id"] for r in cand_rows]
    # OOS only: 2008 + 2009 valid folds (2007 is the first train-only year)
    assert {r["year"] for r in base_rows} == {2008, 2009}

    rep = evaluate_ev_weight_gate(base_rows, cand_rows, b=100, seed=1)
    assert rep.verdict in {"ADOPT", "REJECT", "NO_DECISION"}
    assert rep.n_races > 0


def test_oof_p_from_payload_roundtrip():
    payload = {"predictions": {
        "R1": {"H1": {"win": 0.6, "top2": 0.8, "top3": 0.9},
               "H2": {"win": 0.4, "top2": 0.7, "top3": 0.85}},
    }}
    d = oof_p_from_payload(payload)
    assert d[("R1", "H1")] == 0.6 and d[("R1", "H2")] == 0.4
