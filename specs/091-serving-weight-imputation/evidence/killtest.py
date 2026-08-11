"""Body-weight kill-test: does imputing a missing body weight with the previous race's
weight beat the current production behaviour (NaN)?

Fixed model (no retraining) — only the SERVING INPUT is perturbed, which is exactly the
train/serve skew we are measuring.

Arms
  A actual   : real weight + real weight_diff          (upper bound: weight was known)
  B nan      : weight=NaN, weight_diff=NaN             (current production at serve time)
  C prev     : weight=prev race weight, diff=NaN       (the proposal)
  D noDiff   : real weight, weight_diff=NaN            (isolates weight_diff; C's ceiling)

carried_weight_ratio (= 斤量 / body weight) is the only derived column that depends on body
weight, and is patched consistently per arm. carried_weight / _rel / _change are impost-only
and stay untouched.

PRIMARY: race-level winner NLL, paired diffs, race-day cluster bootstrap 95% CI.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.metrics import winner_nll
from horseracing_training.cond_logit import race_softmax
from horseracing_training.target_encoding import apply_encoded_columns

SCRATCH = Path(__file__).parent
MODEL_DIR = Path("/Users/kuwatawaku/workspace/horseracing/artifacts/model_versions/lgbm-064-f02acc")
CLIP = 1e-6


# --------------------------------------------------------------------------- model
def load_model():
    import pickle

    import lightgbm as lgb

    booster = lgb.Booster(model_file=str(MODEL_DIR / "model.txt"))
    prep = pickle.load(open(MODEL_DIR / "preprocessor.pkl", "rb"))
    calibrator = pickle.load(open(MODEL_DIR / "calibrator.pkl", "rb"))
    meta = json.load(open(MODEL_DIR / "metadata.json"))
    return booster, prep, calibrator, meta


def make_X(rows: pd.DataFrame, prep) -> pd.DataFrame:
    """Replicate serving/predictor.py dtype coercion + target encoding."""
    feature_cols = list(prep["feature_cols"])
    cat_cols = list(prep.get("categorical_cols", []))
    encoders = prep.get("encoders", {})
    rows = rows.copy()
    for col in cat_cols:
        if col in rows.columns:
            rows[col] = rows[col].astype("category")
    numeric_cols = [c for c in feature_cols if c not in cat_cols and c not in encoders]
    for col in numeric_cols:
        rows[col] = pd.to_numeric(rows[col], errors="coerce")
    base = rows[feature_cols].copy()
    if encoders:
        encoded = {c: enc.transform(rows[c]) for c, enc in encoders.items()}
        return apply_encoded_columns(base, encoded, feature_cols)
    return base


def predict_win(rows: pd.DataFrame, group_sizes: list[int], booster, prep, calibrator):
    """raw -> race-softmax -> isotonic -> clip -> race-normalize (= assemble_predictions.win)."""
    X = make_X(rows, prep)
    raw = np.asarray(booster.predict(X), dtype=float)
    soft = race_softmax(raw, group_sizes)
    cal = np.asarray(calibrator.transform(soft), dtype=float)
    cal = np.clip(cal, CLIP, 1.0 - CLIP)
    # per-race normalisation
    idx = np.repeat(np.arange(len(group_sizes)), group_sizes)
    sums = np.bincount(idx, weights=cal, minlength=len(group_sizes))
    return cal / sums[idx]


# --------------------------------------------------------------------------- arms
def patch(feats: pd.DataFrame, arm: str) -> pd.DataFrame:
    out = feats  # copied by caller where needed
    if arm == "A_actual":
        return out
    out = out.copy()
    cw = pd.to_numeric(out["carried_weight"], errors="coerce").astype("float64")
    if arm == "B_nan":
        out["weight"] = np.nan
        out["weight_diff"] = np.nan
        out["carried_weight_ratio"] = np.nan
    elif arm == "C_prev":
        body = pd.to_numeric(out["prev_weight"], errors="coerce").astype("float64")
        out["weight"] = body
        out["weight_diff"] = np.nan
        out["carried_weight_ratio"] = np.where(body > 0, cw / body, np.nan)
    elif arm == "D_nodiff":
        out["weight_diff"] = np.nan
    else:
        raise ValueError(arm)
    return out


# --------------------------------------------------------------------------- main
def main() -> int:
    t0 = time.time()
    feats = pd.read_parquet(SCRATCH / "feats.parquet")
    meta = pd.read_parquet(SCRATCH / "meta.parquet")
    winners = pd.read_parquet(SCRATCH / "winners.parquet")
    prev = pd.read_parquet(SCRATCH / "prev_weight.parquet")

    # strictly-before only: drop a same-day "previous" entry (repo same-day exclusion)
    prev = prev.merge(meta[["race_id", "race_date"]], on="race_id", how="left")
    same_day = prev["prev_weight_date"].notna() & (
        pd.to_datetime(prev["prev_weight_date"]) >= pd.to_datetime(prev["race_date"])
    )
    prev.loc[same_day, ["prev_weight", "prev_weight_date"]] = None
    print(f"same-day previous entries nulled: {int(same_day.sum())}")

    feats = feats.merge(
        prev[["race_id", "horse_id", "prev_weight", "prev_weight_date"]],
        on=["race_id", "horse_id"], how="left",
    )
    feats = feats.merge(meta[["race_id", "race_date"]], on="race_id", how="left")

    # eligible races: exactly one finish_order==1 row (dead heats / no winner excluded)
    wcount = winners.groupby("race_id").size()
    eligible = set(wcount[wcount == 1].index)
    winner_of = dict(
        winners[winners["race_id"].isin(eligible)][["race_id", "horse_id"]].values
    )
    print(f"races with a single winner: {len(eligible)} (excluded {len(wcount) - len(eligible)})")

    feats = feats[feats["race_id"].isin(eligible)].copy()
    feats = feats.sort_values(["race_id", "horse_id"], kind="mergesort").reset_index(drop=True)
    # winner must be among the started rows we are scoring
    has_winner = feats.groupby("race_id")["horse_id"].apply(
        lambda s: winner_of[s.name] in set(s)
    )
    good = set(has_winner[has_winner].index)
    dropped = len(eligible) - len(good)
    feats = feats[feats["race_id"].isin(good)].copy()
    feats = feats.sort_values(["race_id", "horse_id"], kind="mergesort").reset_index(drop=True)
    print(f"races scored: {len(good)} (dropped {dropped}: winner not in started rows)")

    race_ids = feats["race_id"].to_numpy()
    group_sizes = feats.groupby("race_id", sort=False).size().tolist()
    ordered_races = feats["race_id"].drop_duplicates().tolist()
    race_date = dict(feats.groupby("race_id")["race_date"].first().items())
    is_winner = np.array(
        [winner_of[r] == h for r, h in zip(race_ids, feats["horse_id"].to_numpy())]
    )

    # coverage
    n_rows = len(feats)
    prev_cov = feats["prev_weight"].notna().mean()
    print(f"rows={n_rows}  prev_weight coverage={prev_cov:.3%}")
    lay = pd.to_numeric(feats["days_since_last"], errors="coerce")
    print(f"days_since_last: median={lay.median():.0f}  p90={lay.quantile(0.9):.0f}")

    booster, prep, calibrator, mmeta = load_model()
    print(f"model: {mmeta['model_version']} objective={mmeta['objective']} "
          f"calib={mmeta['calibration']} fit_through={mmeta.get('model_fit_through')}")

    results = {}
    for arm in ["A_actual", "B_nan", "C_prev", "D_nodiff"]:
        ta = time.time()
        rows = patch(feats, arm)
        win = predict_win(rows, group_sizes, booster, prep, calibrator)
        results[arm] = win
        wp = win[is_winner]
        nll, _ = winner_nll(wp.tolist())
        print(f"  {arm:10s} winner_nll={nll:.6f}  ({time.time()-ta:.1f}s)")

    # per-race winner prob -> paired diffs
    winner_idx = np.where(is_winner)[0]
    wrace = race_ids[winner_idx]
    per_race = {arm: dict(zip(wrace, results[arm][winner_idx])) for arm in results}

    def nll_of(arm, subset=None):
        vals = [per_race[arm][r] for r in ordered_races if subset is None or r in subset]
        return winner_nll(vals)[0]

    def paired(a, b, subset=None):
        """mean( -log p_a  −  -log p_b ) with race-day cluster CI. Negative = a better."""
        by_day: dict[str, list[float]] = {}
        for r in ordered_races:
            if subset is not None and r not in subset:
                continue
            pa = np.clip(per_race[a][r], 1e-15, 1 - 1e-15)
            pb = np.clip(per_race[b][r], 1e-15, 1 - 1e-15)
            by_day.setdefault(str(race_date[r]), []).append(float(-np.log(pa) + np.log(pb)))
        ci = race_day_cluster_bootstrap_ci_v1(by_day)
        n = sum(len(v) for v in by_day.values())
        return {"diff": ci.point, "ci_low": ci.ci_low, "ci_high": ci.ci_high,
                "n_races": n, "n_days": ci.n_days}

    # subgroup: races where EVERY started horse has a prev_weight (clean C)
    cov_by_race = feats.groupby("race_id")["prev_weight"].apply(lambda s: s.notna().all())
    full_cov = set(cov_by_race[cov_by_race].index)
    print(f"\nraces with full prev_weight coverage: {len(full_cov)}/{len(ordered_races)}")

    report = {"model": mmeta["model_version"], "n_races": len(ordered_races),
              "n_rows": int(n_rows), "prev_weight_coverage": float(prev_cov),
              "winner_nll": {a: nll_of(a) for a in results}, "paired": {}, "subgroups": {}}

    print("\n=== paired winner NLL diffs (negative = first arm better) ===")
    for a, b, label in [
        ("B_nan", "A_actual", "B−A  cost of losing weight entirely (production penalty)"),
        ("D_nodiff", "A_actual", "D−A  cost of losing weight_diff only (irrecoverable)"),
        ("B_nan", "D_nodiff", "B−D  cost of losing body weight given diff already gone"),
        ("C_prev", "B_nan", "C−B  the proposal vs production  ***"),
        ("C_prev", "D_nodiff", "C−D  proposal vs its ceiling"),
    ]:
        r = paired(a, b)
        report["paired"][f"{a}_vs_{b}"] = r
        star = "" if r["ci_low"] is None else (
            "  SIGNIF" if (r["ci_high"] < 0 or r["ci_low"] > 0) else "  ns")
        print(f"  {label}\n      diff={r['diff']:+.6f}  CI=[{r['ci_low']:+.6f},"
              f"{r['ci_high']:+.6f}]  n={r['n_races']}{star}")

    print("\n=== subgroup: races with FULL prev_weight coverage ===")
    for a, b in [("C_prev", "B_nan"), ("C_prev", "D_nodiff"), ("B_nan", "A_actual")]:
        r = paired(a, b, subset=full_cov)
        report["subgroups"][f"fullcov_{a}_vs_{b}"] = r
        star = "" if r["ci_low"] is None else (
            "  SIGNIF" if (r["ci_high"] < 0 or r["ci_low"] > 0) else "  ns")
        print(f"  {a} − {b}: diff={r['diff']:+.6f}  "
              f"CI=[{r['ci_low']:+.6f},{r['ci_high']:+.6f}]  n={r['n_races']}{star}")

    json.dump(report, open(SCRATCH / "killtest_report.json", "w"), indent=2)
    print(f"\ndone in {time.time()-t0:.1f}s -> killtest_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
