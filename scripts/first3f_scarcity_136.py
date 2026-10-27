"""136 research C: does training under the 2026 first-3F supply also help serving under it?

The question
------------
Three columns (``asof_rel_first3f_avg`` / ``asof_rel_first3f_best`` / ``asof_pace_balance_avg``)
are missing for ~11% of rows in 2007-2024 but **41.7% in 2026**, because netkeiba does not
publish a per-horse first 3F and only pre-cutover history carries one. The booster therefore
learned its NaN branch at ~11% frequency and is asked to use it at ~42%.

Feature 091 fixed the same shape of problem for body weight by masking the columns during
training so the fitted model sees the serving distribution. It bought -0.0106. This asks whether
the same move helps here.

Why the mask is per ROW, not per race
-------------------------------------
091's weight mask is race-atomic because weights are published (or not) for a whole race. First-3F
missingness is not like that: it depends on whether that HORSE's own past races recorded one. The
frozen snapshot shows the race-level all-missing rate is flat at 7-9% across every year (those are
debut races) while the row rate jumps 11% -> 41.7% in 2026. So the 2026 jump is within-race partial
missingness, and a race-atomic mask would simulate a supply failure that does not happen.

Design
------
Two arms, both SCORED under the scarce regime, on the same folds 130 used:

  control   : trained on the matrix as-is, predicted on the masked matrix  (today's skew)
  candidate : trained AND predicted on the masked matrix                   (091's move)

The scarce regime is produced by raising every year's missing rate for those three columns to the
2026 observed rate, masking only rows that currently have values, deterministically. Years already
at or above the target are left alone.

What this cannot claim
----------------------
The mask matches the RATE, not the PATTERN: real 2026 missingness is concentrated on horses whose
racing history is post-cutover, and a pseudo-random draw breaks that correlation. And both years
are development period. This is a breakpoint measurement, not a verdict.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import accuracy_batch_common as common  # noqa: E402
from horseracing_eval.dataset import population_masks  # noqa: E402
from horseracing_eval.splits import expanding_folds  # noqa: E402
from horseracing_training.calib_split import OofCalibratedPredictor  # noqa: E402
from horseracing_training.recipe import ModelRecipe  # noqa: E402

OUT = ROOT / "artifacts/136-first3f-scarcity"
COLS = ("asof_rel_first3f_avg", "asof_rel_first3f_best", "asof_pace_balance_avg")
#: 2026 observed row-level missing rate in the frozen snapshot (research-c/first3f-missingness).
TARGET_RATE = 0.417283
MASK_SEED = 20260922
FOLD_FIRST_VALID_YEAR = 2020
SEED = 42
SEEDS = (42, 43, 44)
YEARS = (2020, 2021, 2022, 2023, 2024, 2025, 2026)
#: specs/136-eight-model-candidate/research-c-extension-preregistration.json
PREREG_SHA256 = "50f06f57ecc557c6f93b99fd8f9dde5c508982400ab7751ceb3b5ee41f21cfbc"
ROUNDS = 900
#: The production joint member's recipe, read off the shipped artefact so it cannot drift.
JOINT_DROPS = tuple(json.loads(
    (ROOT / "artifacts/129-candidate-mixture-serving/members/joint-42/metadata.json").read_text()
)["recipe"]["drop_features"])


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def recipe(smoke=False, seed=SEED) -> ModelRecipe:
    return ModelRecipe(
        objective="pl_topk", calibration="none", calib_frac=0.0, seed=seed,
        drop_features=list(JOINT_DROPS),
        params=(("n_estimators", 5 if smoke else ROUNDS),),
        weight_mask_rate=0.5, weight_mask_seed=20260810,
        target_encode_cols=("jockey_id", "trainer_id"), te_smoothing=10.0,
    )


def _row_draw(race_ids, horse_ids) -> np.ndarray:
    """Deterministic uniform draw per (race_id, horse_id).

    Not Python's ``hash``: it is salted per process by PYTHONHASHSEED, so the same row would be
    masked in one run and not the next, and no receipt could reproduce the fit.
    """
    out = np.empty(len(race_ids), dtype=np.float64)
    for i, (rid, hid) in enumerate(zip(race_ids, horse_ids, strict=True)):
        h = hashlib.sha256(f"{MASK_SEED}|{rid}|{hid}".encode()).digest()
        out[i] = int.from_bytes(h[:8], "big") / float(1 << 64)
    return out


def apply_scarcity(frame, target_rate=TARGET_RATE):
    """Raise each year's missing rate for the three columns to ``target_rate``.

    Only rows that currently HAVE values are eligible; a year already at or above the target is
    left untouched. The three columns share one draw per row, because in reality they go missing
    together (they all derive from the same absent first-3F).
    """
    out = frame.copy()
    present = out[list(COLS)].notna().all(axis=1).to_numpy()
    if not present.any():
        raise ValueError("no rows carry the first-3F block; nothing to mask")
    years = out.race_date.astype("datetime64[ns]").dt.year.to_numpy()
    draw = _row_draw(out.race_id.tolist(), out.horse_id.tolist())
    mask = np.zeros(len(out), dtype=bool)
    report = {}
    for year in np.unique(years):
        rows = years == year
        current = 1.0 - present[rows].mean()
        need = 0.0 if current >= target_rate else (target_rate - current) / (1.0 - current)
        chosen = rows & present & (draw < need)
        mask |= chosen
        report[int(year)] = {
            "n_rows": int(rows.sum()), "rate_before": round(float(current), 6),
            "extra_mask_probability": round(float(need), 6),
            "n_masked": int(chosen.sum()),
            "rate_after": round(float(1.0 - present[rows & ~chosen].sum() / rows.sum()), 6),
        }
    out.loc[mask, list(COLS)] = np.nan
    return out, {"target_rate": target_rate, "mask_seed": MASK_SEED,
                 "columns": list(COLS), "n_masked_rows": int(mask.sum()),
                 "unit": "row (per race_id+horse_id), NOT race-atomic", "by_year": report}


def _predict(predictor, valid):
    return {er.context.race_id: {h: (float(v.win), float(v.top2), float(v.top3))
                                 for h, v in predictor.predict_race(er.context).items()}
            for er in valid}


def winner_nll(valid, predictions):
    rows = []
    for er in valid:
        pop = population_masks(er)
        if not pop.eligible:
            continue
        ids = [h.horse_id for h in er.context.started_horses]
        p = predictions[er.context.race_id]
        rows.append((er.context.race_id, str(er.context.race_date),
                     -float(np.log(p[pop.winner_horse_id][0])), ids.index(pop.winner_horse_id)))
    return rows


def run(year, smoke, out_path, seed=SEED):
    inputs = common.load_frozen_inputs()
    matrix, races = inputs.matrix, inputs.races
    masked_frame, mask_report = apply_scarcity(matrix.frame)
    masked_matrix = replace(matrix, frame=masked_frame)

    folds = {f.valid_year: f for f in expanding_folds(races, FOLD_FIRST_VALID_YEAR)}
    fold = folds[year]
    train, valid = list(fold.train), list(fold.valid)
    if smoke:
        train = [r for r in train if r.context.race_date.year <= 2008]
        valid = valid[:24]

    results = {}
    for arm, fit_matrix in (("control_trained_unmasked", matrix),
                            ("candidate_trained_masked", masked_matrix)):
        predictor = OofCalibratedPredictor(
            None, recipe(smoke, seed), shared_data=fit_matrix,
            n_oof_blocks=2 if smoke else 8, method="isotonic", require_sufficient=not smoke)
        t0 = time.monotonic()
        with common_frozen_outcomes(train):
            predictor.fit([r.context for r in train], num_threads=1)
        elapsed = time.monotonic() - t0
        # BOTH arms are scored under the scarce regime: swap the matrix in after fitting. The
        # data actually used at predict time lives on ``_base`` and is COLUMN-SCOPED, so it must
        # be re-scoped rather than assigned raw -- feature 097 found that assigning an unscoped
        # matrix silently restores dropped columns and makes two arms identical.
        # For the candidate this swap is a no-op (it already fitted on this matrix), which is
        # precisely the property being tested: train and predict see the same distribution.
        base = predictor._base
        original_shared, original_data = predictor._shared, base._data
        by_regime = {}
        # PRIMARY = scarce (what a served race actually meets from 2026 on).
        # GUARD   = natural (untouched rows; plentiful for 2020-2025, scarce for 2026).
        for regime, regime_matrix in (("scarce", masked_matrix), ("natural", matrix)):
            predictor._shared = regime_matrix
            base._data = base._scope_columns(regime_matrix)
            if base._data.feature_cols != original_data.feature_cols:
                raise ValueError("re-scoping changed the fitted column set")
            try:
                by_regime[regime] = winner_nll(valid, _predict(predictor, valid))
            finally:
                predictor._shared, base._data = original_shared, original_data
        results[arm] = {"fit_seconds": elapsed, "regimes": by_regime, "n_valid": len(valid)}

    per_regime = {}
    n_differ_primary = 0
    for regime in ("scarce", "natural"):
        a = {r[0]: r[2] for r in results["control_trained_unmasked"]["regimes"][regime]}
        b = {r[0]: r[2] for r in results["candidate_trained_masked"]["regimes"][regime]}
        if set(a) != set(b):
            raise ValueError("arms scored different races")
        days = {r[0]: r[1] for r in results["control_trained_unmasked"]["regimes"][regime]}
        per_regime[regime] = {
            "control_winner_nll": float(np.mean(list(a.values()))),
            "candidate_winner_nll": float(np.mean(list(b.values()))),
            "winner_nll_diff": float(np.mean([b[k] - a[k] for k in a])),
            "n_races_where_arms_differ": sum(1 for k in a if a[k] != b[k]),
            "per_race": [{"race_id": k, "race_day": days[k], "diff": b[k] - a[k]}
                         for k in sorted(a)],
        }
        if regime == "scarce":
            n_differ_primary = per_regime[regime]["n_races_where_arms_differ"]
    a = {r[0]: r[2] for r in results["control_trained_unmasked"]["regimes"]["scarce"]}
    b = {r[0]: r[2] for r in results["candidate_trained_masked"]["regimes"]["scarce"]}
    # 097-style structural abort: if the training-side mask never reached the booster, the two
    # arms are the same model and the "effect" would just be one number wearing two labels.
    # Smoke is exempt: a 5-round booster can legitimately never split on the three columns.
    n_differ = n_differ_primary
    if not smoke and n_differ == 0:
        raise ValueError(
            "control and candidate produced identical losses on every race: the training-side "
            "mask did not reach the model, so this measurement is void")

    # The two evaluation regimes must relate to the mask the way the mask report says they do.
    # Measured on the 5-round smoke: its booster puts 0 of 150 splits on the first-3F block, so
    # masking those columns at predict time cannot move it and the regimes ARE identical -- which
    # is why smoke is exempt here rather than being a loophole.
    # in-memory the report is keyed by int; JSON turns them into strings on save
    masked_validation = mask_report["by_year"][int(year)]["n_masked"] > 0
    regimes_identical = all(
        x["diff"] == y["diff"] for x, y in
        zip(per_regime["scarce"]["per_race"], per_regime["natural"]["per_race"], strict=True))
    if not smoke:
        if masked_validation and regimes_identical:
            raise ValueError(
                f"{year}: validation rows were masked but the scarce and natural regimes scored "
                "identically -- the predict-side swap did not reach the model")
        if not masked_validation and not regimes_identical:
            raise ValueError(
                f"{year}: no validation row was masked, so the two regimes must be the same "
                "measurement; they differ, which means something other than the mask moved")
    # The top-level scalars mirror the PRIMARY regime; both regimes are kept under `regimes`.
    diffs = [(r["race_id"], r["race_day"], r["diff"]) for r in per_regime["scarce"]["per_race"]]
    point = per_regime["scarce"]["winner_nll_diff"]
    doc = {
        "artifact_kind": "first3f_scarcity_breakpoint", "schema_version": 1,
        "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "year": year, "smoke": smoke, "seed": seed,
        "primary_regime": "scarce", "guard_regime": "natural",
        "preregistration_sha256": PREREG_SHA256, "regimes": per_regime,
        "orientation": "candidate - control (negative = training under scarcity helps)",
        "mask": mask_report,
        "n_eligible_races": len(diffs), "n_races_where_arms_differ": n_differ,
        "winner_nll_diff": point,
        "control_winner_nll": float(np.mean(list(a.values()))),
        "candidate_winner_nll": float(np.mean(list(b.values()))),
        "fit_seconds": {k: v["fit_seconds"] for k, v in results.items()},
        "per_race": [{"race_id": r, "race_day": d, "diff": v} for r, d, v in diffs],
        "provenance": {"seeds_registered": list(SEEDS), "years_registered": list(YEARS),
                       "snapshot_sha256": inputs.provenance["snapshot_sha256"],
                       "recipe": json.loads(json.dumps(recipe(smoke, seed).meta(), default=str)),
                       "driver_sha256": digest(Path(__file__))},
        "can_adopt": False, "eligible_for_verdict": False,
        "limits": ("Rate matched, pattern not: real 2026 missingness concentrates on horses whose "
                   "history is post-cutover. Development period. Breakpoint, not a verdict."),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("x") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True, default=str, allow_nan=False)
        fh.write("\n")
    print(f"{year} s{seed} smoke={smoke} scarce={point:+.9f} "
          f"natural={per_regime['natural']['winner_nll_diff']:+.9f} "
          f"n={len(diffs)}", flush=True)
    return doc


def common_frozen_outcomes(train):
    from contextlib import contextmanager
    from unittest.mock import patch

    outcomes = {r.context.race_id: (r.n_result_rows, {x.horse_id for x in r.labels if x.win == 1})
                for r in train}

    def lookup(session, ids):
        if session is not None or any(rid not in outcomes for rid in ids):
            raise ValueError("OOF outcome request is outside frozen inputs")
        return {rid: outcomes[rid] for rid in ids}

    @contextmanager
    def ctx():
        with patch("horseracing_training.calib_split._started_all_outcomes", side_effect=lookup):
            yield

    return ctx()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["audit", "fit"])
    ap.add_argument("--year", type=int)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()
    if a.command == "audit":
        inputs = common.load_frozen_inputs()
        _, report = apply_scarcity(inputs.matrix.frame)
        print(json.dumps(report, indent=1))
        return
    if a.year is None:
        ap.error("fit requires --year")
    if a.seed not in SEEDS:
        ap.error(f"seed must be one of the registered {SEEDS}")
    tag = "smoke" if a.smoke else "full"
    run(a.year, a.smoke, OUT / f"{tag}-{a.year}-s{a.seed}.json", seed=a.seed)


if __name__ == "__main__":
    main()
