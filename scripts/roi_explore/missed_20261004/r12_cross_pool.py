"""R12_cross_pool — runs the frozen prereg (artifacts/.../R12_cross_pool/prereg.json).

Read-only: SELECT from the DB, parquet artifacts; writes only under the R12 output dir.
Run: cd training && uv run python ../scripts/roi_explore/missed_20261004/r12_cross_pool.py
"""
from __future__ import annotations

import itertools
import json
import math
import sys
import time

import numpy as np
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import r12_common as C  # noqa: E402
from horseracing_eval.attention_rules import definition, match_mask  # noqa: E402
from horseracing_eval.bootstrap import (  # noqa: E402
    block_bootstrap_counts,
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
    race_day_cluster_bootstrap_ci_v1,
)

OUT = C.OUT
PREREG = OUT / "prereg.json"
SEED = 20261004
B = 20000
T0 = time.time()
LOG = []


def log(*a):
    s = f"[{time.time() - T0:7.1f}s] " + " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def window_of(ds: str, kind: str) -> str:
    if kind == "i" or kind == "ii":
        if ds <= "2024-12-31":
            return "discovery"
        if ds <= C.WIN_CUTOFF:
            return "confirm"
        return "post"
    if kind == "iii":
        if ds <= "2025-12-31":
            return "discovery"
        if ds <= C.WIN_CUTOFF:
            return "confirm"
        return "post"
    raise ValueError(kind)


QBANDS = [1, 5, 10, 20, 50, 100, 200, 500, np.inf]


def qband(o: float) -> int:
    for k in range(len(QBANDS) - 1):
        if QBANDS[k] <= o < QBANDS[k + 1]:
            return k
    return len(QBANDS) - 2 if o >= QBANDS[-2] else 0


SBANDS = [0, 5, 10, 20, 40, np.inf]


def sband(o: float) -> int:
    for k in range(len(SBANDS) - 1):
        if SBANDS[k] <= o < SBANDS[k + 1]:
            return k
    return len(SBANDS) - 2


def popband(p: float) -> int:
    if p == 1:
        return 0
    if p <= 3:
        return 1
    if p <= 6:
        return 2
    if p <= 9:
        return 3
    return 4


def ratio_ci(num_by_day: dict, den_by_day: dict, days: list[str], seed=SEED, b=B):
    num = np.array([num_by_day.get(d, 0.0) for d in days], float)
    den = np.array([den_by_day.get(d, 0.0) for d in days], float)
    r = race_block_ratio_bootstrap_ci_v1(num, den, days, b=b, seed=seed)
    row = r.row(0)
    p = centered_one_sided_p_from_replicates(r.replicates[0], float(r.point[0]))
    return {"point": float(r.point[0]), "ci": [row.ci_low, row.ci_high], "p_gt1": p,
            "num": float(num.sum()), "den": float(den.sum()), "n_days": len(days)}


def by_day(df: pd.DataFrame, col: str) -> dict:
    return df.groupby("race_date")[col].sum().to_dict()


# ------------------------------------------------------------------------------------------
# loading
# ------------------------------------------------------------------------------------------

def load_all():
    d, prov = C.load_rows(2024, with_outcomes=True)
    for rid in ("S1", "S3"):
        d[rid] = match_mask(definition(rid), ens_ev=d.ens_ev, single_ev=d.single_ev, odds=d.odds,
                            days_since_last=d.days_since_last)
    with C.engine().connect() as c:
        canc = pd.read_sql(text("""select distinct rh.race_id from race_horses rh join races r using(race_id)
                                   where rh.entry_status='cancelled' and rh.odds is not null
                                   and r.race_date >= '2024-01-01'"""), c)
    canc_set = set(canc.race_id)
    prov["n_races_postclose_scratch_2024plus"] = len(canc_set)
    gq = C.load_grids("quinella")
    gt = C.load_grids("trio")
    dv = C.load_dividends(("quinella", "place"))
    prov["prereg_sha256"] = C.sha(PREREG)
    return d, canc_set, gq, gt, dv, prov


# ------------------------------------------------------------------------------------------
# arm (i)
# ------------------------------------------------------------------------------------------

def fit_lambda_2024(d: pd.DataFrame) -> dict:
    x = d[(d.year == 2024) & d.race_pred_ok & ~d.dead_heat]
    terms = []
    for rid, g in x.groupby("race_id", sort=False):
        fo = g.finish_order.to_numpy()
        i1 = np.flatnonzero(fo == 1)
        i2 = np.flatnonzero(fo == 2)
        if len(i1) != 1 or len(i2) != 1:
            continue
        p = g.p_ens_raw.to_numpy(float)
        p = p / p.sum()
        mask = np.ones(len(p), bool)
        mask[i1[0]] = False
        terms.append((np.log(p[i2[0]]), np.log(p[mask])))

    def nll(lam):
        s = 0.0
        for lp2, lps in terms:
            s -= lam * lp2 - np.log(np.exp(lam * lps).sum())
        return s

    a, b_ = 0.2, 2.0
    gr = (math.sqrt(5) - 1) / 2
    c1 = b_ - gr * (b_ - a)
    c2 = a + gr * (b_ - a)
    f1, f2 = nll(c1), nll(c2)
    while b_ - a > 1e-6:
        if f1 < f2:
            b_, c2, f2 = c2, c1, f1
            c1 = b_ - gr * (b_ - a)
            f1 = nll(c1)
        else:
            a, c1, f1 = c1, c2, f2
            c2 = a + gr * (b_ - a)
            f2 = nll(c2)
    lam = (a + b_) / 2
    return {"lambda_ens": lam, "n_races": len(terms), "nll_at_lambda": nll(lam), "nll_at_1": nll(1.0),
            "nll_at_0.75": nll(0.75), "per_race_gain_vs_1": (nll(1.0) - nll(lam)) / len(terms)}


def build_arm_i(d, canc_set, gq, dv, lam_ens):
    qdiv = dv[dv.bet_type == "quinella"]
    qdiv_map = {}
    for r in qdiv.itertuples():
        qdiv_map.setdefault(r.race_id, []).append((tuple(sorted(int(x) for x in r.selection)), r.odds))
    grid_by_race = {r.race_id: (r.quotes, r.timing) for r in gq.itertuples()}
    excl = {}
    rows = []
    allpairs_rows = []  # per race: band counts and winning-band return

    def ex(k):
        excl[k] = excl.get(k, 0) + 1

    for rid, g in d.groupby("race_id", sort=False):
        if rid not in grid_by_race:
            continue
        quotes, timing = grid_by_race[rid]
        if not bool(g.race_pred_ok.iloc[0]):
            ex("pred_incomplete"); continue
        if rid in canc_set:
            ex("postclose_scratch"); continue
        nums = g.horse_number.to_numpy().astype(int)
        st = set(int(x) for x in nums)
        if len(st) != len(nums):
            ex("dup_horse_number"); continue
        grid = C.parse_grid(quotes, st, 2)
        if len(grid) != len(st) * (len(st) - 1) // 2:
            ex("grid_incomplete"); continue
        fo = g.finish_order.to_numpy()
        i1 = np.flatnonzero(fo == 1)
        i2 = np.flatnonzero(fo == 2)
        if len(i1) != 1 or len(i2) != 1:
            ex("tie_or_missing_top2"); continue
        top2 = tuple(sorted((int(nums[i1[0]]), int(nums[i2[0]]))))
        divs = qdiv_map.get(rid)
        if divs is None and timing == "pre":
            ex("pre_no_dividend"); continue
        if divs is not None:
            if len(divs) != 1 or divs[0][0] != top2:
                ex("dividend_integrity"); continue
            D = float(divs[0][1])
            dsrc = "dividend"
        else:
            D = grid[top2]
            dsrc = "grid"
        ds = g.race_date.iloc[0]
        idx = {n: k for k, n in enumerate(nums)}
        p = g.p_ens_raw.to_numpy(float)
        p = p / p.sum()
        q = g.q.to_numpy(float)
        q = q / q.sum()
        Pm = C.harville_top2(p, lam_ens)
        Pm1 = C.harville_top2(p, 1.0)
        Pm75 = C.harville_top2(p, 0.75)
        Pq = C.harville_top2(q, C.MARKET_L2)
        pairs = sorted(grid)
        O = np.array([grid[c] for c in pairs])
        ii = np.array([idx[a] for a, _ in pairs])
        jj = np.array([idx[b] for _, b in pairs])
        inv = 1.0 / O
        pay_rate = 1.0 / inv.sum()
        hitv = np.array([c == top2 for c in pairs])
        bands = np.array([qband(o) for o in O])
        bc = np.bincount(bands, minlength=8)
        wband = int(bands[hitv][0])
        rec = {"race_id": rid, "race_date": ds, "year": int(ds[:4]), "timing": timing,
               "window": window_of(ds, "i"), "n": len(nums), "pay_rate": pay_rate, "D": D, "dsrc": dsrc,
               "grid_win_odds": float(grid[top2]), "win_band": wband,
               "has_S3": bool(g.S3.any())}
        for k in range(8):
            rec[f"nb_all{k}"] = int(bc[k])
        for tag, P in (("ens", Pm[ii, jj]), ("ens_l1", Pm1[ii, jj]), ("ens_l075", Pm75[ii, jj]),
                       ("q", Pq[ii, jj])):
            ev = P * O
            k = int(np.argmax(ev))  # pairs sorted -> first max = lexicographically smallest
            rec[f"{tag}_ev"] = float(ev[k])
            rec[f"{tag}_P"] = float(P[k])
            rec[f"{tag}_O"] = float(O[k])
            rec[f"{tag}_band"] = int(bands[k])
            rec[f"{tag}_hit"] = bool(hitv[k])
            rec[f"{tag}_pair"] = f"{pairs[k][0]}-{pairs[k][1]}"
        rows.append(rec)
        # store per-pair info for null resampling (probabilities) compactly
        allpairs_rows.append((rid, O, bands, int(np.argmax(Pm[ii, jj] * O))))
    df = pd.DataFrame(rows)
    return df, excl, allpairs_rows


def theta_boot(df_w: pd.DataFrame, bet_mask: np.ndarray, ret: np.ndarray, band: np.ndarray,
               days: list[str], all_ret_win: np.ndarray, b=B, seed=SEED, parent_df=None):
    """theta = sum ret_bets / sum_b nb_bets_b * (R_b/N_b); parent from all pairs of the same races."""
    di = {dd: k for k, dd in enumerate(days)}
    nd = len(days)
    num = np.zeros(nd)
    nb = np.zeros((nd, 8))
    R = np.zeros((nd, 8))
    N = np.zeros((nd, 8))
    dk = df_w.race_date.map(di).to_numpy()
    np.add.at(num, dk[bet_mask], ret[bet_mask])
    np.add.at(nb, (dk[bet_mask], band[bet_mask]), 1.0)
    pdf = df_w if parent_df is None else parent_df
    pdk = pdf.race_date.map(di).to_numpy()
    np.add.at(R, (pdk, pdf.win_band.to_numpy()), all_ret_win)
    for k in range(8):
        np.add.at(N[:, k], pdk, pdf[f"nb_all{k}"].to_numpy(float))
    Rt, Nt = R.sum(0), N.sum(0)
    with np.errstate(divide="ignore", invalid="ignore"):
        proi = np.where(Nt > 0, Rt / Nt, 0.0)
    den = (nb.sum(0) * proi).sum()
    point = num.sum() / den if den > 0 else float("nan")
    W = block_bootstrap_counts(nd, b, seed).astype(float)   # (b, nd)
    num_s = W @ num
    nb_s = W @ nb
    R_s = W @ R
    N_s = W @ N
    with np.errstate(divide="ignore", invalid="ignore"):
        proi_s = np.where(N_s > 0, R_s / N_s, 0.0)
        den_s = (nb_s * proi_s).sum(1)
        reps = np.where(den_s > 0, num_s / den_s, np.nan)
    lo, hi = np.nanpercentile(reps, [2.5, 97.5])
    return {"theta": float(point), "ci": [float(lo), float(hi)], "reps": reps,
            "parent_roi_by_band": [float(x) for x in proi], "parent_n_by_band": [int(x) for x in Nt],
            "n_bets": int(bet_mask.sum()), "ret": float(num.sum()), "den": float(den)}


def p_dir(reps, point, s):
    """one-sided centered p in direction s (s=+1: H0 theta<=1; s=-1: H0 1/theta<=1)."""
    if s > 0:
        return centered_one_sided_p_from_replicates(reps, point)
    with np.errstate(divide="ignore"):
        return centered_one_sided_p_from_replicates(1.0 / reps, 1.0 / point)


def arm_i_window(df, win, tag="ens", gate=1.2, timing="final", b=B):
    w = df[(df.window == win) & (df.timing == timing)].reset_index(drop=True)
    if win == "pre":
        w = df[df.timing == "pre"].reset_index(drop=True)
    days = sorted(w.race_date.unique())
    bet = (w[f"{tag}_ev"] > gate).to_numpy() if gate is not None else np.ones(len(w), bool)
    ret = np.where(w[f"{tag}_hit"], w.D, 0.0).astype(float)
    band = w[f"{tag}_band"].to_numpy()
    th = theta_boot(w, bet, ret, band, days, w.D.to_numpy(float), b=b)
    wb = w[bet].copy()
    wb["ret"] = ret[bet]
    wb["one"] = 1.0
    out = {"n_races": len(w), "n_days": len(days), "n_bets": int(bet.sum()), "hits": int(wb[f"{tag}_hit"].sum()),
           "theta_matched_parent": {k: v for k, v in th.items() if k != "reps"}}
    if len(wb):
        out["roi"] = ratio_ci(by_day(wb, "ret"), by_day(wb, "one"), days, b=b)
        out["takeout_null_ratio"] = ratio_ci(by_day(wb, "ret"), by_day(wb, "pay_rate"), days, b=b)
        out["expected_hits"] = float(wb[f"{tag}_P"].sum())
        out["mean_odds"] = float(wb[f"{tag}_O"].mean())
        out["median_odds"] = float(wb[f"{tag}_O"].median())
        out["max_hit_share"] = float(wb.ret.max() / wb.ret.sum()) if wb.ret.sum() > 0 else None
        out["per_year"] = {int(y): {"n": int(len(g)), "hits": int(g[f"{tag}_hit"].sum()),
                                    "roi": float(g.ret.sum() / len(g))} for y, g in wb.groupby("year")}
        out["year_weighted_roi"] = float(np.mean([v["roi"] for v in out["per_year"].values()]))
    return out, th


# ------------------------------------------------------------------------------------------
# arm (ii)
# ------------------------------------------------------------------------------------------

def build_arm_ii(d, canc_set, gq):
    grid_by_race = {r.race_id: (r.quotes, r.timing) for r in gq.itertuples()}
    rows = []
    excl = {}
    for rid, g in d.groupby("race_id", sort=False):
        if rid not in grid_by_race:
            continue
        quotes, timing = grid_by_race[rid]
        if rid in canc_set:
            excl["postclose_scratch"] = excl.get("postclose_scratch", 0) + 1; continue
        nums = g.horse_number.to_numpy().astype(int)
        st = set(int(x) for x in nums)
        if len(st) != len(nums):
            excl["dup_horse_number"] = excl.get("dup_horse_number", 0) + 1; continue
        grid = C.parse_grid(quotes, st, 2)
        if len(grid) != len(st) * (len(st) - 1) // 2:
            excl["grid_incomplete"] = excl.get("grid_incomplete", 0) + 1; continue
        idx = {n: k for k, n in enumerate(nums)}
        inv = {c: 1.0 / o for c, o in grid.items()}
        tot = sum(inv.values())
        x = np.zeros(len(nums))
        for (a, b_), v in inv.items():
            x[idx[a]] += v / tot
            x[idx[b_]] += v / tot
        q = g.q.to_numpy(float)
        q = q / q.sum()
        h2, _ = C.harville_top_marginals(q, C.MARKET_L2, C.MARKET_L3)
        pe = g.p_ens_raw.to_numpy(float)
        pe_n = pe / pe.sum() if np.isfinite(pe).all() else np.full(len(pe), np.nan)
        fo = g.finish_order.to_numpy()
        top2 = (fo == 1) | (fo == 2)
        sub = pd.DataFrame({"race_id": rid, "race_date": g.race_date.iloc[0], "year": g.year.iloc[0],
                            "timing": timing, "horse_number": nums, "horse_id": g.horse_id.to_numpy(),
                            "odds": g.odds.to_numpy(float), "q": q, "x": x, "h2": h2, "v": x / h2,
                            "S1": g.S1.to_numpy(), "S3": g.S3.to_numpy(), "won": g.won.to_numpy(bool),
                            "top2": top2, "dead_heat": g.dead_heat.to_numpy(bool),
                            "p_ens_n": pe_n, "race_pred_ok": g.race_pred_ok.to_numpy()})
        rows.append(sub)
    return pd.concat(rows, ignore_index=True), excl


def strat_perm_p(df_s: pd.DataFrame, label: np.ndarray, ret: np.ndarray, strata: np.ndarray, s: int,
                 Bp: int, seed: int):
    """Delta = ROI_label - ROI_all; permute labels within strata. one-sided p in direction s."""
    n_lab = label.sum()
    if n_lab == 0:
        return {"delta": None, "p": None}
    roi_all = ret.sum() / len(ret)
    obs = ret[label].sum() / n_lab - roi_all
    rng = np.random.default_rng(seed)
    perm_sums = np.zeros(Bp)
    for st in np.unique(strata):
        m = np.flatnonzero(strata == st)
        k = int(label[m].sum())
        if k == 0:
            continue
        r = ret[m]
        for c0 in range(0, Bp, 2000):
            c1 = min(Bp, c0 + 2000)
            keys = rng.random((c1 - c0, len(m)))
            sel = np.argpartition(keys, k - 1, axis=1)[:, :k] if k < len(m) else np.tile(np.arange(len(m)), (c1 - c0, 1))
            perm_sums[c0:c1] += r[sel].sum(1)
    perm = perm_sums / n_lab - roi_all
    p = (1 + int(np.sum(s * perm >= s * obs - 1e-12))) / (Bp + 1)
    return {"delta": float(obs), "p": float(p), "perm_mean": float(perm.mean()), "perm_sd": float(perm.std())}


def arm_ii_eval(h: pd.DataFrame, srule: str, win_sel, settle_col="ret", Bp=B, seed=SEED, s=None,
                boot=True):
    x = h[h[srule] & win_sel].copy()
    if len(x) == 0:
        return {"n": 0}, None
    x["one"] = 1.0
    keep = (x.v >= 1).to_numpy()
    ret = x[settle_col].to_numpy(float)
    strata = (x.year.astype(str) + "_" + x.odds.map(sband).astype(str)).to_numpy()
    roi_keep = ret[keep].sum() / max(keep.sum(), 1)
    roi_all = ret.sum() / len(ret)
    roi_veto = ret[~keep].sum() / max((~keep).sum(), 1)
    delta = roi_keep - roi_all
    out = {"n": int(len(x)), "n_keep": int(keep.sum()), "n_veto": int((~keep).sum()),
           "hits_keep": int(x.won.to_numpy()[keep].sum()), "hits_veto": int(x.won.to_numpy()[~keep].sum()),
           "roi_S": roi_all, "roi_keep": roi_keep, "roi_veto": roi_veto, "delta": delta,
           "keep_frac": float(keep.mean())}
    if np.isfinite(x.p_ens_n).all():
        out["AE_keep"] = float(x.won.to_numpy()[keep].sum() / x.p_ens_n.to_numpy()[keep].sum()) if keep.any() else None
        out["AE_veto"] = float(x.won.to_numpy()[~keep].sum() / x.p_ens_n.to_numpy()[~keep].sum()) if (~keep).any() else None
    if s is not None:
        out["perm"] = strat_perm_p(x, keep, ret, strata, s, Bp, seed)
    if boot:
        days = sorted(x.race_date.unique())
        xk = x[keep]
        xv = x[~keep]
        out["roi_keep_ci"] = ratio_ci(by_day(xk, settle_col), by_day(xk, "one"), days)["ci"] if len(xk) else None
        out["roi_veto_ci"] = ratio_ci(by_day(xv, settle_col), by_day(xv, "one"), days)["ci"] if len(xv) else None
        out["roi_S_ci"] = ratio_ci(by_day(x, settle_col), by_day(x, "one"), days)["ci"]
        out["per_year"] = {int(y): {"n": int(len(g)), "roi_S": float(g[settle_col].sum() / len(g)),
                                    "roi_keep": float(g[settle_col][g.v >= 1].sum() / max((g.v >= 1).sum(), 1)),
                                    "n_keep": int((g.v >= 1).sum())} for y, g in x.groupby("year")}
    return out, x


# ------------------------------------------------------------------------------------------
# arm (iii)
# ------------------------------------------------------------------------------------------

def build_arm_iii(d, canc_set, gt, dv):
    pdiv = dv[dv.bet_type == "place"]
    pmap = {}
    for r in pdiv.itertuples():
        pmap.setdefault(r.race_id, []).append((int(r.selection[0]), float(r.odds)))
    grid_by_race = {r.race_id: (r.quotes, r.timing) for r in gt.itertuples()}
    rows = []
    excl = {}

    def ex(k):
        excl[k] = excl.get(k, 0) + 1

    for rid, g in d.groupby("race_id", sort=False):
        if rid not in grid_by_race:
            continue
        quotes, timing = grid_by_race[rid]
        n = len(g)
        if n < 8:
            ex("n_lt_8"); continue
        if rid in canc_set:
            ex("postclose_scratch"); continue
        nums = g.horse_number.to_numpy().astype(int)
        st = set(int(x) for x in nums)
        if len(st) != len(nums):
            ex("dup_horse_number"); continue
        grid = C.parse_grid(quotes, st, 3)
        if len(grid) != math.comb(len(st), 3):
            ex("grid_incomplete"); continue
        fo = g.finish_order.to_numpy()
        tops = [np.flatnonzero(fo == k) for k in (1, 2, 3)]
        if any(len(t) != 1 for t in tops):
            ex("tie_or_missing_top3"); continue
        top3 = {int(nums[t[0]]) for t in tops}
        pl = pmap.get(rid)
        if pl is None or len(pl) != 3 or {a for a, _ in pl} != top3:
            ex("place_dividend_integrity"); continue
        pdict = dict(pl)
        idx = {nn: k for k, nn in enumerate(nums)}
        inv = {c: 1.0 / o for c, o in grid.items()}
        tot = sum(inv.values())
        P3T = np.zeros(n)
        for (a, b_, c_), v in inv.items():
            P3T[idx[a]] += v / tot
            P3T[idx[b_]] += v / tot
            P3T[idx[c_]] += v / tot
        q = g.q.to_numpy(float)
        q = q / q.sum()
        _, P3W = C.harville_top_marginals(q, C.MARKET_L2, C.MARKET_L3)
        pop = g.popularity.to_numpy(float)
        pop = np.where(np.isnan(pop), g.odds_rank.to_numpy(float), pop)
        ret = np.array([pdict.get(int(nn), 0.0) for nn in nums])
        sub = pd.DataFrame({"race_id": rid, "race_date": g.race_date.iloc[0], "year": g.year.iloc[0],
                            "timing": timing, "horse_number": nums, "popularity": pop,
                            "band": [popband(x) for x in pop], "P3T": P3T, "P3W": P3W, "r": P3T / P3W,
                            "placed": np.isin(nums, list(top3)), "ret": ret, "odds": g.odds.to_numpy(float)})
        rows.append(sub)
    return pd.concat(rows, ignore_index=True), excl


def arm_iii_eval(h: pd.DataFrame, sel, cuts: dict, s=None, Bp=B, seed=20261006):
    x = h[sel].copy()
    if len(x) == 0:
        return {"n": 0}
    x["one"] = 1.0
    x["top"] = [r >= cuts[b][1] for r, b in zip(x.r, x.band)]
    x["bottom"] = [r <= cuts[b][0] for r, b in zip(x.r, x.band)]
    ret = x.ret.to_numpy(float)
    top = x.top.to_numpy()
    bot = x.bottom.to_numpy()
    band = x.band.to_numpy()
    roi_band = {int(b_): float(g.ret.sum() / len(g)) for b_, g in x.groupby("band")}
    nt = top.sum()
    roi_top = ret[top].sum() / max(nt, 1)
    parent_matched = float(np.mean([roi_band[b_] for b_ in band[top]])) if nt else float("nan")
    out = {"n_horses": int(len(x)), "n_races": int(x.race_id.nunique()), "n_top": int(nt),
           "n_bottom": int(bot.sum()), "hits_top": int(x.placed.to_numpy()[top].sum()),
           "hits_bottom": int(x.placed.to_numpy()[bot].sum()),
           "roi_top": float(roi_top), "roi_bottom": float(ret[bot].sum() / max(bot.sum(), 1)),
           "roi_all": float(ret.sum() / len(ret)), "roi_parent_matched": parent_matched,
           "delta": float(roi_top - parent_matched), "roi_band_all": roi_band,
           "per_band": {int(b_): {"n_top": int(g.top.sum()), "roi_top": float(g.ret[g.top].sum() / max(g.top.sum(), 1)),
                                  "n_bottom": int(g.bottom.sum()),
                                  "roi_bottom": float(g.ret[g.bottom].sum() / max(g.bottom.sum(), 1)),
                                  "roi_band": float(g.ret.sum() / len(g)), "n": int(len(g))}
                        for b_, g in x.groupby("band")}}
    days = sorted(x.race_date.unique())
    xt, xb = x[top], x[bot]
    if len(xt):
        out["roi_top_ci"] = ratio_ci(by_day(xt, "ret"), by_day(xt, "one"), days)
    if len(xb):
        out["roi_bottom_ci"] = ratio_ci(by_day(xb, "ret"), by_day(xb, "one"), days)
    out["roi_all_ci"] = ratio_ci(by_day(x, "ret"), by_day(x, "one"), days)
    out["per_year"] = {int(y): {"roi_top": float(g.ret[g.top].sum() / max(g.top.sum(), 1)), "n_top": int(g.top.sum())}
                       for y, g in x.groupby("year")}
    if s is not None:
        strata = (x.year.astype(str) + "_" + x.band.astype(str)).to_numpy()
        # Delta = ROI_top - parent_matched; parent constant under within-band permutation
        pr = strat_perm_p(x, top, ret, strata, s, Bp, seed)
        # convert: strat_perm_p returns ROI_top - ROI_all; shift to parent_matched scale
        shift = out["roi_all"] - parent_matched
        out["perm"] = {"delta_vs_parent": float(pr["delta"] + shift), "p": pr["p"],
                       "perm_mean_delta": float(pr["perm_mean"] + shift), "perm_sd": pr["perm_sd"]}
    return out


# ------------------------------------------------------------------------------------------
# leak diagnostic
# ------------------------------------------------------------------------------------------

def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def leak_diag(h: pd.DataFrame, pool_col: str, win_col: str, ycol: str, groups: dict) -> dict:
    out = {}
    for name, sel in groups.items():
        x = h[sel]
        if len(x) == 0:
            out[name] = {"n": 0}
            continue
        diff = ll(x[win_col].to_numpy(), x[ycol].to_numpy(float)) - ll(x[pool_col].to_numpy(), x[ycol].to_numpy(float))
        dd = {}
        for dday, v in zip(x.race_date.to_numpy(), diff):
            dd.setdefault(dday, []).append(v)
        ci = race_day_cluster_bootstrap_ci_v1(dd, b=2000, seed=SEED)
        out[name] = {"n_horses": int(len(x)), "n_races": int(x.race_id.nunique()), "mean_ll_gain": ci.point,
                     "ci": [ci.ci_low, ci.ci_high], "n_days": ci.n_days}
    return out


# ------------------------------------------------------------------------------------------
# main
# ------------------------------------------------------------------------------------------

def main():
    d, canc_set, gq, gt, dv, prov = load_all()
    log("rows", len(d), "postclose-scratch races", len(canc_set))
    res = {"test_id": "R12_cross_pool", "prereg_sha256": prov["prereg_sha256"], "provenance": prov}

    # ---------------- arm (i)
    lamfit = fit_lambda_2024(d)
    lam = lamfit["lambda_ens"]
    log("lambda_ens", lamfit)
    A1, ex1, allpairs = build_arm_i(d, canc_set, gq, dv, lam)
    log("arm i races", len(A1), ex1)
    A1.to_parquet(OUT / "arm_i_races.parquet")
    import pickle
    with open(OUT / "arm_i_pairs.pkl", "wb") as f:
        pickle.dump([(rid, O, bands, k) for rid, O, bands, k in allpairs if rid in set(A1.race_id)], f)
    r1 = {"lambda_fit": lamfit, "exclusions": ex1,
          "n_races_by_window_timing": {f"{a}|{b}": int(v) for (a, b), v in A1.groupby(["window", "timing"]).size().items()}}
    both = A1[A1.dsrc == "dividend"]
    r1["grid_vs_dividend_agreement"] = {"n": int(len(both)),
                                        "exact_equal_rate": float((np.abs(both.grid_win_odds - both.D) < 1e-9).mean()) if len(both) else None,
                                        "median_ratio_D_over_grid": float((both.D / both.grid_win_odds).median()) if len(both) else None}
    disc, th_disc = arm_i_window(A1, "discovery")
    s_i = 1 if th_disc["theta"] >= 1 else -1
    conf, th_conf = arm_i_window(A1, "confirm")
    p_i = p_dir(th_conf["reps"], th_conf["theta"], s_i)
    r1["discovery"] = disc
    r1["direction"] = s_i
    r1["confirm"] = conf
    r1["confirm_primary_p"] = p_i
    r1["post_final"] = arm_i_window(A1, "post")[0]
    pre, th_pre = arm_i_window(A1, "pre")
    r1["pre_race"] = pre
    r1["pre_direction_agrees"] = bool((th_pre["theta"] >= 1) == (s_i > 0)) if np.isfinite(th_pre["theta"]) else None
    # baselines and sensitivity (confirm + discovery)
    base = {}
    for win in ("discovery", "confirm"):
        bw = {}
        bw["B1a_qHarville_always"] = arm_i_window(A1, win, tag="q", gate=None)[0]
        bw["B1b_qHarville_ev1.2"] = arm_i_window(A1, win, tag="q", gate=1.2)[0]
        bw["B0_ens_argmax_always"] = arm_i_window(A1, win, tag="ens", gate=None)[0]
        for gte in (1.0, 1.1, 1.5):
            bw[f"ens_ev{gte}"] = arm_i_window(A1, win, tag="ens", gate=gte)[0]
        bw["ens_lambda1_ev1.2"] = arm_i_window(A1, win, tag="ens_l1", gate=1.2)[0]
        bw["ens_lambda0.75_ev1.2"] = arm_i_window(A1, win, tag="ens_l075", gate=1.2)[0]
        w = A1[(A1.window == win) & (A1.timing == "final")]
        bw["B1c_all_pairs_uniform_roi"] = float(w.D.sum() / sum(w[f"nb_all{k}"].sum() for k in range(8)))
        base[win] = bw
    r1["baselines_and_sensitivity"] = {k: {kk: (vv if isinstance(vv, float) else {
        "n_bets": vv.get("n_bets"), "hits": vv.get("hits"),
        "roi": vv.get("roi", {}).get("point") if vv.get("roi") else None,
        "roi_ci": vv.get("roi", {}).get("ci") if vv.get("roi") else None,
        "theta": vv["theta_matched_parent"]["theta"], "theta_ci": vv["theta_matched_parent"]["ci"],
        "takeout_ratio": vv.get("takeout_null_ratio", {}).get("point") if vv.get("takeout_null_ratio") else None,
        "expected_hits": vv.get("expected_hits")}) for kk, vv in v.items()} for k, v in base.items()}
    # B2: S3 win bets in races where the rule bets (confirm, <= cutoff)
    wconf = A1[(A1.window == "confirm") & (A1.timing == "final") & (A1.ens_ev > 1.2)]
    s3 = d[d.S3 & d.race_id.isin(set(wconf.race_id)) & ~d.dead_heat]
    s3_ret = np.where(s3.won, s3.odds, 0.0)
    r1["B2_S3_win_in_rule_races"] = {"n_races_with_S3": int(s3.race_id.nunique()), "n_bets": int(len(s3)),
                                     "hits": int(s3.won.sum()), "roi": float(s3_ret.sum() / max(len(s3), 1))}
    rr = wconf[wconf.race_id.isin(set(s3.race_id))]
    r1["B2_rule_in_same_races"] = {"n_bets": int(len(rr)), "hits": int(rr.ens_hit.sum()),
                                   "roi": float(np.where(rr.ens_hit, rr.D, 0).sum() / max(len(rr), 1))}
    res["arm_i"] = r1
    log("arm i discovery theta", disc["theta_matched_parent"]["theta"], "confirm", conf["theta_matched_parent"], "p", p_i)

    # ---------------- arm (ii)
    H2, ex2 = build_arm_ii(d, canc_set, gq)
    H2["ret"] = np.where(H2.won, H2.odds, 0.0)
    H2.to_parquet(OUT / "arm_ii_horses.parquet")
    log("arm ii horses", len(H2), ex2)
    r2 = {"exclusions": ex2}
    # stage A (outcome-free)
    stageA = {}
    for tname, tsel in (("final_all", H2.timing == "final"), ("pre", H2.timing == "pre")):
        x = H2[tsel & H2.race_pred_ok].copy()
        x["lv"] = np.log(x.v)
        band = (x.odds >= 20) & (x.odds < 40)
        a = x[band]
        # S1 vs non-S1 in band, race-day bootstrap via shared counts (vectorised)
        days = sorted(x.race_date.unique())
        di = {k: j for j, k in enumerate(days)}
        nd = len(days)
        W = block_bootstrap_counts(nd, 2000, SEED).astype(float)
        dk = a.race_date.map(di).to_numpy()
        agg = np.zeros((nd, 4))
        s1 = a.S1.to_numpy()
        np.add.at(agg[:, 0], dk[s1], a.lv.to_numpy()[s1]); np.add.at(agg[:, 1], dk[s1], 1.0)
        np.add.at(agg[:, 2], dk[~s1], a.lv.to_numpy()[~s1]); np.add.at(agg[:, 3], dk[~s1], 1.0)
        tot = agg.sum(0)
        point = tot[0] / tot[1] - tot[2] / tot[3] if tot[1] > 0 else float("nan")
        bs = W @ agg
        with np.errstate(divide="ignore", invalid="ignore"):
            reps = bs[:, 0] / bs[:, 1] - bs[:, 2] / bs[:, 3]
        stageA[tname] = {"S1_minus_nonS1_band20_40": {"point": float(point),
                                                      "ci": [float(np.nanpercentile(reps, 2.5)), float(np.nanpercentile(reps, 97.5))] if tot[1] > 0 else None,
                                                      "n_S1": int(a.S1.sum()), "n_nonS1": int((~a.S1).sum()),
                                                      "mean_lv_S1": float(a.lv[a.S1].mean()) if a.S1.any() else None,
                                                      "mean_lv_nonS1": float(a.lv[~a.S1].mean())}}
        # S3 vs non-S3 matched on odds band
        x["sb"] = x.odds.map(sband)
        dkx = x.race_date.map(di).to_numpy()
        sbx = x.sb.to_numpy()
        s3m = x.S3.to_numpy()
        lv = x.lv.to_numpy()
        nb_ = len(SBANDS) - 1
        A3 = np.zeros((nd, nb_, 4))
        np.add.at(A3[:, :, 0], (dkx[s3m], sbx[s3m]), lv[s3m]); np.add.at(A3[:, :, 1], (dkx[s3m], sbx[s3m]), 1.0)
        np.add.at(A3[:, :, 2], (dkx[~s3m], sbx[~s3m]), lv[~s3m]); np.add.at(A3[:, :, 3], (dkx[~s3m], sbx[~s3m]), 1.0)

        def s3stat(T):  # T: (..., nb_, 4)
            with np.errstate(divide="ignore", invalid="ignore"):
                diff = T[..., 0] / T[..., 1] - T[..., 2] / T[..., 3]
                w = np.where((T[..., 1] > 0) & (T[..., 3] > 0), T[..., 1], 0.0)
                diff = np.where(w > 0, diff, 0.0)
                return (w * diff).sum(-1) / w.sum(-1)
        point3 = float(s3stat(A3.sum(0)))
        B3 = np.einsum("bd,dkc->bkc", W, A3)
        reps3 = s3stat(B3)
        stageA[tname]["S3_minus_nonS3_bandmatched"] = {"point": point3, "ci": [float(np.nanpercentile(reps3, 2.5)), float(np.nanpercentile(reps3, 97.5))],
                                                       "n_S3": int(x.S3.sum())}
        stageA[tname]["v_quantiles_all"] = [float(t) for t in np.quantile(x.v, [0.1, 0.25, 0.5, 0.75, 0.9])]
        stageA[tname]["v_median_by_sband"] = {int(b_): float(g.v.median()) for b_, g in x.groupby("sb")}
    r2["stage_A"] = stageA
    log("stage A", json.dumps(stageA)[:600])
    base_sel = (H2.timing == "final") & ~H2.dead_heat
    dsel = base_sel & (H2.race_date <= "2024-12-31")
    csel = base_sel & (H2.race_date >= "2025-01-01") & (H2.race_date <= C.WIN_CUTOFF)
    r2["S"] = {}
    s_dir = {}
    for srule, sd in (("S3", 20261004), ("S1", 20261005)):
        dres, _ = arm_ii_eval(H2, srule, dsel, s=1, seed=sd)
        s = 1 if dres["delta"] >= 0 else -1
        s_dir[srule] = s
        cres, _ = arm_ii_eval(H2, srule, csel, s=s, seed=sd)
        r2["S"][srule] = {"discovery": dres, "direction": s, "confirm": cres}
        log("arm ii", srule, "disc delta", dres["delta"], "conf", cres.get("delta"), cres.get("perm"))
    # parent diagnostic: 20-40 band all horses
    band_all = (H2.odds >= 20) & (H2.odds < 40)
    H2["B2040"] = band_all
    r2["parent_band20_40_all_horses"] = {
        "discovery": arm_ii_eval(H2, "B2040", dsel, s=None, boot=True)[0],
        "confirm": arm_ii_eval(H2, "B2040", csel, s=None, boot=True)[0]}
    # pre-race check settlement
    aff = pd.read_csv(C.R05 / "affected_races.csv", dtype={"race_id": str})
    arch = pd.read_csv(C.R05 / "archived_final_odds.csv", dtype={"race_id": str})
    tclass = dict(zip(aff.race_id, aff.timing_class))
    arch_ok = set(aff.race_id[aff.archive_tansho_ok.astype(bool)])
    arch_odds = {(r.race_id, int(r.horse_number)): r.result_page_odds for r in arch.itertuples()}
    pre = H2[(H2.timing == "pre") & ~H2.dead_heat].copy()
    settle = []
    for r in pre.itertuples():
        if r.race_id in arch_ok and (r.race_id, r.horse_number) in arch_odds:
            o = arch_odds[(r.race_id, r.horse_number)]
            settle.append(o if r.won else 0.0)
        elif tclass.get(r.race_id) == "FILLED_AFTER_RESULTS":
            settle.append(r.odds if r.won else 0.0)
        else:
            settle.append(np.nan)
    pre["ret_pre"] = settle
    pre_ok = pre[pre.ret_pre.notna()].copy()
    H2pre = pre_ok
    r2["pre_race"] = {"n_races_settleable": int(pre_ok.race_id.nunique()), "n_races_pre_total": int(pre.race_id.nunique())}
    for srule in ("S3", "S1"):
        pr, _ = arm_ii_eval(H2pre, srule, np.ones(len(H2pre), bool), settle_col="ret_pre", s=None, boot=False)
        pr["direction_agrees"] = (None if pr.get("n", 0) == 0 or pr.get("n_keep", 0) == 0 else bool((pr["delta"] >= 0) == (s_dir[srule] > 0)))
        r2["pre_race"][srule] = pr
    res["arm_ii"] = r2

    # ---------------- arm (iii)
    H3, ex3 = build_arm_iii(d, canc_set, gt, dv)
    H3["window"] = H3.race_date.map(lambda s: window_of(s, "iii"))
    H3.to_parquet(OUT / "arm_iii_horses.parquet")
    log("arm iii horses", len(H3), ex3)
    r3 = {"exclusions": ex3,
          "n_races_by_window_timing": {f"{a}|{b}": int(v) for (a, b), v in H3.groupby(["window", "timing"]).race_id.nunique().items()}}
    disc3 = H3[(H3.window == "discovery") & (H3.timing == "final")]
    cuts = {int(b_): (float(g.r.quantile(0.25)), float(g.r.quantile(0.75))) for b_, g in disc3.groupby("band")}
    r3["cut_points"] = cuts
    dres3 = arm_iii_eval(H3, (H3.window == "discovery") & (H3.timing == "final"), cuts, s=1)
    s3d = 1 if dres3["delta"] >= 0 else -1
    cres3 = arm_iii_eval(H3, (H3.window == "confirm") & (H3.timing == "final"), cuts, s=s3d)
    r3["discovery"] = dres3
    r3["direction"] = s3d
    r3["confirm"] = cres3
    r3["post_final"] = arm_iii_eval(H3, (H3.window == "post") & (H3.timing == "final"), cuts)
    pre3 = arm_iii_eval(H3, H3.timing == "pre", cuts)
    pre3["direction_agrees"] = bool((pre3["delta"] >= 0) == (s3d > 0)) if pre3.get("n_top") else None
    r3["pre_race"] = pre3
    res["arm_iii"] = r3
    log("arm iii disc delta", dres3["delta"], "conf", cres3["delta"], cres3.get("perm"))

    # ---------------- leak diagnostics
    H2["top2f"] = H2.top2.astype(float)
    g2 = {"final_2024": (H2.timing == "final") & (H2.race_date <= "2024-12-31") & ~H2.dead_heat,
          "final_2025_2026-06-26": (H2.timing == "final") & (H2.race_date >= "2025-01-01") & (H2.race_date <= C.WIN_CUTOFF) & ~H2.dead_heat,
          "final_2026-06-27+": (H2.timing == "final") & (H2.race_date > C.WIN_CUTOFF) & ~H2.dead_heat,
          "pre_2026-08+": (H2.timing == "pre") & ~H2.dead_heat}
    H3["placedf"] = H3.placed.astype(float)
    g3 = {"final_2025": (H3.timing == "final") & (H3.window == "discovery"),
          "final_2026-01-01..06-26": (H3.timing == "final") & (H3.window == "confirm"),
          "final_2026-06-27+": (H3.timing == "final") & (H3.window == "post"),
          "pre_2026-08+": H3.timing == "pre"}
    res["leak_diagnostic"] = {"top2_x_vs_h2q": leak_diag(H2, "x", "h2", "top2f", g2),
                              "top3_P3T_vs_P3W": leak_diag(H3, "P3T", "P3W", "placedf", g3)}
    log("leak", json.dumps(res["leak_diagnostic"])[:800])

    # ---------------- Holm
    prim = {"i": p_i, "ii_S3": r2["S"]["S3"]["confirm"].get("perm", {}).get("p"),
            "ii_S1": r2["S"]["S1"]["confirm"].get("perm", {}).get("p"),
            "iii": cres3.get("perm", {}).get("p")}
    order = sorted([k for k in prim if prim[k] is not None], key=lambda k: prim[k])
    holm = {}
    m = len(order)
    stop = False
    for rank, k in enumerate(order):
        thr = 0.05 / (m - rank)
        rej = (not stop) and prim[k] <= thr
        if not rej:
            stop = True
        holm[k] = {"p": prim[k], "threshold": thr, "rejected": bool(rej)}
    res["holm"] = holm
    meaning = {"i": s_i > 0, "ii_S3": s_dir["S3"] > 0, "ii_S1": s_dir["S1"] > 0, "iii": s3d > 0}
    res["direction_means_beats_parent"] = meaning
    res["run_log"] = LOG
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    (OUT / "run.log").write_text("\n".join(LOG))
    log("done")


if __name__ == "__main__":
    main()
