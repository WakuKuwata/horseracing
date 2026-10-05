"""R10_norm_meta — ens15 の選び方を見直す 2 本(事前登録: artifacts/roi_explore/missed_20261004/R10_norm_meta/prereg.json)。

V1: レース内正規化 EV_norm = (p̄/Σp̄)·odds で、年ごとに S3 と同じ件数を P(EV_ens>1.0)から選ぶ。
V2: logit p̄ を offset にした符号制約つき低容量ロジスティック(係数 5 本)で p̄ を補正し、同じく件数を揃えて選ぶ。
主指標: S3 との対称差の ROI 差 Δ = ROI(V\\S3) − ROI(S3\\V)(各年で件数一致)。
帰無: 層内(年×オッズ四分位)での増分の並べ替え / q から勝者を引き直した世界でのパイプライン全体 / V2 は入力シャッフル対照。

読み取り専用(DB 不使用・製品コード不変)。

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r10_norm_meta.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd
from horseracing_eval import attention_rules as ar
from horseracing_eval.bootstrap import centered_one_sided_p_from_replicates, race_block_ratio_bootstrap_ci_v1
from scipy.optimize import minimize
from scipy.special import expit

ROOT = pathlib.Path(__file__).resolve().parents[3]
ROWS = ROOT / "artifacts" / "market_ev" / "rows_2007.parquet"
RES = ROOT / "artifacts" / "roi_explore" / "results"
OUT = ROOT / "artifacts" / "roi_explore" / "missed_20261004" / "R10_norm_meta"
PREREG = OUT / "prereg.json"
ENS_RUNS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
SETTLE_MAX = "2026-06-26"
WINDOWS = {"W": ("2013-01-01", SETTLE_MAX), "Q": ("2019-01-01", SETTLE_MAX), "D": ("2013-01-01", "2018-12-31")}
APPLY_YEARS = list(range(2013, 2027))
B_BOOT, SEED_BOOT = 20000, 20260905
R_PERM, SEED_PERM = 1000, 20261004
R_Q, SEED_Q = 200, 20261005
R_SHUF, SEED_SHUF = 200, 20261006
BOUNDS = [(None, None), (None, 0.0), (0.0, None), (None, 0.0), (None, 0.0)]
FEATS = ["z1_seed_sd_resid", "z2_frac_seeds_ev_gt_1_2", "z3_abs_log_sum_p", "z4_within_race_disagreement"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def r6(x):
    if x is None:
        return None
    x = float(x)
    return None if not np.isfinite(x) else round(x, 6)


# ---------------------------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------------------------


def load() -> pd.DataFrame:
    d = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won",
                                       "days_since_last", "race_ok", "dead_heat"])
    for s, tag in ENS_RUNS.items():
        v = pd.read_parquet(RES / tag / "predictions.parquet")[["race_id", "horse_id", "pred"]]
        d = d.merge(v.rename(columns={"pred": f"pred{s}"}), on=["race_id", "horse_id"], how="left",
                    validate="one_to_one")
    d["race_date"] = d["race_date"].astype(str).str.slice(0, 10)
    d = d[d.race_ok & ~d.dead_heat & (d.year >= 2010) & (d.race_date <= SETTLE_MAX)]
    d = d.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    miss = int(d[[f"pred{s}" for s in ENS_RUNS]].isna().any(axis=1).sum())
    if miss:
        raise SystemExit(f"FAIL: {miss} rows lack a prediction")
    return d


def qcut_within(values: np.ndarray, groups: np.ndarray, k: int) -> np.ndarray:
    s = pd.Series(values)
    out = s.groupby(groups).transform(lambda x: pd.qcut(x.rank(method="first"), k, labels=False))
    return out.to_numpy(dtype=np.int64)


# ---------------------------------------------------------------------------------------------
# selection / statistics
# ---------------------------------------------------------------------------------------------


class Ctx:
    pass


def select_top(score_p: np.ndarray, ctx, n_by_year: dict, cand: np.ndarray | None = None) -> np.ndarray:
    """Top n_y rows of P per year by score (stable sort keeps race_id/horse_number ascending for ties)."""
    sel = np.zeros(ctx.nP, dtype=bool)
    for y in APPLY_YEARS:
        idx = ctx.p_year_idx[y]
        if cand is not None:
            idx = idx[cand[idx]]
        n = n_by_year.get(y, 0)
        if n == 0 or idx.size == 0:
            continue
        order = np.argsort(-score_p[idx], kind="stable")
        sel[idx[order[:n]]] = True
    return sel


def delta_stats(sel_p: np.ndarray, base_p: np.ndarray, pay_p: np.ndarray, ctx) -> dict:
    """Delta over W/Q/D and year-weighted Delta over W (selection restricted to apply years)."""
    a = sel_p & ~base_p
    b = base_p & ~sel_p
    out = {}
    for wn, wm in ctx.p_win.items():
        na, nb = (a & wm).sum(), (b & wm).sum()
        out[wn] = (pay_p[a & wm].sum() / (100 * na) - pay_p[b & wm].sum() / (100 * nb)) if na and nb else 0.0
    dy = []
    for y in APPLY_YEARS:
        ym = ctx.p_year == y
        na, nb = (a & ym).sum(), (b & ym).sum()
        dy.append((pay_p[a & ym].sum() / (100 * na) - pay_p[b & ym].sum() / (100 * nb)) if na and nb else 0.0)
    out["W_yw"] = float(np.mean(dy))
    return out


def permute_within(values: np.ndarray, strata: np.ndarray, base_order: np.ndarray, rng) -> np.ndarray:
    order = np.lexsort((rng.random(strata.size), strata))
    out = np.empty_like(values)
    out[base_order] = values[order]
    return out


# ---------------------------------------------------------------------------------------------
# V2 model
# ---------------------------------------------------------------------------------------------


def fit_logit(off: np.ndarray, X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, dict]:
    def f(b):
        eta = off + X @ b
        nll = -(y * eta - np.logaddexp(0.0, eta)).sum()
        g = -(X.T @ (y - expit(eta)))
        return nll, g

    res = minimize(f, np.zeros(X.shape[1]), jac=True, method="L-BFGS-B", bounds=BOUNDS,
                   options={"maxiter": 1000, "gtol": 1e-9})
    return res.x, {"converged": bool(res.success), "nit": int(res.nit), "nll": float(res.fun)}


def run_v2(y_p: np.ndarray, Z_p: np.ndarray, ctx, keep_coefs: bool = False):
    """Expanding refit per application year; returns EV_V2 over P (NaN for 2010-2012) and coefs."""
    score = np.full(ctx.nP, np.nan)
    coefs = []
    for Y in APPLY_YEARS:
        tr = (ctx.p_year >= 2010) & (ctx.p_year <= Y - 1)
        ap = ctx.p_year == Y
        if not ap.any():
            continue
        mu = Z_p[tr].mean(axis=0)
        sd = Z_p[tr].std(axis=0)
        sd[sd == 0] = 1.0
        Xtr = np.column_stack([np.ones(tr.sum()), (Z_p[tr] - mu) / sd])
        b, info = fit_logit(ctx.off_p[tr], Xtr, y_p[tr])
        Xap = np.column_stack([np.ones(ap.sum()), (Z_p[ap] - mu) / sd])
        eta = ctx.off_p[ap] + Xap @ b
        score[ap] = expit(eta) * ctx.odds_p[ap]
        if keep_coefs:
            active = [bool(abs(b[k]) < 1e-10) for k in range(1, 5)]
            coefs.append({"year": Y, "n_train": int(tr.sum()), "b0": r6(b[0]),
                          **{FEATS[k - 1]: r6(b[k]) for k in range(1, 5)},
                          "at_bound_zero": dict(zip(FEATS, active, strict=True)), **info})
    return score, coefs


# ---------------------------------------------------------------------------------------------
# bootstrap summaries
# ---------------------------------------------------------------------------------------------


def day_sums(mask: np.ndarray, pay: np.ndarray, days: np.ndarray, day_list: list) -> tuple[np.ndarray, np.ndarray]:
    pos = {d: i for i, d in enumerate(day_list)}
    num = np.zeros(len(day_list))
    den = np.zeros(len(day_list))
    idx = np.flatnonzero(mask)
    j = np.fromiter((pos[d] for d in days[idx]), dtype=np.int64, count=idx.size)
    np.add.at(num, j, pay[idx])
    np.add.at(den, j, 100.0)
    return num, den


def set_line(mask, pay, won, odds, q, years):
    n = int(mask.sum())
    if n == 0:
        return {"n": 0}
    yr = []
    for y in APPLY_YEARS:
        m = mask & (years == y)
        if m.any():
            yr.append(pay[m].sum() / (100 * m.sum()))
    return {"n": n, "hits": int(won[mask].sum()), "roi": r6(pay[mask].sum() / (100 * n)),
            "roi_year_weighted": r6(np.mean(yr)) if yr else None,
            "market_expected_roi": r6((q[mask] * odds[mask]).mean()), "mean_log_odds": r6(np.log(odds[mask]).mean()),
            "median_odds": r6(np.median(odds[mask]))}


def compare_block(sel_full: np.ndarray, base_full: np.ndarray, g, wmask: np.ndarray) -> dict:
    """Synchronised race-day bootstrap of [V\\S3, S3\\V, V, S3] in one window."""
    A = sel_full & ~base_full & wmask
    Bm = base_full & ~sel_full & wmask
    V = sel_full & wmask
    S = base_full & wmask
    uni = V | S
    day_list = sorted(set(g.days[uni].tolist()))
    nums, dens = [], []
    for m in (A, Bm, V, S):
        nu, de = day_sums(m, g.pay, g.days, day_list)
        nums.append(nu)
        dens.append(de)
    res = race_block_ratio_bootstrap_ci_v1(np.vstack(nums), np.vstack(dens), day_list, block="race_day", b=B_BOOT,
                                           seed=SEED_BOOT)
    out = {"n_days": len(day_list)}
    names = ["V_minus_S3", "S3_minus_V", "V", "S3"]
    for i, (nm, m) in enumerate(zip(names, (A, Bm, V, S), strict=True)):
        line = set_line(m, g.pay, g.won, g.odds, g.q, g.years)
        if line["n"]:
            line["ci95"] = [r6(res.ci_low[i]), r6(res.ci_high[i])]
            line["p_roi_gt_1"] = r6(centered_one_sided_p_from_replicates(res.replicates[i], float(res.point[i])))
        out[nm] = line
    d_obs = float(res.point[0] - res.point[1]) if (A.any() and Bm.any()) else 0.0
    reps = res.replicates[0] - res.replicates[1]
    fin = np.isfinite(reps)
    out["Delta"] = r6(d_obs)
    out["Delta_ci95"] = [r6(np.nanpercentile(reps, 2.5)), r6(np.nanpercentile(reps, 97.5))]
    out["p_boot"] = r6((1 + int(np.sum((reps[fin] - d_obs) >= d_obs))) / (B_BOOT + 1))
    out["overlap_V_and_S3_over_S3"] = r6((V & S).sum() / S.sum()) if S.any() else None
    out["roi_diff_V_minus_S3"] = r6(res.point[2] - res.point[3])
    return out


def null_summary(arr: np.ndarray) -> dict:
    a = np.asarray(arr, dtype=float)
    return {"mean": r6(a.mean()), "sd": r6(a.std(ddof=1)), "p2.5": r6(np.percentile(a, 2.5)),
            "p50": r6(np.percentile(a, 50)), "p97.5": r6(np.percentile(a, 97.5))}


def pval_ge(null: np.ndarray, obs: float) -> float:
    null = np.asarray(null, dtype=float)
    return (1 + int(np.sum(null >= obs))) / (null.size + 1)


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------


def main() -> int:
    t0 = time.time()
    prereg_sha = sha(PREREG)
    log(f"prereg sha256 {prereg_sha}")
    d = load()
    n = len(d)
    log(f"population rows {n}, races {d.race_id.nunique()}, days {d.race_date.nunique()}")

    g = Ctx()
    g.odds = d.odds.to_numpy(float)
    g.won = d.won.to_numpy(bool).astype(float)
    g.pay = g.won * g.odds * 100.0
    g.years = d.year.to_numpy(int)
    g.days = d.race_date.to_numpy()
    dsl = d.days_since_last.to_numpy(float)
    ev_s = np.stack([1.0 + d[f"pred{s}"].to_numpy(float) / 100.0 for s in ENS_RUNS])  # (15, n)
    ph_s = ev_s / g.odds
    p_bar = ph_s.mean(axis=0)
    ev_ens = p_bar * g.odds
    _, ridx = np.unique(d.race_id.to_numpy(), return_inverse=True)
    nr = ridx.max() + 1
    sum_p = np.bincount(ridx, weights=p_bar, minlength=nr)[ridx]
    inv = 1.0 / g.odds
    g.q = inv / np.bincount(ridx, weights=inv, minlength=nr)[ridx]
    ev_norm = p_bar / sum_p * g.odds

    s3 = ar.match_mask(ar.definition("S3"), ens_ev=ev_ens, single_ev=np.full(n, np.nan), odds=g.odds,
                       days_since_last=dsl)
    s1 = ar.match_mask(ar.definition("S1"), ens_ev=ev_ens, single_ev=np.full(n, np.nan), odds=g.odds,
                       days_since_last=dsl)
    in_apply = g.years >= 2013
    P = ev_ens > 1.0
    assert not (s3 & ~P).any()
    pidx = np.flatnonzero(P)

    # P-level context
    ctx = Ctx()
    ctx.nP = pidx.size
    ctx.p_year = g.years[pidx]
    ctx.odds_p = g.odds[pidx]
    ctx.off_p = np.log(np.clip(p_bar[pidx], 1e-6, 1 - 1e-6)) - np.log1p(-np.clip(p_bar[pidx], 1e-6, 1 - 1e-6))
    ctx.p_year_idx = {y: np.flatnonzero(ctx.p_year == y) for y in APPLY_YEARS}
    ctx.p_win = {}
    p_days = g.days[pidx]
    for wn, (lo, hi) in WINDOWS.items():
        ctx.p_win[wn] = (p_days >= lo) & (p_days <= hi)
    pay_p = g.pay[pidx]
    won_p = g.won[pidx]
    s3_p = s3[pidx]
    oq = qcut_within(ctx.odds_p, ctx.p_year, 4)
    odec = qcut_within(ctx.odds_p, ctx.p_year, 10)
    strata = ctx.p_year * 10 + oq
    base_order = np.argsort(strata, kind="stable")
    n_by_year = {y: int((s3 & (g.years == y)).sum()) for y in APPLY_YEARS}
    log(f"P rows {ctx.nP}; S3 apply-years rows {sum(n_by_year.values())}")

    # ---- V2 features (P rows)
    sdlog = np.log(ph_s[:, pidx]).std(axis=0, ddof=1)
    lev = np.log(ev_ens[pidx])
    z1 = np.empty(ctx.nP)
    cell = ctx.p_year * 100 + odec
    for c in np.unique(cell):
        m = cell == c
        Xc = np.column_stack([np.ones(m.sum()), lev[m]])
        beta, *_ = np.linalg.lstsq(Xc, sdlog[m], rcond=None)
        z1[m] = sdlog[m] - Xc @ beta
    z2 = (ev_s[:, pidx] > 1.2).sum(axis=0) / 15.0
    z3 = np.abs(np.log(sum_p[pidx]))
    dlt = np.log(p_bar / sum_p) - np.log(g.q)
    s2r = np.bincount(ridx, weights=dlt**2, minlength=nr)
    cnt = np.bincount(ridx, minlength=nr)
    z4_all = np.sqrt(np.maximum(s2r[ridx] - dlt**2, 0.0) / (cnt[ridx] - 1))
    z4 = z4_all[pidx]
    Z = np.column_stack([z1, z2, z3, z4])
    assert np.all(np.isfinite(Z))
    feat_desc = {f: {"mean": r6(Z[:, k].mean()), "sd": r6(Z[:, k].std()),
                     "corr_with_log_ev": r6(np.corrcoef(Z[:, k], lev)[0, 1])} for k, f in enumerate(FEATS)}

    # ---- observed selections
    incr_v1 = -np.log(sum_p[pidx])
    ev_norm_p = ev_norm[pidx]
    sel_v1 = select_top(ev_norm_p, ctx, n_by_year)
    score_v2, coefs = run_v2(won_p, Z, ctx, keep_coefs=True)
    incr_v2 = np.log(score_v2 / ctx.odds_p) - np.log(p_bar[pidx])
    sel_v2 = select_top(score_v2, ctx, n_by_year)
    obs = {"V1": delta_stats(sel_v1, s3_p, pay_p, ctx), "V2": delta_stats(sel_v2, s3_p, pay_p, ctx)}
    log(f"observed Delta V1 {obs['V1']['W']:.4f} V2 {obs['V2']['W']:.4f} ({time.time()-t0:.0f}s)")

    def to_full(sel_p):
        f = np.zeros(n, dtype=bool)
        f[pidx[sel_p]] = True
        return f

    full_sel = {"V1": to_full(sel_v1), "V2": to_full(sel_v2)}
    results = {"test_id": "R10_norm_meta", "prereg_sha256": prereg_sha, "script_sha256": sha(pathlib.Path(__file__)),
               "population": {"rows": n, "races": int(nr), "P_rows": int(ctx.nP),
                              "S3_rows_apply_years": int(sum(n_by_year.values())), "n_by_year": n_by_year,
                              "sum_p_bar_race_quantiles": {str(k): r6(v) for k, v in zip(
                                  [0.01, 0.05, 0.5, 0.95, 0.99],
                                  np.quantile(np.bincount(ridx, weights=p_bar, minlength=nr),
                                              [0.01, 0.05, 0.5, 0.95, 0.99]), strict=True)}},
               "v2_features": feat_desc, "v2_coefficients": coefs}

    # ---- bootstrap blocks for observed
    wm_full = {wn: (g.days >= lo) & (g.days <= hi) for wn, (lo, hi) in WINDOWS.items()}
    blocks = {}
    for v in ("V1", "V2"):
        blocks[v] = {wn: compare_block(full_sel[v], s3, g, wm_full[wn]) for wn in WINDOWS}
        blocks[v]["W"]["Delta_year_weighted"] = r6(obs[v]["W_yw"])
    # context: parent P and all rows over windows (bootstrap)
    ctxt = {}
    for wn in WINDOWS:
        line = {}
        for nm, m in (("P_ev_gt_1", P & wm_full[wn]), ("all_rows", wm_full[wn] & in_apply)):
            dl = sorted(set(g.days[m].tolist()))
            nu, de = day_sums(m, g.pay, g.days, dl)
            res = race_block_ratio_bootstrap_ci_v1(nu, de, dl, block="race_day", b=B_BOOT, seed=SEED_BOOT)
            ln = set_line(m, g.pay, g.won, g.odds, g.q, g.years)
            ln["ci95"] = [r6(res.ci_low[0]), r6(res.ci_high[0])]
            ln["p_roi_gt_1"] = r6(centered_one_sided_p_from_replicates(res.replicates[0], float(res.point[0])))
            line[nm] = ln
        ctxt[wn] = line
    results["context_sets"] = ctxt
    log(f"bootstrap done ({time.time()-t0:.0f}s)")

    # ---- null 1: stratified permutation of the increment
    rng = np.random.default_rng(SEED_PERM)
    ev_p = ev_ens[pidx]
    perm = {"V1": [], "V2": []}
    for r in range(R_PERM):
        for v, incr in (("V1", incr_v1), ("V2", incr_v2)):
            ip = permute_within(incr, strata, base_order, rng)
            sp = select_top(ev_p * np.exp(ip), ctx, n_by_year)
            perm[v].append(delta_stats(sp, s3_p, pay_p, ctx))
    log(f"perm null done ({time.time()-t0:.0f}s)")

    # ---- null 2: q-world (full pipeline)
    rng = np.random.default_rng(SEED_Q)
    starts = np.flatnonzero(np.r_[True, ridx[1:] != ridx[:-1]])
    ends = np.r_[starts[1:], n]
    cumq = np.cumsum(g.q)
    before = cumq[starts] - g.q[starts]
    qnull = {"V1": [], "V2": []}
    for r in range(R_Q):
        u = rng.random(starts.size)
        w = np.searchsorted(cumq, before + u, side="left")
        w = np.minimum(np.maximum(w, starts), ends - 1)
        won_sim = np.zeros(n)
        won_sim[w] = 1.0
        pay_sim_p = (won_sim * g.odds * 100.0)[pidx]
        qnull["V1"].append(delta_stats(sel_v1, s3_p, pay_sim_p, ctx))
        sc, _ = run_v2(won_sim[pidx], Z, ctx)
        qnull["V2"].append(delta_stats(select_top(sc, ctx, n_by_year), s3_p, pay_sim_p, ctx))
        if (r + 1) % 50 == 0:
            log(f"q-world {r+1}/{R_Q} ({time.time()-t0:.0f}s)")

    # ---- null 3: shuffled inputs (V2)
    rng = np.random.default_rng(SEED_SHUF)
    shuf = []
    for r in range(R_SHUF):
        order = np.lexsort((rng.random(ctx.nP), strata))
        Zs = np.empty_like(Z)
        Zs[base_order] = Z[order]
        sc, _ = run_v2(won_p, Zs, ctx)
        shuf.append(delta_stats(select_top(sc, ctx, n_by_year), s3_p, pay_p, ctx))
        if (r + 1) % 50 == 0:
            log(f"shuffle {r+1}/{R_SHUF} ({time.time()-t0:.0f}s)")

    # ---- decision
    def col(lst, key):
        return np.array([x[key] for x in lst], dtype=float)

    decision = {}
    for v in ("V1", "V2"):
        dW = obs[v]["W"]
        comp = {"p_boot": blocks[v]["W"]["p_boot"], "p_perm": r6(pval_ge(col(perm[v], "W"), dW)),
                "p_q": r6(pval_ge(col(qnull[v], "W"), dW))}
        if v == "V2":
            comp["p_shuf"] = r6(pval_ge(col(shuf, "W"), dW))
        comp["p_IUT"] = max(comp.values())
        nulls = {"perm": {wn: null_summary(col(perm[v], wn)) for wn in ("W", "Q", "D", "W_yw")},
                 "q_world": {wn: null_summary(col(qnull[v], wn)) for wn in ("W", "Q", "D", "W_yw")}}
        diag_p = {"perm_Q": r6(pval_ge(col(perm[v], "Q"), obs[v]["Q"])),
                  "q_world_Q": r6(pval_ge(col(qnull[v], "Q"), obs[v]["Q"]))}
        if v == "V2":
            nulls["shuffled_inputs"] = {wn: null_summary(col(shuf, wn)) for wn in ("W", "Q", "D", "W_yw")}
            diag_p["shuf_Q"] = r6(pval_ge(col(shuf, "Q"), obs[v]["Q"]))
        decision[v] = {"Delta_W": r6(dW), "Delta_Q": r6(obs[v]["Q"]), "Delta_D": r6(obs[v]["D"]),
                       "Delta_W_year_weighted": r6(obs[v]["W_yw"]), "component_p": comp, "nulls": nulls,
                       "diagnostic_p_Q_window": diag_p}
    ps = sorted(((decision[v]["component_p"]["p_IUT"], v) for v in ("V1", "V2")))
    holm = {}
    stop = False
    for k, (p, v) in enumerate(ps):
        thr = 0.05 / (2 - k)
        rej = (not stop) and p < thr
        if not rej:
            stop = True
        holm[v] = {"p_IUT": p, "threshold": thr, "rejected": bool(rej)}
    order_v = ["no_effect", "inconclusive", "loss_reduction_only", "improves_roi_candidate"]
    for v in ("V1", "V2"):
        dd = decision[v]
        consist = dd["Delta_Q"] > 0 and dd["Delta_W_year_weighted"] > 0
        roi_v = blocks[v]["W"]["V"]["roi"]
        if holm[v]["rejected"] and consist:
            verdict = "improves_roi_candidate" if roi_v > 1.0 else "loss_reduction_only"
        elif dd["Delta_W"] > 0 and consist:
            verdict = "inconclusive"
        else:
            verdict = "no_effect"
        dd["holm"] = holm[v]
        dd["consistency"] = bool(consist)
        dd["verdict"] = verdict
    overall = max((decision[v]["verdict"] for v in ("V1", "V2")), key=order_v.index)
    results["observed_blocks"] = blocks
    results["decision"] = decision
    results["overall_verdict"] = overall

    # ---- diagnostics
    diag = {}
    band = P & (g.odds >= 20) & (g.odds < 40) & (dsl >= 14) & (dsl <= 112)
    band_p = band[pidx]
    n1 = {y: int((s1 & (g.years == y)).sum()) for y in APPLY_YEARS}
    s1_p = s1[pidx]
    for nm, sc in (("V1_norm", ev_norm_p), ("V2", score_v2)):
        sp = select_top(sc, ctx, n1, cand=band_p)
        diag[f"S1_band_{nm}"] = {wn: {k: vv for k, vv in compare_block(to_full(sp), s1, g, wm_full[wn]).items()
                                      if k in ("Delta", "Delta_ci95", "p_boot", "V", "S3", "V_minus_S3",
                                               "S3_minus_V", "overlap_V_and_S3_over_S3", "n_days")}
                                 for wn in ("W", "Q")}
    # unrestricted V1 (all rows)
    unr = np.zeros(n, dtype=bool)
    for y in APPLY_YEARS:
        idx = np.flatnonzero(g.years == y)
        order = np.argsort(-ev_norm[idx], kind="stable")
        unr[idx[order[:n_by_year[y]]]] = True
    diag["V1_unrestricted"] = {
        "rows_outside_P": int((unr & ~P).sum()),
        "overlap_with_V1": r6((unr & full_sel["V1"]).sum() / unr.sum()),
        "W": {k: vv for k, vv in compare_block(unr, s3, g, wm_full["W"]).items()
              if k in ("Delta", "Delta_ci95", "p_boot", "V", "S3")}}
    # per-year table
    rows = []
    for y in APPLY_YEARS:
        ym = g.years == y
        rec = {"year": y, "n_S3": n_by_year[y]}
        for nm, m in (("S3", s3), ("V1", full_sel["V1"]), ("V2", full_sel["V2"])):
            mm = m & ym
            rec[f"{nm}_hits"] = int(g.won[mm].sum())
            rec[f"{nm}_roi"] = r6(g.pay[mm].sum() / (100 * mm.sum())) if mm.any() else None
        for v in ("V1", "V2"):
            a = full_sel[v] & ~s3 & ym
            b = s3 & ~full_sel[v] & ym
            rec[f"{v}_symdiff_n"] = int(a.sum())
            rec[f"{v}_only_roi"] = r6(g.pay[a].sum() / (100 * a.sum())) if a.any() else None
            rec[f"S3_only_vs_{v}_roi"] = r6(g.pay[b].sum() / (100 * b.sum())) if b.any() else None
            rec[f"{v}_Delta"] = r6((g.pay[a].sum() / (100 * a.sum()) - g.pay[b].sum() / (100 * b.sum()))
                                   if a.any() and b.any() else 0.0)
        rows.append(rec)
    pd.DataFrame(rows).to_csv(OUT / "per_year.csv", index=False)
    diag["per_year"] = rows
    results["diagnostics"] = diag
    results["runtime_sec"] = round(time.time() - t0, 1)
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    log(f"done: overall {overall}; V1 {decision['V1']['verdict']} V2 {decision['V2']['verdict']} "
        f"({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
