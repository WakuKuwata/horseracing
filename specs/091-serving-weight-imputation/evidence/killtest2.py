"""Body-weight kill-test v2 — codex review applied.

Changes vs v1:
  * prev weight source restricted to entry_status=STARTED with a non-null weight, matched with
    the repo's own convention (merge_asof backward, allow_exact_matches=False) instead of an
    ad-hoc window function. Cancelled-horse weights become a SEPARATE sensitivity arm C2
    ("last observed weight"), not the primary.
  * staleness uses weight_age_days = target_date − source_weight_date (NOT days_since_last,
    which skips intermediate runs whose weight was null).
  * D is reported as an ORACLE BENCHMARK, not a mathematical ceiling.
  * arms share one bootstrap draw so replicate-level differences stay additive.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from horseracing_eval.metrics import winner_nll
from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent))
from killtest import CLIP, load_model, make_X  # noqa: E402

from horseracing_db.session import create_db_engine  # noqa: E402
from horseracing_training.cond_logit import race_softmax  # noqa: E402

SCRATCH = Path(__file__).parent
ARMS = ["A_actual", "B_nan", "C_prev", "C2_lastobs", "D_nodiff"]
SEED = 20260809
B_BOOT = 2000

SRC_SQL = text("""
SELECT rh.horse_id, r.race_date, rh.weight, rh.entry_status
FROM race_horses rh JOIN races r ON r.race_id = rh.race_id
WHERE rh.weight IS NOT NULL AND rh.weight BETWEEN 200 AND 800
""")


def build_prev(targets: pd.DataFrame, src: pd.DataFrame, *, started_only: bool, col: str):
    """Latest weight strictly before the target race date (repo convention: merge_asof
    backward with allow_exact_matches=False -> same-day sources are excluded)."""
    s = src[src["entry_status"] == "started"] if started_only else src
    s = (s[["horse_id", "race_date", "weight"]]
         .rename(columns={"weight": col, "race_date": f"{col}_date"})
         .sort_values("race_date" if False else f"{col}_date", kind="stable"))
    s["race_date"] = s[f"{col}_date"]
    t = targets.sort_values("race_date", kind="stable")
    m = pd.merge_asof(t, s, on="race_date", by="horse_id",
                      direction="backward", allow_exact_matches=False)
    return m[["race_id", "horse_id", col, f"{col}_date"]]


def predict_all(rows, group_sizes, booster, prep, calibrator):
    X = make_X(rows, prep)
    raw = np.asarray(booster.predict(X), dtype=float)
    soft = race_softmax(raw, group_sizes)
    cal = np.clip(np.asarray(calibrator.transform(soft), dtype=float), CLIP, 1.0 - CLIP)
    idx = np.repeat(np.arange(len(group_sizes)), group_sizes)
    return cal / np.bincount(idx, weights=cal, minlength=len(group_sizes))[idx], soft


def patch2(feats: pd.DataFrame, arm: str) -> pd.DataFrame:
    if arm == "A_actual":
        return feats
    out = feats.copy()
    cw = pd.to_numeric(out["carried_weight"], errors="coerce").astype("float64")
    if arm == "B_nan":
        body = pd.Series(np.nan, index=out.index)
    elif arm == "C_prev":
        body = pd.to_numeric(out["prev_w"], errors="coerce").astype("float64")
    elif arm == "C2_lastobs":
        body = pd.to_numeric(out["lastobs_w"], errors="coerce").astype("float64")
    elif arm == "D_nodiff":
        out["weight_diff"] = np.nan
        return out
    else:
        raise ValueError(arm)
    out["weight"] = body
    out["weight_diff"] = np.nan
    out["carried_weight_ratio"] = np.where(body > 0, cw / body, np.nan)
    return out


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
    )
    t0 = time.time()
    feats = pd.read_parquet(SCRATCH / "feats.parquet")
    meta = pd.read_parquet(SCRATCH / "meta.parquet")
    winners = pd.read_parquet(SCRATCH / "winners.parquet")

    with Session(create_db_engine()) as session:
        src = pd.read_sql(SRC_SQL, session.connection())
    src["race_date"] = pd.to_datetime(src["race_date"])
    print(f"weight sources: {len(src)} (started={int((src['entry_status']=='started').sum())})")

    feats = feats.merge(meta[["race_id", "race_date"]], on="race_id", how="left")
    feats["race_date"] = pd.to_datetime(feats["race_date"])
    tg = feats[["race_id", "horse_id", "race_date"]].copy()

    prev = build_prev(tg, src, started_only=True, col="prev_w")
    last = build_prev(tg, src, started_only=False, col="lastobs_w")
    feats = feats.merge(prev, on=["race_id", "horse_id"], how="left")
    feats = feats.merge(last, on=["race_id", "horse_id"], how="left")
    feats["weight_age_days"] = (
        feats["race_date"] - pd.to_datetime(feats["prev_w_date"])
    ).dt.days

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

    print(f"races={len(ordered)} rows={len(feats)}")
    print(f"prev_w (started-only) coverage = {feats['prev_w'].notna().mean():.3%}")
    print(f"lastobs_w coverage            = {feats['lastobs_w'].notna().mean():.3%}")
    diff_src = (feats["prev_w"].notna() != feats["lastobs_w"].notna()).sum()
    both = feats["prev_w"].notna() & feats["lastobs_w"].notna()
    disagree = int((feats.loc[both, "prev_w"] != feats.loc[both, "lastobs_w"]).sum())
    print(f"source differs: availability {int(diff_src)} rows, value {disagree} rows")
    age = feats["weight_age_days"]
    print(f"weight_age_days: median={age.median():.0f} p90={age.quantile(0.9):.0f} "
          f"max={age.max():.0f}")

    booster, prep, calibrator, mmeta = load_model()
    cal_res, unc_res = {}, {}
    for arm in ARMS:
        c, u = predict_all(patch2(feats, arm), group_sizes, booster, prep, calibrator)
        cal_res[arm], unc_res[arm] = c, u

    widx = np.where(is_winner)[0]
    wrace = race_ids[widx]
    per = {sp: {a: dict(zip(wrace, res[a][widx])) for a in ARMS}
           for sp, res in [("cal", cal_res), ("unc", unc_res)]}

    days = sorted({str(rdate[r].date()) for r in ordered})
    day_index = {d: i for i, d in enumerate(days)}
    races_by_day: list[list[str]] = [[] for _ in days]
    for r in ordered:
        races_by_day[day_index[str(rdate[r].date())]].append(r)

    # one shared bootstrap draw for every arm pair (additive across replicates)
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(days), size=(B_BOOT, len(days)))

    def loss(space, arm):
        return {r: float(-np.log(np.clip(per[space][arm][r], 1e-15, 1 - 1e-15)))
                for r in ordered}

    losses = {sp: {a: loss(sp, a) for a in ARMS} for sp in ("cal", "unc")}

    def paired(space, a, b, subset=None):
        day_arrays = []
        for rs in races_by_day:
            v = [losses[space][a][r] - losses[space][b][r]
                 for r in rs if subset is None or r in subset]
            day_arrays.append(np.asarray(v, dtype=float))
        allv = np.concatenate([d for d in day_arrays if d.size]) if day_arrays else np.array([])
        if allv.size == 0:
            return {"diff": float("nan"), "ci_low": None, "ci_high": None, "n": 0}
        sums = np.array([d.sum() for d in day_arrays])
        cnts = np.array([d.size for d in day_arrays], dtype=float)
        bs = sums[draws].sum(axis=1) / np.maximum(cnts[draws].sum(axis=1), 1)
        return {"diff": float(allv.mean()), "ci_low": float(np.percentile(bs, 2.5)),
                "ci_high": float(np.percentile(bs, 97.5)), "n": int(allv.size)}

    def show(space, a, b, subset=None, label=""):
        r = paired(space, a, b, subset)
        sig = "SIGNIF" if r["ci_low"] is not None and (
            r["ci_high"] < 0 or r["ci_low"] > 0) else "ns"
        print(f"  {label or f'{a}−{b}':46s} {r['diff']:+.6f} "
              f"[{r['ci_low']:+.6f},{r['ci_high']:+.6f}] n={r['n']} {sig}")
        return r

    report = {"model": mmeta["model_version"], "n_races": len(ordered),
              "winner_nll": {sp: {a: winner_nll([per[sp][a][r] for r in ordered])[0]
                                  for a in ARMS} for sp in ("cal", "unc")}}
    print("\n=== winner NLL ===")
    for sp in ("cal", "unc"):
        print(f"  [{sp}] " + "  ".join(f"{a}={report['winner_nll'][sp][a]:.6f}" for a in ARMS))

    print("\n=== PRIMARY (calibrated) — negative = first arm better ===")
    report["primary"] = {
        "C_minus_B": show("cal", "C_prev", "B_nan", label="C−B  proposal vs production ***"),
        "C2_minus_B": show("cal", "C2_lastobs", "B_nan", label="C2−B last-observed variant"),
        "C_minus_C2": show("cal", "C_prev", "C2_lastobs", label="C−C2 source definition effect"),
        "C_minus_D": show("cal", "C_prev", "D_nodiff", label="C−D  vs actual-weight oracle"),
        "B_minus_D": show("cal", "B_nan", "D_nodiff", label="B−D  body-weight loss (oracle gap)"),
        "D_minus_A": show("cal", "D_nodiff", "A_actual", label="D−A  weight_diff contribution"),
    }
    print("\n=== diagnostic (uncalibrated race-softmax) ===")
    report["uncal"] = {"C_minus_B": show("unc", "C_prev", "B_nan", label="C−B"),
                       "B_minus_D": show("unc", "B_nan", "D_nodiff", label="B−D")}

    print("\n=== staleness bands (weight_age_days, max within race) ===")
    mx = feats.groupby("race_id")["weight_age_days"].max()
    report["staleness"] = {}
    for name, m in [("<=45d", mx <= 45), ("46-120d", (mx > 45) & (mx <= 120)),
                    ("121-365d", (mx > 120) & (mx <= 365)), (">365d", mx > 365)]:
        sub = set(mx[m].index)
        if len(sub) < 50:
            print(f"  {name}: n={len(sub)} too small")
            continue
        report["staleness"][name] = show("cal", "C_prev", "B_nan", sub, label=f"C−B {name}")

    print("\n=== coverage cohorts ===")
    cov = feats.groupby("race_id")["prev_w"].apply(lambda s: s.notna().mean())
    report["cohorts"] = {}
    for name, sub in [("all sourced", set(cov[cov == 1.0].index)),
                      ("partial", set(cov[(cov > 0) & (cov < 1)].index)),
                      ("none sourced", set(cov[cov == 0].index))]:
        if len(sub) < 50:
            print(f"  {name}: n={len(sub)} too small")
            continue
        report["cohorts"][name] = show("cal", "C_prev", "B_nan", sub, label=f"C−B {name}")

    json.dump(report, open(SCRATCH / "killtest2_report.json", "w"), indent=2, default=float)
    print(f"\ndone in {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
