"""T042/T043: end-to-end wiring smoke — run BEFORE committing hours to a full fit.

The dangerous failure is not a crash. It is a green unit-test suite over a model whose actual
inputs never contained `prev_weight`, or whose mask never reached the matrix. Both produce a
perfectly normal-looking multi-hour run that measures nothing.

Everything here inspects the TRANSFORMED MATRIX, never call counts.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from horseracing_db.session import create_db_engine
from horseracing_features.registry import FEATURE_VERSION, model_input_features
from horseracing_features.weight_mask import WEIGHT_MASK_COLUMNS, MaskSpec, apply_weight_mask
from horseracing_training.dataset import build_training_matrix
from horseracing_training.recipe import ModelRecipe

OUT = Path(__file__).parent / "wiring_smoke.json"
GATE = json.loads((Path(__file__).parent.parent / "gate-config.json").read_text())


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
    )
    wm = GATE["weight_mask"]
    spec = MaskSpec(rate=wm["rate"], seed=wm["seed"], unit=wm["unit"])
    recipe = ModelRecipe(weight_mask_rate=wm["rate"], weight_mask_seed=wm["seed"])
    checks: dict = {"feature_version": FEATURE_VERSION, "recipe_hash": recipe.recipe_hash()}
    failures: list[str] = []

    def check(name: str, ok: bool, detail=None):
        checks[name] = {"pass": bool(ok), "detail": detail}
        if not ok:
            failures.append(name)

    # 1. the column is in the model's declared inputs, exactly once
    cols = model_input_features()
    check("prev_weight_in_model_inputs_exactly_once", cols.count("prev_weight") == 1,
          {"n_model_inputs": len(cols)})
    check("mask_columns_are_model_inputs", all(c in cols for c in WEIGHT_MASK_COLUMNS),
          {"columns": list(WEIGHT_MASK_COLUMNS)})
    # research D1: freshness/availability are supplied by these, so they must still be inputs
    check("d1_substitute_columns_present",
          "days_since_last" in cols and "has_past_race" in cols)

    with Session(create_db_engine()) as session:
        data = build_training_matrix(session, use_materialized=False)
    df = data.frame
    check("prev_weight_in_training_matrix", "prev_weight" in df.columns)

    w = pd.to_numeric(df["weight"], errors="coerce")
    pw = pd.to_numeric(df["prev_weight"], errors="coerce")

    # 3. the mask actually lands on the matrix, race-atomically
    masked = apply_weight_mask(df, spec=spec)

    # 2. AFTER masking, rows must exist where the same-day weight is gone but the proxy remains —
    #    the serving situation. Measured on the MASKED matrix on purpose: in the raw training
    #    population this combination essentially never occurs (1 row in 956k, and that horse was
    #    on debut so it has no proxy either). That absence is not a defect, it IS the train/serve
    #    skew — and it is why the mask is the mechanism rather than a refinement: without it the
    #    model would never once see the input pattern it must handle at serve time.
    mw = pd.to_numeric(masked["weight"], errors="coerce")
    mpw = pd.to_numeric(masked["prev_weight"], errors="coerce")
    n_fallback = int((mw.isna() & mpw.notna()).sum())
    check("masked_rows_with_missing_weight_but_present_prev_weight", n_fallback > 0,
          {"n_rows": n_fallback,
           "n_rows_before_masking": int((w.isna() & pw.notna()).sum())})
    changed = masked["weight"].isna() & w.notna()
    masked_races = set(df.loc[changed, "race_id"].unique())
    all_races = df["race_id"].nunique()
    check("mask_selected_a_plausible_fraction_of_races",
          0.3 * all_races < len(masked_races) < 0.7 * all_races,
          {"masked_races": len(masked_races), "all_races": all_races, "rate": wm["rate"]})

    sub = masked[masked["race_id"].isin(masked_races)]
    for col in WEIGHT_MASK_COLUMNS:
        check(f"masked_races_have_nan_{col}", bool(pd.to_numeric(sub[col], errors="coerce").isna().all()))
    check("masked_races_keep_prev_weight",
          bool(pd.to_numeric(sub["prev_weight"], errors="coerce").notna().any()))

    # 4. carried_weight_ratio must NOT be recomputed from weight downstream (which would silently
    #    resurrect the value the mask just removed)
    unmasked_ratio = pd.to_numeric(df.loc[sub.index, "carried_weight_ratio"], errors="coerce")
    check("carried_weight_ratio_not_resurrected",
          bool(pd.to_numeric(sub["carried_weight_ratio"], errors="coerce").isna().all()),
          {"had_values_before_mask": int(unmasked_ratio.notna().sum())})

    # 5. race-atomicity: no race may be partially masked
    per_race = masked.groupby("race_id")["weight"].apply(lambda s: s.isna().nunique())
    check("no_race_is_partially_masked", bool((per_race == 1).all()),
          {"mixed_races": int((per_race > 1).sum())})

    # 6. provenance: an explicit rate is a distinct model identity from "no masking configured"
    check("recipe_hash_differs_from_unmasked",
          recipe.recipe_hash() != ModelRecipe().recipe_hash())
    check("mask_spec_hash_recorded",
          recipe.weight_mask_spec() is not None,
          {"spec": str(recipe.weight_mask_spec())})

    # --- T043 diagnostics (not pass/fail): does prev_weight carry signal at all? ---
    both = w.notna() & pw.notna()
    checks["diagnostics"] = {
        "prev_weight_coverage": float(pw.notna().mean()),
        "corr_weight_vs_prev_weight": float(np.corrcoef(w[both], pw[both])[0, 1]),
        "median_abs_kg_diff": float((w[both] - pw[both]).abs().median()),
        "raw_rows_with_missing_weight": int(w.isna().sum()),
        "note": "high correlation is expected and is exactly why masking is required: without it "
                "the tree splits on `weight` and never learns to use the proxy. Note the raw "
                "training population has ~0 rows with a missing same-day weight - the fallback "
                "pattern exists ONLY because the mask creates it.",
    }

    checks["failures"] = failures
    checks["pass"] = not failures
    OUT.write_text(json.dumps(checks, ensure_ascii=False, indent=2, default=float))
    for k, v in checks.items():
        if isinstance(v, dict) and "pass" in v:
            print(f"  {'PASS' if v['pass'] else 'FAIL'}  {k}  {v['detail'] or ''}")
    print(f"\nwiring smoke: {'PASS' if not failures else 'FAIL ' + str(failures)} -> {OUT.name}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
