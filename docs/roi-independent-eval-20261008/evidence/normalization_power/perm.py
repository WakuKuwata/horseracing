"""Mechanism controls for N1 = (EV_ens > 1.2 * sum_p_race):
 1. plain threshold raise EV_ens > t (same count as N1 and a grid)
 2. permutation null: shuffle sum_p across races within year -> race-varying random threshold
 3. S3 + race filter sum_p < c (no re-ranking)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kuwatawaku/workspace/horseracing")
OUT = Path(sys.argv[1])
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
SEED_DIRS = ["armC_binary_drop-sameday+weightlive_from2007_ens15"] + [
    f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)]

rows = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "race_date", "year", "odds", "won", "race_ok", "dead_heat", "field_size"])
rows = rows[(rows.race_date >= "2010-01-01") & (rows.race_date <= "2026-06-26") & rows.race_ok & ~rows.dead_heat]
df = rows
for i, tag in enumerate(SEED_DIRS, 1):
    p = pd.read_parquet(ROOT / "artifacts/roi_explore/results" / tag / "predictions.parquet", columns=["race_id", "horse_id", "pred"]).rename(columns={"pred": f"pred_{i}"})
    df = df.merge(p, on=["race_id", "horse_id"], how="inner")
assert len(df) == len(rows)
ev_s = 1.0 + df[[f"pred_{i}" for i in range(1, 16)]].to_numpy(float) / 100.0
df["EV_ens"] = ev_s.mean(axis=1)
df["p_bar"] = (ev_s / df.odds.to_numpy(float)[:, None]).mean(axis=1)
df["sum_p"] = df.groupby("race_id")["p_bar"].transform("sum")
df["payout"] = np.where(df.won, df.odds * 100.0, 0.0)
d = df[df.year.between(2013, 2026)].reset_index(drop=True)
d.to_parquet(OUT / "pop_applied.parquet", index=False)
ev = d.EV_ens.to_numpy(); sp = d.sum_p.to_numpy(); pay = d.payout.to_numpy(); yr = d.year.to_numpy()
post = yr >= 2019


def roi(mask):
    n = int(mask.sum())
    return {"n": n, "hits": int((pay[mask] > 0).sum()), "roi": float(pay[mask].sum() / (100 * n)) if n else None}


res = {}
s3 = ev > 1.2
n1 = ev > 1.2 * sp
res["S3"] = {"ALL": roi(s3), "POST": roi(s3 & post)}
res["N1"] = {"ALL": roi(n1), "POST": roi(n1 & post)}

# 1. plain threshold grid + count-matched threshold
grid = {}
for t in [1.2, 1.22, 1.24, 1.25, 1.26, 1.28, 1.30, 1.35, 1.40]:
    m = ev > t
    grid[str(t)] = {"ALL": roi(m), "POST": roi(m & post)}
n_target = int(n1.sum())
t_match = float(np.sort(ev)[::-1][n_target - 1])
mm = ev >= t_match
grid[f"count_matched_t={t_match:.5f}"] = {"ALL": roi(mm), "POST": roi(mm & post)}
res["threshold_grid"] = grid

# 3. S3 + race filter
filt = {}
for c in [0.95, 1.0, 1.05, 1.10]:
    m = s3 & (sp < c)
    filt[f"S3 & sum_p<{c}"] = {"ALL": roi(m), "POST": roi(m & post)}
res["S3_race_filter"] = filt

# 2. permutation null: shuffle sum_p across races within year
race = d.drop_duplicates("race_id")[["race_id", "year", "sum_p"]].reset_index(drop=True)
rid = pd.factorize(d.race_id)[0]
race_idx = pd.factorize(d.race_id)[1]
race = race.set_index("race_id").loc[race_idx].reset_index()
rng = np.random.default_rng(11)
R = 500
perm_roi_all, perm_roi_post, perm_n = [], [], []
sp_race = race.sum_p.to_numpy()
yr_race = race.year.to_numpy()
for r in range(R):
    sp_perm = sp_race.copy()
    for y in np.unique(yr_race):
        ix = np.where(yr_race == y)[0]
        sp_perm[ix] = sp_race[rng.permutation(ix)]
    m = ev > 1.2 * sp_perm[rid]
    perm_roi_all.append(pay[m].sum() / (100 * m.sum()))
    perm_roi_post.append(pay[m & post].sum() / (100 * (m & post).sum()))
    perm_n.append(int(m.sum()))
perm_roi_all = np.array(perm_roi_all); perm_roi_post = np.array(perm_roi_post)
obs_all = res["N1"]["ALL"]["roi"]; obs_post = res["N1"]["POST"]["roi"]
res["perm_null_shuffle_sum_p_within_year"] = {
    "R": R, "seed": 11,
    "ALL": {"mean": float(perm_roi_all.mean()), "sd": float(perm_roi_all.std(ddof=1)),
            "q": {str(q): float(np.quantile(perm_roi_all, q)) for q in [.025, .5, .975]},
            "p_ge_obs": float((1 + (perm_roi_all >= obs_all).sum()) / (R + 1)), "obs": obs_all},
    "POST": {"mean": float(perm_roi_post.mean()), "sd": float(perm_roi_post.std(ddof=1)),
             "q": {str(q): float(np.quantile(perm_roi_post, q)) for q in [.025, .5, .975]},
             "p_ge_obs": float((1 + (perm_roi_post >= obs_post).sum()) / (R + 1)), "obs": obs_post},
    "n_mean": float(np.mean(perm_n)),
}
(OUT / "perm.json").write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
