"""Kill-test: what does the netkeiba cutover's grade encoding cost us?

JRA-VAN put the grade INTO `race_class` (`Ｇ１`/`Ｇ２`/`Ｇ３`). netkeiba splits it: `race_class`
becomes `オープン` and the grade moves to the separate `grade` column, which the feature layer
never reads. So every graded race since the cutover reaches the model as a plain open-class race:

  * `race_class` is a CATEGORICAL model input  -> the string the model sees is simply wrong
  * `class_transition` = class_rank − prev_class_rank -> off by 1..3 ranks, in both directions
    (into a graded race, and out of one)

This is the same shape as the body-weight skew (091): the training data has fifteen years of
correctly-ranked graded races, so the model knows what a G1 looks like — it is the SERVING input
that degraded. The fix direction is therefore "move serving back onto the training distribution",
and the honest way to size it is to replay a fixed model with the input repaired.

No retraining, no netkeiba requests. Arm B rewrites `races.race_class` upstream and rebuilds, so
the real derivation runs rather than a hand-patched output column.

Power warning, stated up front: only 484 races are affected at all. The adoption gate's MDE is
0.0024 over ~26k races, so on this cohort it is ~0.018 — the winner-NLL CI will very likely span
zero even if the defect is real. That is why the PRIMARY read here is how far the predictions
move, not a significance verdict.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pickle
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from horseracing_features.builder import build_feature_matrix
from horseracing_training.cond_logit import race_softmax
from horseracing_training.target_encoding import apply_encoded_columns
from sqlalchemy import text
from sqlalchemy.orm import Session

from horseracing_db.session import create_db_engine

OUT = Path("out/grade-killtest")
MODEL = Path("artifacts/model_versions/lgbm-091-wmask")
CLIP = 1e-6
END = dt.date(2026, 8, 9)

#: JRA-VAN's own spelling, so arm B produces a category the model actually saw in training.
JRAVAN_SPELLING = {"G1": "Ｇ１", "G2": "Ｇ２", "G3": "Ｇ３"}

AFFECTED_SQL = text("""
SELECT race_id, grade FROM races
WHERE grade IN ('G1','G2','G3')
  AND race_class NOT LIKE '%Ｇ%' AND race_class NOT LIKE '%G%'
""")

COHORT_SQL = text("""
WITH graded AS (
  SELECT race_id, race_date FROM races
  WHERE grade IN ('G1','G2','G3')
    AND race_class NOT LIKE '%Ｇ%' AND race_class NOT LIKE '%G%'
),
direct AS (
  SELECT rh.race_id, rh.horse_id, g.race_date
  FROM race_horses rh JOIN graded g USING (race_id) WHERE rh.entry_status = 'started'
),
downstream AS (
  SELECT rh2.race_id FROM direct d
  JOIN race_horses rh2 ON rh2.horse_id = d.horse_id AND rh2.entry_status = 'started'
  JOIN races r2 ON r2.race_id = rh2.race_id
  WHERE r2.race_date > d.race_date
)
SELECT DISTINCT race_id FROM (
  SELECT race_id FROM direct UNION SELECT race_id FROM downstream
) u
""")


def _load_model():
    booster = lgb.Booster(model_file=str(MODEL / "model.txt"))
    prep = pickle.load(open(MODEL / "preprocessor.pkl", "rb"))
    calib = pickle.load(open(MODEL / "calibrator.pkl", "rb"))
    return booster, prep, calib


def _predict(rows: pd.DataFrame, sizes: list[int], booster, prep, calib) -> np.ndarray:
    """raw -> race-softmax -> isotonic -> clip -> renormalise (serving's assemble_predictions)."""
    cols, cats = list(prep["feature_cols"]), list(prep.get("categorical_cols", []))
    encoders = prep.get("encoders", {})
    rows = rows.copy()
    for c in cats:
        if c in rows.columns:
            rows[c] = rows[c].astype("category")
    for c in [c for c in cols if c not in cats and c not in encoders]:
        rows[c] = pd.to_numeric(rows[c], errors="coerce")
    X = rows[cols].copy()
    if encoders:
        X = apply_encoded_columns(X, {c: e.transform(rows[c]) for c, e in encoders.items()}, cols)
    p = np.clip(np.asarray(calib.transform(race_softmax(
        np.asarray(booster.predict(X), dtype=float), sizes)), dtype=float), CLIP, 1 - CLIP)
    idx = np.repeat(np.arange(len(sizes)), sizes)
    return p / np.bincount(idx, weights=p, minlength=len(sizes))[idx]


def _winner_nll(feats, p, winners, days: dict[str, str]) -> dict:
    """Race-level winner NLL, and the per-day values the cluster bootstrap needs."""
    by_day: dict[str, list[float]] = {}
    vals = []
    for rid, g in feats.assign(_p=p).groupby("race_id", sort=True):
        day = days.get(rid)
        w = winners.get(rid)
        if w is None:
            continue
        hit = g.loc[g["horse_id"] == w, "_p"]
        if hit.empty:
            continue
        nll = -float(np.log(max(float(hit.iloc[0]), CLIP)))
        vals.append(nll)
        if day:
            by_day.setdefault(str(day)[:10], []).append(nll)
    return {"mean": float(np.mean(vals)) if vals else None, "n": len(vals), "by_day": by_day}


def _cluster_ci(diff_by_day: dict[str, list[float]], b: int = 2000, seed: int = 20260813):
    days = sorted(diff_by_day)
    if not days:
        return None, None, None
    per = np.array([np.mean(diff_by_day[d]) for d in days])
    wts = np.array([len(diff_by_day[d]) for d in days], dtype=float)
    point = float(np.sum(per * wts) / wts.sum())
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(b):
        pick = rng.integers(0, len(days), len(days))
        boots.append(float(np.sum(per[pick] * wts[pick]) / wts[pick].sum()))
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return point, float(lo), float(hi)


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
    OUT.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine()

    with Session(engine) as s:
        affected = {r.race_id: r.grade for r in s.execute(AFFECTED_SQL)}
        cohort = frozenset(r.race_id for r in s.execute(COHORT_SQL))
        winners = {
            r.race_id: r.horse_id
            for r in s.execute(text(
                "SELECT race_id, horse_id FROM race_results "
                "WHERE finish_order = 1 AND result_status = 'finished'"))
        }
        days = {r.race_id: str(r.race_date) for r in s.execute(
            text("SELECT race_id, race_date FROM races"))}
        cohort = frozenset(r for r in cohort if r in winners)  # settled only
        print(f"affected graded races : {len(affected)}")
        print(f"cohort (settled)      : {len(cohort)} races")

        booster, prep, calib = _load_model()
        results = {}
        for arm in ("A_current", "B_grade_aware"):
            if arm == "B_grade_aware":
                # Rewrite upstream INSIDE the transaction and never commit: the builder reads
                # through this same session, so the real derivation runs on the repaired input
                # (patching the output columns by hand would only test my arithmetic, not the
                # code path). The rollback in the finally below guarantees no residue.
                for rid, g in affected.items():
                    s.execute(
                        text("UPDATE races SET race_class = :rc WHERE race_id = :rid"),
                        {"rc": JRAVAN_SPELLING[g], "rid": rid},
                    )
                s.flush()
                print(f"  arm B: rewrote race_class on {len(affected)} races (uncommitted)")
            feats = build_feature_matrix(s, end_date=END, target_race_ids=cohort)
            feats = feats.sort_values(["race_id", "horse_id"]).reset_index(drop=True)
            sizes = feats.groupby("race_id", sort=True).size().tolist()
            p = _predict(feats, sizes, booster, prep, calib)
            results[arm] = {"feats": feats, "p": p, "nll": _winner_nll(feats, p, winners, days)}
            print(f"  {arm}: winner NLL {results[arm]['nll']['mean']:.6f} "
                  f"over {results[arm]['nll']['n']} races")
        s.rollback()  # the arm-B rewrite must never reach the database

    a, bb = results["A_current"], results["B_grade_aware"]
    assert a["feats"]["race_id"].equals(bb["feats"]["race_id"]), "row alignment broke"

    # PRIMARY read: how far did the predictions actually move?
    d = np.abs(bb["p"] - a["p"])
    moved = d > 1e-12
    report = {
        "cohort_races": len(cohort),
        "affected_graded_races": len(affected),
        "rows": int(len(d)),
        "rows_changed": int(moved.sum()),
        "abs_delta_p": {
            "mean_over_changed": float(d[moved].mean()) if moved.any() else 0.0,
            "p50": float(np.percentile(d[moved], 50)) if moved.any() else 0.0,
            "p95": float(np.percentile(d[moved], 95)) if moved.any() else 0.0,
            "max": float(d.max()),
        },
        "winner_nll": {
            "A_current": a["nll"]["mean"],
            "B_grade_aware": bb["nll"]["mean"],
            "n_races": a["nll"]["n"],
        },
    }
    diff_by_day: dict[str, list[float]] = {}
    for day in a["nll"]["by_day"]:
        av, bv = a["nll"]["by_day"][day], bb["nll"]["by_day"].get(day, [])
        if len(av) == len(bv):
            diff_by_day[day] = [y - x for x, y in zip(av, bv, strict=True)]
    pt, lo, hi = _cluster_ci(diff_by_day)
    report["winner_nll"]["diff_B_minus_A"] = pt
    report["winner_nll"]["ci"] = [lo, hi]
    report["winner_nll"]["n_days"] = len(diff_by_day)
    report["note"] = (
        "PRIMARY read is abs_delta_p: with 484 affected races the winner-NLL CI is underpowered "
        "by construction (MDE ~0.018 vs the gate's 0.0024 at 26k races), so a CI spanning zero "
        "here is NOT evidence the defect is harmless."
    )
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
