"""Robustness checks for the body-weight kill-test.

1. Uncalibrated (race-softmax only) winner NLL — the isotonic map was fit on arm-A-like
   scores, so confirm the ranking is not an artefact of the calibrator.
2. Per-year breakdown (is it stable over time, incl. the recent regime).
3. Subgroup by prev_weight availability (debut / no previous weight).
4. Layoff bands — a horse off for a year may have changed a lot.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.metrics import winner_nll
from horseracing_training.cond_logit import race_softmax

sys.path.insert(0, str(Path(__file__).parent))
from killtest import CLIP, load_model, make_X, patch  # noqa: E402

SCRATCH = Path(__file__).parent
ARMS = ["A_actual", "B_nan", "C_prev", "D_nodiff"]


def predict_both(rows, group_sizes, booster, prep, calibrator):
    """Return (calibrated_win, uncalibrated_win). Uncalibrated = race-softmax, already Σ=1."""
    X = make_X(rows, prep)
    raw = np.asarray(booster.predict(X), dtype=float)
    soft = race_softmax(raw, group_sizes)
    cal = np.clip(np.asarray(calibrator.transform(soft), dtype=float), CLIP, 1.0 - CLIP)
    idx = np.repeat(np.arange(len(group_sizes)), group_sizes)
    sums = np.bincount(idx, weights=cal, minlength=len(group_sizes))
    return cal / sums[idx], soft


def ci_str(r):
    if r["ci_low"] is None:
        return "NO_DECISION"
    sig = "SIGNIF" if (r["ci_high"] < 0 or r["ci_low"] > 0) else "ns"
    return f"{r['diff']:+.6f} CI=[{r['ci_low']:+.6f},{r['ci_high']:+.6f}] n={r['n_races']} {sig}"


def main() -> int:
    t0 = time.time()
    feats = pd.read_parquet(SCRATCH / "feats.parquet")
    meta = pd.read_parquet(SCRATCH / "meta.parquet")
    winners = pd.read_parquet(SCRATCH / "winners.parquet")
    prev = pd.read_parquet(SCRATCH / "prev_weight.parquet")

    prev = prev.merge(meta[["race_id", "race_date"]], on="race_id", how="left")
    same_day = prev["prev_weight_date"].notna() & (
        pd.to_datetime(prev["prev_weight_date"]) >= pd.to_datetime(prev["race_date"])
    )
    prev.loc[same_day, ["prev_weight", "prev_weight_date"]] = None
    feats = feats.merge(prev[["race_id", "horse_id", "prev_weight", "prev_weight_date"]],
                        on=["race_id", "horse_id"], how="left")
    feats = feats.merge(meta[["race_id", "race_date"]], on="race_id", how="left")

    wcount = winners.groupby("race_id").size()
    eligible = set(wcount[wcount == 1].index)
    winner_of = dict(winners[winners["race_id"].isin(eligible)][["race_id", "horse_id"]].values)
    feats = feats[feats["race_id"].isin(eligible)].copy()
    feats = feats.sort_values(["race_id", "horse_id"], kind="mergesort").reset_index(drop=True)

    race_ids = feats["race_id"].to_numpy()
    group_sizes = feats.groupby("race_id", sort=False).size().tolist()
    ordered = feats["race_id"].drop_duplicates().tolist()
    rdate = dict(feats.groupby("race_id")["race_date"].first().items())
    is_winner = np.array([winner_of[r] == h
                          for r, h in zip(race_ids, feats["horse_id"].to_numpy())])

    booster, prep, calibrator, mmeta = load_model()
    cal_res, unc_res = {}, {}
    for arm in ARMS:
        c, u = predict_both(patch(feats, arm), group_sizes, booster, prep, calibrator)
        cal_res[arm], unc_res[arm] = c, u

    widx = np.where(is_winner)[0]
    wrace = race_ids[widx]
    per = {k: {a: dict(zip(wrace, v[a][widx])) for a in ARMS}
           for k, v in [("cal", cal_res), ("unc", unc_res)]}

    def paired(space, a, b, subset=None):
        by_day: dict[str, list[float]] = {}
        for r in ordered:
            if subset is not None and r not in subset:
                continue
            pa = np.clip(per[space][a][r], 1e-15, 1 - 1e-15)
            pb = np.clip(per[space][b][r], 1e-15, 1 - 1e-15)
            by_day.setdefault(str(rdate[r]), []).append(float(-np.log(pa) + np.log(pb)))
        if not by_day:
            return {"diff": float("nan"), "ci_low": None, "ci_high": None, "n_races": 0}
        ci = race_day_cluster_bootstrap_ci_v1(by_day)
        return {"diff": ci.point, "ci_low": ci.ci_low, "ci_high": ci.ci_high,
                "n_races": sum(len(v) for v in by_day.values())}

    print("=== 1. CALIBRATED vs UNCALIBRATED (race-softmax only) ===")
    for space, label in [("cal", "calibrated (production)"), ("unc", "uncalibrated")]:
        nlls = {a: winner_nll([per[space][a][r] for r in ordered])[0] for a in ARMS}
        print(f"  [{label}] " + "  ".join(f"{a}={nlls[a]:.6f}" for a in ARMS))
        print(f"      C−B: {ci_str(paired(space, 'C_prev', 'B_nan'))}")
        print(f"      B−D: {ci_str(paired(space, 'B_nan', 'D_nodiff'))}")

    print("\n=== 2. per-year (calibrated, C−B) ===")
    years = sorted({str(rdate[r])[:4] for r in ordered})
    for y in years:
        sub = {r for r in ordered if str(rdate[r]).startswith(y)}
        print(f"  {y}: {ci_str(paired('cal', 'C_prev', 'B_nan', sub))}")

    print("\n=== 3. by prev_weight availability ===")
    cov = feats.groupby("race_id")["prev_weight"].apply(lambda s: s.notna().mean())
    full = set(cov[cov == 1.0].index)
    partial = set(cov[(cov > 0) & (cov < 1.0)].index)
    none_ = set(cov[cov == 0].index)
    for name, sub in [("full coverage", full), ("partial", partial), ("no prev weight", none_)]:
        if not sub:
            print(f"  {name}: n=0")
            continue
        print(f"  {name:16s} C−B: {ci_str(paired('cal', 'C_prev', 'B_nan', sub))}")

    print("\n=== 4. layoff bands (max days_since_last in race, calibrated C−B) ===")
    lay = pd.to_numeric(feats["days_since_last"], errors="coerce")
    feats["_lay"] = lay
    mx = feats.groupby("race_id")["_lay"].max()
    bands = [("<=60d", mx <= 60), ("61-180d", (mx > 60) & (mx <= 180)),
             ("181-365d", (mx > 180) & (mx <= 365)), (">365d", mx > 365)]
    for name, m in bands:
        sub = set(mx[m].index)
        if len(sub) < 30:
            print(f"  {name:10s} n={len(sub)} (too small)")
            continue
        print(f"  {name:10s} C−B: {ci_str(paired('cal', 'C_prev', 'B_nan', sub))}")

    print(f"\ndone in {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
