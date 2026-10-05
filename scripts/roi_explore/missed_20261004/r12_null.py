"""R12_cross_pool — pre-registered pipeline false-positive simulations (prereg.json null_and_fpr).

arm (i): top-2 pair drawn from the devigged final quinella grid, payout = grid odds of the drawn pair,
          selection unchanged (lambda fixed at the real 2024 fit); R=200 (seeds 1..200), bootstrap b=2000.
arm (ii): winner drawn from q, payout = stored win odds of the drawn winner; R=200, permutation B=2000.
Reads only the parquet/pickle written by r12_cross_pool.py.
"""
from __future__ import annotations

import json
import pickle
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import r12_common as C  # noqa: E402
import r12_cross_pool as M  # noqa: E402

OUT = C.OUT
R = 200
T0 = time.time()


def arm_i_null():
    A1 = pd.read_parquet(OUT / "arm_i_races.parquet")
    with open(OUT / "arm_i_pairs.pkl", "rb") as f:
        pairs = {rid: (O, bands, k) for rid, O, bands, k in pickle.load(f)}
    A = A1[(A1.timing == "final") & A1.window.isin(["discovery", "confirm"])].reset_index(drop=True)
    n = len(A)
    maxp = max(len(pairs[r][0]) for r in A.race_id)
    cum = np.ones((n, maxp))
    Om = np.zeros((n, maxp))
    Bm = np.zeros((n, maxp), dtype=int)
    kk = np.zeros(n, dtype=int)
    for i, rid in enumerate(A.race_id):
        O, bands, k = pairs[rid]
        pi = (1.0 / O) / (1.0 / O).sum()
        cum[i, :len(O)] = np.cumsum(pi)
        cum[i, len(O) - 1:] = 1.0
        Om[i, :len(O)] = O
        Bm[i, :len(O)] = bands
        kk[i] = k
    bet = (A.ens_ev > 1.2).to_numpy()
    out = []
    for seed in range(1, R + 1):
        rng = np.random.default_rng(seed)
        u = rng.random(n)
        j = (cum < u[:, None]).sum(1)
        j = np.minimum(j, (Om > 0).sum(1) - 1)
        S = A.copy()
        S["ens_hit"] = j == kk
        S["D"] = Om[np.arange(n), j]
        S["win_band"] = Bm[np.arange(n), j]
        res = {}
        for win in ("discovery", "confirm"):
            w = S[S.window == win].reset_index(drop=True)
            days = sorted(w.race_date.unique())
            b_ = (w.ens_ev > 1.2).to_numpy()
            ret = np.where(w.ens_hit, w.D, 0.0).astype(float)
            th = M.theta_boot(w, b_, ret, w.ens_band.to_numpy(), days, w.D.to_numpy(float), b=2000)
            res[win] = th
        s = 1 if res["discovery"]["theta"] >= 1 else -1
        p = M.p_dir(res["confirm"]["reps"], res["confirm"]["theta"], s)
        out.append({"seed": seed, "theta_disc": res["discovery"]["theta"], "s": s,
                    "theta_conf": res["confirm"]["theta"], "p": p,
                    "roi_conf": res["confirm"]["ret"] / max(res["confirm"]["n_bets"], 1)})
        if seed % 50 == 0:
            print(f"[{time.time() - T0:.0f}s] arm i null {seed}", flush=True)
    df = pd.DataFrame(out)
    return df, {"R": R, "fpr_p_lt_0.05": float((df.p < 0.05).mean()), "fpr_p_lt_0.0125": float((df.p < 0.0125).mean()),
                "fpr_beats_parent_p_lt_0.05": float(((df.p < 0.05) & (df.s > 0)).mean()),
                "theta_conf_mean": float(df.theta_conf.mean()), "theta_conf_sd": float(df.theta_conf.std()),
                "roi_conf_mean": float(df.roi_conf.mean()), "roi_conf_sd": float(df.roi_conf.std()),
                "theta_conf_quantiles": [float(x) for x in df.theta_conf.quantile([0.025, 0.5, 0.975])]}


def arm_ii_null():
    H = pd.read_parquet(OUT / "arm_ii_horses.parquet")
    H = H[(H.timing == "final") & ~H.dead_heat & (H.race_date <= C.WIN_CUTOFF)].reset_index(drop=True)
    rids = H.race_id.to_numpy()
    starts = np.flatnonzero(np.r_[True, rids[1:] != rids[:-1]])
    ends = np.r_[starts[1:], len(H)]
    nr = len(starts)
    maxn = int((ends - starts).max())
    cum = np.ones((nr, maxn))
    for i, (a, b) in enumerate(zip(starts, ends)):
        q = H.q.to_numpy()[a:b]
        cum[i, :b - a] = np.cumsum(q / q.sum())
        cum[i, b - a - 1:] = 1.0
    sizes = ends - starts
    dsel = (H.race_date <= "2024-12-31").to_numpy()
    csel = (H.race_date >= "2025-01-01").to_numpy()
    out = []
    for seed in range(1, R + 1):
        rng = np.random.default_rng(seed)
        u = rng.random(nr)
        j = np.minimum((cum < u[:, None]).sum(1), sizes - 1)
        won = np.zeros(len(H), bool)
        won[starts + j] = True
        S = H.copy()
        S["won"] = won
        S["ret"] = np.where(won, S.odds, 0.0)
        rec = {"seed": seed}
        for srule, sd in (("S3", 20261004), ("S1", 20261005)):
            dres, _ = M.arm_ii_eval(S, srule, pd.Series(dsel, index=S.index), s=None, boot=False)
            s = 1 if dres["delta"] >= 0 else -1
            cres, _ = M.arm_ii_eval(S, srule, pd.Series(csel, index=S.index), s=s, Bp=2000, seed=sd, boot=False)
            rec[f"{srule}_s"] = s
            rec[f"{srule}_p"] = cres["perm"]["p"]
            rec[f"{srule}_delta_conf"] = cres["delta"]
        out.append(rec)
        if seed % 50 == 0:
            print(f"[{time.time() - T0:.0f}s] arm ii null {seed}", flush=True)
    df = pd.DataFrame(out)
    summ = {"R": R}
    for srule in ("S3", "S1"):
        summ[srule] = {"fpr_p_lt_0.05": float((df[f"{srule}_p"] < 0.05).mean()),
                       "fpr_p_lt_0.0167": float((df[f"{srule}_p"] < 0.05 / 3).mean()),
                       "delta_conf_mean": float(df[f"{srule}_delta_conf"].mean()),
                       "delta_conf_sd": float(df[f"{srule}_delta_conf"].std())}
    summ["any_of_S3_S1_p_lt_0.05"] = float(((df.S3_p < 0.05) | (df.S1_p < 0.05)).mean())
    return df, summ


def main():
    d1, s1 = arm_i_null()
    d2, s2 = arm_ii_null()
    d1.to_csv(OUT / "null_arm_i.csv", index=False)
    d2.to_csv(OUT / "null_arm_ii.csv", index=False)
    res = {"arm_i_grid_resampled": s1, "arm_ii_q_resampled": s2,
           "arm_iii": "not simulated (place dividends of non-placed horses unobserved); stratified permutation is the null"}
    (OUT / "null_fpr.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
