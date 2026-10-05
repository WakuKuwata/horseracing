"""R04_delta_r2: information vs the market (Benter ΔR², winner NLL) for q / mev / ens15 / production p.

Pre-registration: artifacts/roi_explore/missed_20261004/R04_delta_r2/prereg.json (sha256 in prereg.sha256).
Vectorised re-implementation of eval/delta_r2.evaluate_delta_r2 (same objective: race-internal softmax of
sum_k theta_k log x_k, unregularised MLE, prequential calendar-year blocks, first block fit-only, EPS floor then
renormalise) extended to k components for nested increments. Parity vs the unchanged repo instrument is checked
in the report step (repo_instrument_{ens,mev}.json).

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r04_delta_r2.py main
    cd training && uv run python ../scripts/roi_explore/missed_20261004/r04_delta_r2.py null --reps 200 --workers 5
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import time
import pathlib
from multiprocessing import get_context

import numpy as np
import pandas as pd
from scipy.optimize import minimize

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r04_common as C  # noqa: E402
from horseracing_eval.bootstrap import (  # noqa: E402
    block_bootstrap_counts, race_block_ratio_bootstrap_ci_v1, race_day_cluster_ratio_bootstrap_ci_v1)

EPS = 1e-15
B, BSEED, ALPHA = 2000, 20260729, 0.05
NULL_SEED0 = 20261004


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# --------------------------------------------------------------------------------------------- engine
class Engine:
    """Horses sorted by (race_date, race_id); races contiguous. Components are log-probs after floor+renorm."""

    def __init__(self, d: pd.DataFrame, comps: dict[str, str]):
        rid = d.race_id.to_numpy()
        new = np.r_[True, rid[1:] != rid[:-1]]
        self.starts = np.flatnonzero(new)
        self.n_r = len(self.starts)
        self.lens = np.diff(np.r_[self.starts, len(d)])
        self.ridx = np.repeat(np.arange(self.n_r), self.lens)
        self.race_id = rid[self.starts]
        self.day = d.race_date.to_numpy()[self.starts]
        self.year = d.year.to_numpy()[self.starts].astype(int)
        if not (np.all(self.day[1:] >= self.day[:-1])):
            raise ValueError("races not date-sorted")
        won = d.won.to_numpy().astype(bool)
        wpos = np.flatnonzero(won)
        if len(wpos) != self.n_r or not np.array_equal(self.ridx[wpos], np.arange(self.n_r)):
            raise ValueError("exactly one winner per race required")
        self.win_local = wpos - self.starts[self.ridx[wpos]]
        self.logN = np.log(self.lens.astype(float))
        self.odds = d.odds.to_numpy(float)
        self.dsl = d.days_since_last.to_numpy(float)
        self.L = {}
        self.n_floored = {}
        for name, col in comps.items():
            a = d[col].to_numpy(float)
            if not np.all(np.isfinite(a)) or np.any(a < 0) or np.any(a > 1 + 1e-9):
                raise ValueError(f"{name}: invalid probabilities")
            self.n_floored[name] = int((a < EPS).sum())
            a = np.maximum(a, EPS)
            s = np.add.reduceat(a, self.starts)
            self.L[name] = np.log(a / s[self.ridx])
        self.blocks = sorted(set(self.year.tolist()))
        self.set_winners(self.win_local)

    def set_winners(self, win_local):
        self.win_local = np.asarray(win_local)
        self.win = self.starts + self.win_local
        self.y = np.zeros(len(self.ridx), dtype=bool)
        self.y[self.win] = True

    def X(self, names):
        return np.column_stack([self.L[n] for n in names])

    def _nll_grad(self, theta, X, starts, ridx, win):
        z = X @ theta
        m = np.maximum.reduceat(z, starts)
        e = np.exp(z - m[ridx])
        s = np.add.reduceat(e, starts)
        lse = m + np.log(s)
        n = len(starts)
        nll = float((lse - z[win]).sum()) / n
        w = e / s[ridx]
        grad = (X.T @ w - X[win].sum(axis=0)) / n
        return nll, grad

    def fit(self, names, race_hi):
        """MLE of theta on races [0, race_hi)."""
        h_hi = self.starts[race_hi] if race_hi < self.n_r else len(self.ridx)
        X = self.X(names)[:h_hi]
        starts = self.starts[:race_hi]
        ridx = self.ridx[:h_hi]
        win = self.win[:race_hi]
        x0 = np.ones(len(names))
        res = minimize(self._nll_grad, x0, args=(X, starts, ridx, win), jac=True, method="L-BFGS-B",
                       options={"maxiter": 2000, "ftol": 1e-15, "gtol": 1e-10, "maxcor": 20})
        return res.x, bool(res.success), int(res.nit)

    def race_probs(self, names, theta, r_lo, r_hi):
        h_lo = self.starts[r_lo]
        h_hi = self.starts[r_hi] if r_hi < self.n_r else len(self.ridx)
        z = self.X(names)[h_lo:h_hi] @ theta
        st = self.starts[r_lo:r_hi] - h_lo
        rx = self.ridx[h_lo:h_hi] - r_lo
        m = np.maximum.reduceat(z, st)
        e = np.exp(z - m[rx])
        s = np.add.reduceat(e, st)
        lse = m + np.log(s)
        nll = lse - z[self.win[r_lo:r_hi] - h_lo]
        c = e / s[rx]
        return nll, c

    def prequential(self, models: dict[str, list[str] | None], fixed: dict | None = None):
        """Returns per-race NLL arrays (scored races only, i.e. block > first) + fits + horse-level probs.

        models: name -> component list (fit) ; fixed: name -> (component list, theta) not fit (e.g. raw q).
        """
        fixed = fixed or {}
        first = self.blocks[0]
        scored_lo = int(np.searchsorted(self.year, first, side="right"))
        nll = {k: np.empty(self.n_r - scored_lo) for k in list(models) + list(fixed)}
        prob = {k: np.empty(len(self.ridx) - self.starts[scored_lo]) for k in list(models) + list(fixed)}
        fits = []
        for blk in self.blocks[1:]:
            lo = int(np.searchsorted(self.year, blk, side="left"))
            hi = int(np.searchsorted(self.year, blk, side="right"))
            if self.day[lo - 1] >= self.day[lo]:
                raise ValueError("fit window overlaps scored window by day")
            hlo = self.starts[lo] - self.starts[scored_lo]
            hhi = (self.starts[hi] if hi < self.n_r else len(self.ridx)) - self.starts[scored_lo]
            rec = {"block": int(blk), "n_fit_races": lo, "fit_through_day": str(self.day[lo - 1])}
            for k, names in models.items():
                th, ok, nit = self.fit(names, lo)
                rec[k] = {"theta": th.tolist(), "converged": ok, "nit": nit}
                a, c = self.race_probs(names, th, lo, hi)
                nll[k][lo - scored_lo:hi - scored_lo] = a
                prob[k][hlo:hhi] = c
            for k, (names, th) in fixed.items():
                a, c = self.race_probs(names, np.asarray(th, float), lo, hi)
                nll[k][lo - scored_lo:hi - scored_lo] = a
                prob[k][hlo:hhi] = c
            fits.append(rec)
        return {"scored_lo": scored_lo, "nll": nll, "prob": prob, "fits": fits}


# --------------------------------------------------------------------------------------------- stats
def per_day(values, days):
    """Sum per unique day (days sorted). Returns (day_keys, sums)."""
    keys, inv = np.unique(days, return_inverse=True)
    return keys, np.bincount(inv, weights=values, minlength=len(keys))


def ratio_ci(num_r, den_r, days_r, *, instrument=False):
    keys, num = per_day(num_r, days_r)
    _, den = per_day(den_r, days_r)
    if instrument:
        nd = {}
        dd = {}
        for x, y, dk in zip(num_r, den_r, days_r):
            nd.setdefault(dk, []).append(float(x))
            dd.setdefault(dk, []).append(float(y))
        ci = race_day_cluster_ratio_bootstrap_ci_v1(nd, dd, b=B, seed=BSEED, alpha=ALPHA)
        return {"point": ci.point, "ci_low": ci.ci_low, "ci_high": ci.ci_high, "n_days": ci.n_days}
    r = race_block_ratio_bootstrap_ci_v1(num, den, list(keys), b=B, seed=BSEED, alpha=ALPHA)
    return {"point": float(r.point[0]), "ci_low": float(r.ci_low[0]), "ci_high": float(r.ci_high[0]),
            "n_days": int(len(keys)), "_reps": r.replicates[0]}


def year_weighted(num_r, den_r, days_r, years_r):
    keys, num = per_day(num_r, days_r)
    _, den = per_day(den_r, days_r)
    kyear = np.array([int(k[:4]) for k in keys])
    counts = block_bootstrap_counts(len(keys), B, BSEED).astype(float)  # (B, n_days) same draws as ratio_ci
    ys = sorted(set(kyear.tolist()))
    pts, reps = [], []
    for y in ys:
        idx = kyear == y
        pts.append(num[idx].sum() / den[idx].sum())
        rn = counts[:, idx] @ num[idx]
        rd = counts[:, idx] @ den[idx]
        with np.errstate(divide="ignore", invalid="ignore"):
            reps.append(np.where(rd > 0, rn / rd, np.nan))
    reps = np.nanmean(np.vstack(reps), axis=0)
    return {"point": float(np.mean(pts)), "ci_low": float(np.nanpercentile(reps, 2.5)),
            "ci_high": float(np.nanpercentile(reps, 97.5)), "n_years": len(ys)}


def strip(x):
    if isinstance(x, dict):
        return {k: strip(v) for k, v in x.items() if not k.startswith("_")}
    if isinstance(x, list):
        return [strip(v) for v in x]
    return x


# --------------------------------------------------------------------------------------------- mixture
def reassemble_mix130(d: pd.DataFrame, dsl_source: str = "rows") -> np.ndarray:
    """Preweight mix-129 win prob from the 130 caches + frozen annual coefficients (prereg)."""
    frozen = json.loads((C.W130 / "run-freeze.json").read_text())
    coefs = frozen["coefficients"]
    hist = pd.read_parquet(C.ROWS, columns=["horse_id", "race_date"])
    hist = hist[hist.race_date >= "2007-01-01"]
    dates = {h: np.unique(g.race_date.to_numpy().astype("datetime64[D]")) for h, g in hist.groupby("horse_id")}
    sub_idx = np.flatnonzero(d.year.to_numpy() >= 2020)
    sub = d.iloc[sub_idx]
    if dsl_source == "rows":
        gap = sub.days_since_last.to_numpy(float)
    else:
        fp = pd.read_parquet(C.ROOT / "artifacts/features.parquet", columns=["race_id", "horse_id", "days_since_last"])
        gap = sub[["race_id", "horse_id"]].merge(fp, how="left", on=["race_id", "horse_id"]).days_since_last.to_numpy(float)
    day64 = sub.race_date.to_numpy().astype("datetime64[D]")
    prior = np.full(len(sub), np.nan)
    for i, (h, dy) in enumerate(zip(sub.horse_id.to_numpy(), day64)):
        prev = dates.get(h)
        if prev is None:
            continue
        pos = int(np.searchsorted(prev, dy, side="left"))
        if pos >= 2:
            prior[i] = float((prev[pos - 1] - prev[pos - 2]) / np.timedelta64(1, "D"))
    rd = pd.to_datetime(sub.race_date)
    theta = 2 * np.pi * ((rd.dt.dayofyear.to_numpy() - 1) / np.where(rd.dt.is_leap_year, 366., 365.))
    sex = sub.sex.astype(object)
    female = np.where(sex.isna(), np.nan, (sex == "牝").to_numpy(dtype=float))
    terms_all = np.column_stack([np.log1p(gap), np.log1p(prior), female * np.sin(theta), female * np.cos(theta)])
    h0 = np.nan_to_num(terms_all, nan=0.0)
    rid = sub.race_id.to_numpy()
    starts = np.flatnonzero(np.r_[True, rid[1:] != rid[:-1]])
    ridx = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, len(sub)]))
    years = sub.year.to_numpy()
    caches = {}
    out = np.zeros(len(sub))
    for label, branch, seed in C.MIX_MEMBERS:
        base = np.empty(len(sub))
        beta = np.empty((len(sub), 5))
        for y in range(2020, 2027):
            m = years == y
            c = pickle.load(open(C.W130 / "cache" / f"{label}-{seed}-{y}.pkl", "rb"))["predictions"]["preweight"]
            base[m] = [c[r][h][0] for r, h in zip(rid[m], sub.horse_id.to_numpy()[m])]
            cf = coefs[f"{label}-{seed}"][str(y)]
            beta[m] = (cf + [0.0] * (5 - len(cf))) if branch == "pruning" else [cf[0], 0, 0, 0, 0]
        lp = np.log(base)
        mlp = np.add.reduceat(lp, starts) / np.diff(np.r_[starts, len(sub)])
        H = np.column_stack([h0, lp - mlp[ridx]])
        if branch != "pruning":
            H[:, 1:] = 0.0
        z = (H * beta).sum(axis=1)
        zm = np.maximum.reduceat(z, starts)
        e = base * np.exp(z - zm[ridx])
        q = e / np.add.reduceat(e, starts)[ridx]
        out += q / 6.0
    full = np.full(len(d), np.nan)
    full[sub_idx] = out
    return full


def mix_parity(d: pd.DataFrame, pmix: np.ndarray) -> dict:
    ev = json.loads(C.EVID130.read_text())["rows"]
    ref = {r["race_id"]: (r["preweight_candidate_nll"], r["winner"]) for r in ev}
    sub = d[np.isfinite(pmix)].assign(pm=pmix[np.isfinite(pmix)])
    diffs = []
    for rid, g in sub.groupby("race_id", sort=False):
        if rid not in ref:
            continue
        wnll, wid = ref[rid]
        row = g[g.horse_id == wid]
        if len(row) != 1:
            diffs.append(np.inf)
            continue
        diffs.append(abs(-math.log(float(row.pm.iloc[0])) - wnll))
    diffs = np.asarray(diffs)
    return {"n_overlap": int(len(diffs)), "share_le_1e-6": float(np.mean(diffs <= 1e-6)),
            "median_absdiff": float(np.median(diffs)), "max_absdiff": float(np.max(diffs)),
            "pass": bool(np.mean(diffs <= 1e-6) >= 0.995)}


# --------------------------------------------------------------------------------------------- analysis
FULL_MODELS = {"M0": ["q"], "ens": ["q", "ens"], "mev": ["q", "mev"], "prod": ["q", "prod"],
               "ens_prod": ["q", "ens", "prod"], "ens_mev": ["q", "ens", "mev"]}
FULL_FIXED = {"q_raw": (["q"], [1.0]), "ens_raw": (["ens"], [1.0]), "mev_raw": (["mev"], [1.0]),
              "prod_raw": (["prod"], [1.0])}
SUB_MODELS = {"M0": ["q"], "ens": ["q", "ens"], "mix": ["q", "mix"], "ens_mix": ["q", "ens", "mix"],
              "anc": ["q", "anc"], "ens_anc": ["q", "ens", "anc"]}
SUB_FIXED = {"q_raw": (["q"], [1.0]), "mix_raw": (["mix"], [1.0])}
FULL_INCR = {"ens|q": ("M0", "ens"), "mev|q": ("M0", "mev"), "prod|q": ("M0", "prod"),
             "prod|q,ens": ("ens", "ens_prod"), "ens|q,mev": ("mev", "ens_mev"), "ens_vs_mev": ("mev", "ens"),
             "ens|q_literal": ("q_raw", "ens"), "mev|q_literal": ("q_raw", "mev"), "M0|q_raw": ("q_raw", "M0")}
SUB_INCR = {"mix|q,ens": ("ens", "ens_mix"), "anc|q,ens": ("ens", "ens_anc"), "mix|q": ("M0", "mix"),
            "anc|q": ("M0", "anc"), "ens|q(2021+)": ("M0", "ens")}
NULL_FULL = {"ens|q", "mev|q", "prod|q", "prod|q,ens"}
NULL_SUB = {"mix|q,ens", "anc|q,ens", "mix|q"}


def increments(eng, pre, incr, *, with_ci=True, instrument_keys=()):
    sl = pre["scored_lo"]
    days = eng.day[sl:]
    years = eng.year[sl:]
    logn = eng.logN[sl:]
    out = {}
    for name, (red, full) in incr.items():
        num = pre["nll"][red] - pre["nll"][full]
        rec = {"reduced": red, "full": full}
        if with_ci:
            rec |= ratio_ci(num, logn, days)
            if name in instrument_keys:
                rec["instrument_bootstrap"] = ratio_ci(num, logn, days, instrument=True)
        else:
            keys, n = per_day(num, days)
            _, dd = per_day(logn, days)
            r = race_block_ratio_bootstrap_ci_v1(n, dd, list(keys), b=B, seed=BSEED, alpha=ALPHA)
            rec |= {"point": float(r.point[0]), "ci_low": float(r.ci_low[0]), "ci_high": float(r.ci_high[0])}
        rec["mean_winner_nll_diff"] = float(num.mean())  # reduced - full (positive = full better)
        out[name] = rec
    return out


def main_run():
    t0 = time.time()
    d, prov = C.load_population()
    d = C.attach_prod108(d)
    d = C.attach_anchor42_preweight(d)
    log(f"population {len(d)} rows")
    # mixture reassembly + parity gate (prereg)
    pmix = reassemble_mix130(d, "rows")
    par = mix_parity(d, pmix)
    par_src = "rows"
    log(f"mix parity (rows_2007 days_since_last): {par}")
    deviations = []
    if not par["pass"]:
        pmix2 = reassemble_mix130(d, "features")
        par2 = mix_parity(d, pmix2)
        log(f"mix parity retry (features.parquet): {par2}")
        if par2["pass"]:
            pmix, par, par_src = pmix2, par2, "features"
        else:
            deviations.append("mix130 parity failed twice -> P3 replaced by anchor42 preweight")
            par_src = "FAILED"
    d["p_mix"] = pmix
    full_comps = {"q": "q", "ens": "p_ens", "mev": "p_mev", "prod": "p_prod"}
    eng = Engine(d, full_comps)
    log(f"engine full: {eng.n_r} races; floored {eng.n_floored}")
    pre = eng.prequential(FULL_MODELS, FULL_FIXED)
    log(f"full prequential done {time.time()-t0:.0f}s")
    sl = pre["scored_lo"]
    logn = eng.logN[sl:]
    d0 = logn.sum()
    res = {"prereg_sha256": (C.OUT / "prereg.sha256").read_text().split()[0], "provenance": prov,
           "n_races_scored": int(eng.n_r - sl), "n_days_scored": int(len(set(eng.day[sl:]))),
           "scored_window": [str(eng.day[sl]), str(eng.day[-1])], "mean_log_field": float(logn.mean()),
           "n_floored": eng.n_floored, "mix_parity": par | {"dsl_source": par_src}, "deviations": deviations}
    res["R2"] = {k: 1.0 - float(v.sum()) / d0 for k, v in pre["nll"].items()}
    res["mean_winner_nll"] = {k: float(v.mean()) for k, v in pre["nll"].items()}
    res["increments"] = increments(eng, pre, FULL_INCR, instrument_keys=("ens|q", "mev|q"))
    res["fits"] = pre["fits"]
    # 2019+ pooled and year-weighted, yearly
    days, years = eng.day[sl:], eng.year[sl:]
    m19 = years >= 2019
    res["pooled_2019plus"] = {}
    res["year_weighted"] = {}
    yearly = []
    for name in ("ens|q", "mev|q", "prod|q", "prod|q,ens", "ens_vs_mev"):
        red, full = FULL_INCR[name]
        num = pre["nll"][red] - pre["nll"][full]
        res["pooled_2019plus"][name] = strip(ratio_ci(num[m19], logn[m19], days[m19]))
        res["year_weighted"][name] = year_weighted(num, logn, days, years)
        for y in sorted(set(years.tolist())):
            k = years == y
            ci = ratio_ci(num[k], logn[k], days[k])
            yearly.append({"increment": name, "year": int(y), "n_races": int(k.sum()), "point": ci["point"],
                           "ci_low": ci["ci_low"], "ci_high": ci["ci_high"],
                           "mean_winner_nll_diff": float(num[k].mean())})
    # yearly R2 of market alone (context: market efficiency drift)
    for y in sorted(set(years.tolist())):
        k = years == y
        yearly.append({"increment": "R2_M0", "year": int(y), "n_races": int(k.sum()),
                       "point": 1 - pre["nll"]["M0"][k].sum() / logn[k].sum()})
    # band analysis (20 <= odds < 40), outcome-independent selection
    hs = eng.starts[sl]
    odds = eng.odds[hs:]
    dsl = eng.dsl[hs:]
    yb = eng.y[hs:].astype(float)
    hday = eng.day[eng.ridx[hs:]]
    band = (odds >= 20) & (odds < 40)
    s1ctx = band & (dsl >= 14) & (dsl <= 112)
    q_raw_h = np.exp(eng.L["q"][hs:])
    pens_h = np.exp(eng.L["ens"][hs:])

    def bern(c):
        c = np.clip(c, 1e-15, 1 - 1e-15)
        return -(yb * np.log(c) + (1 - yb) * np.log(1 - c))

    res["band"] = {}
    for bname, bm in (("odds20_40", band), ("S1_context_odds20_40_gap14_112", s1ctx)):
        rec = {"n_horses": int(bm.sum()), "n_wins": int(yb[bm].sum())}
        for mname in ("ens", "mev", "prod"):
            dl = bern(pre["prob"]["M0"]) - bern(pre["prob"][mname])
            rec[f"bern_ll_gain_{mname}|q_per_horse"] = strip(ratio_ci(dl[bm], np.ones(bm.sum()), hday[bm]))
        for pname, p in (("q_raw", q_raw_h), ("M0", pre["prob"]["M0"]), ("M_ens", pre["prob"]["ens"]),
                         ("M_mev", pre["prob"]["mev"]), ("p_ens_raw", pens_h)):
            rec[f"calib_won_over_{pname}"] = strip(ratio_ci(yb[bm], p[bm], hday[bm]))
        res["band"][bname] = rec
    log(f"full analysis done {time.time()-t0:.0f}s")
    # 2020+ sub-engine (mix130 / anchor42), 2020 fit-only
    ds = d[d.year >= 2020].reset_index(drop=True)
    eng2 = Engine(ds, {"q": "q", "ens": "p_ens", "mix": "p_mix", "anc": "p_anchor42"})
    pre2 = eng2.prequential(SUB_MODELS, SUB_FIXED)
    sl2 = pre2["scored_lo"]
    logn2 = eng2.logN[sl2:]
    res["sub2020"] = {"n_races_scored": int(eng2.n_r - sl2), "scored_window": [str(eng2.day[sl2]), str(eng2.day[-1])],
                      "R2": {k: 1.0 - float(v.sum()) / logn2.sum() for k, v in pre2["nll"].items()},
                      "mean_winner_nll": {k: float(v.mean()) for k, v in pre2["nll"].items()},
                      "increments": increments(eng2, pre2, SUB_INCR), "fits": pre2["fits"]}
    days2, years2 = eng2.day[sl2:], eng2.year[sl2:]
    res["sub2020"]["year_weighted"] = {}
    for name in ("mix|q,ens", "anc|q,ens", "mix|q"):
        red, full = SUB_INCR[name]
        num = pre2["nll"][red] - pre2["nll"][full]
        res["sub2020"]["year_weighted"][name] = year_weighted(num, logn2, days2, years2)
        for y in sorted(set(years2.tolist())):
            k = years2 == y
            ci = ratio_ci(num[k], logn2[k], days2[k])
            yearly.append({"increment": name, "year": int(y), "n_races": int(k.sum()), "point": ci["point"],
                           "ci_low": ci["ci_low"], "ci_high": ci["ci_high"],
                           "mean_winner_nll_diff": float(num[k].mean())})
    log(f"sub2020 done {time.time()-t0:.0f}s")
    # per-race NLL evidence
    ev = pd.DataFrame({"race_id": eng.race_id[sl:], "race_date": eng.day[sl:], "year": eng.year[sl:],
                       "log_n": logn} | {f"nll_{k}": v for k, v in pre["nll"].items()})
    ev.to_parquet(C.OUT / "per_race_nll_full.parquet", index=False)
    ev2 = pd.DataFrame({"race_id": eng2.race_id[sl2:], "race_date": eng2.day[sl2:], "year": eng2.year[sl2:],
                        "log_n": logn2} | {f"nll_{k}": v for k, v in pre2["nll"].items()})
    ev2.to_parquet(C.OUT / "per_race_nll_sub2020.parquet", index=False)
    pd.DataFrame(yearly).to_csv(C.OUT / "yearly.csv", index=False)
    res["elapsed_s"] = time.time() - t0
    (C.OUT / "results_main.json").write_text(json.dumps(strip(res), indent=1, ensure_ascii=False))
    log(f"wrote results_main.json ({time.time()-t0:.0f}s)")


# --------------------------------------------------------------------------------------------- null
_G = {}


def _null_init():
    d, _ = C.load_population()
    d = C.attach_prod108(d)
    d = C.attach_anchor42_preweight(d)
    mix = json.loads((C.OUT / "results_main.json").read_text())["mix_parity"]
    d["p_mix"] = reassemble_mix130(d, "features" if mix["dsl_source"] == "features" else "rows")
    _G["eng"] = Engine(d, {"q": "q", "ens": "p_ens", "mev": "p_mev", "prod": "p_prod"})
    ds = d[d.year >= 2020].reset_index(drop=True)
    _G["eng2"] = Engine(ds, {"q": "q", "ens": "p_ens", "mix": "p_mix", "anc": "p_anchor42"})
    _G["rid2"] = ds.race_id.to_numpy()


def _draw(eng, rng):
    q = np.exp(eng.L["q"])
    cq = np.cumsum(q)
    base = np.r_[0.0, cq[eng.starts[1:] - 1]]
    tot = np.add.reduceat(q, eng.starts)
    u = rng.random(eng.n_r) * tot + base
    pos = np.searchsorted(cq, u, side="right")
    pos = np.clip(pos, eng.starts, eng.starts + eng.lens - 1)
    return pos - eng.starts


def _null_one(r):
    eng, eng2 = _G["eng"], _G["eng2"]
    rng = np.random.default_rng(NULL_SEED0 + r)
    wl = _draw(eng, rng)
    eng.set_winners(wl)
    # same null world on the 2020+ subset: map winners by race id
    off = int(np.searchsorted(eng.year, 2020, side="left"))
    if not np.array_equal(eng.race_id[off:], eng2.race_id):
        raise RuntimeError("sub engine race order differs")
    eng2.set_winners(wl[off:])
    models = {k: v for k, v in FULL_MODELS.items() if k in ("M0", "ens", "mev", "prod", "ens_prod")}
    pre = eng.prequential(models)
    inc = increments(eng, pre, {k: FULL_INCR[k] for k in NULL_FULL}, with_ci=False)
    models2 = {k: v for k, v in SUB_MODELS.items() if k in ("M0", "ens", "mix", "ens_mix", "ens_anc")}
    pre2 = eng2.prequential(models2)
    inc |= increments(eng2, pre2, {k: SUB_INCR[k] for k in NULL_SUB}, with_ci=False)
    return {"rep": r} | {f"{k}__{f}": v[f] for k, v in inc.items() for f in ("point", "ci_low", "ci_high")}


def null_run(reps, workers):
    t0 = time.time()
    out = C.OUT / "null_replicates.csv"
    done = set()
    if out.exists():
        done = set(pd.read_csv(out).rep.tolist())
    todo = [r for r in range(reps) if r not in done]
    log(f"null: {len(todo)} replicates to run with {workers} workers")
    ctx = get_context("fork")
    _null_init()
    with ctx.Pool(workers) as pool:
        for i, row in enumerate(pool.imap_unordered(_null_one, todo)):
            pd.DataFrame([row]).to_csv(out, mode="a", header=not out.exists(), index=False)
            if i % 10 == 0:
                log(f"null {i+1}/{len(todo)} ({time.time()-t0:.0f}s)")
    log(f"null done {time.time()-t0:.0f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["main", "null"])
    ap.add_argument("--reps", type=int, default=200)
    ap.add_argument("--workers", type=int, default=5)
    a = ap.parse_args()
    if a.cmd == "main":
        main_run()
    else:
        null_run(a.reps, a.workers)
