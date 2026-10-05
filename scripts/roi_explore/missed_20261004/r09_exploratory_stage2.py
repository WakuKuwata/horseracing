"""R09_new_info — 事前登録外の探索的診断(DEVIATION・検定ではない)。

段 1 で通過したセルは 0 本だったので、事前登録の規則では段 2 は走らない(規則は空)。本スクリプトは、段 1 で
3 窓とも符号が揃っていた X04a(栗東→東)/ X04b(美浦→西)を、もし段 2 に入れていたら S3 の買い目がどれだけ
入れ替わり、回収率がどう動いたかを、事前登録の段 2 と同じ手順で「参考として」測る。2019–2026H1 の L を見た後に
選んだセルなので、この数値は前向き登録の要否を決める材料にしかならない(利益の主張には使わない)。

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r09_exploratory_stage2.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r09_cells as cells  # noqa: E402
import r09_new_info as base  # noqa: E402

CELLS = ["X04a_ritto_to_east", "X04b_miho_to_west"]
WIN = (2019, 2026)
R_I = 2000
R_II = 1000
SEED = 20261006


def roi_ci(pay, dates, m):
    from horseracing_eval.bootstrap import centered_one_sided_p_from_replicates, race_block_ratio_bootstrap_ci_v1
    if not m.any():
        return {"n": 0}
    g = pd.DataFrame({"d": dates[m], "pay": pay[m]}).groupby("d", sort=True)["pay"].agg(["sum", "size"])
    br = race_block_ratio_bootstrap_ci_v1(g["sum"].to_numpy(float), 100.0 * g["size"].to_numpy(float), list(g.index),
                                          block="race_day", b=20000, seed=20260905)
    pt = float(br.point[0])
    return {"n": int(m.sum()), "hits": int((pay[m] > 0).sum()), "roi": pt,
            "ci95": [float(br.ci_low[0]), float(br.ci_high[0])],
            "p_roi_gt_1": float(centered_one_sided_p_from_replicates(br.replicates[0], pt))}


def select_topn(evp, years, order_key, n_by_year):
    sel = np.zeros(evp.shape[0], dtype=bool)
    for y, n in n_by_year.items():
        idx = np.flatnonzero(years == y)
        if n == 0 or idx.size == 0:
            continue
        # sort by -EV', then race_id/horse_number ascending (order_key is a rank of that tie order)
        o = np.lexsort((order_key[idx], -evp[idx]))
        sel[idx[o[:n]]] = True
    return sel


def stat(pay, sel, s3):
    a = sel & ~s3
    b = s3 & ~sel
    ra = pay[a].sum() / (100.0 * a.sum()) if a.any() else np.nan
    rb = pay[b].sum() / (100.0 * b.sum()) if b.any() else np.nan
    return (ra - rb) if (a.any() and b.any()) else 0.0, int(a.sum()), int(b.sum()), ra, rb


def main():
    rows, ens, horses, _ = base.load_all()
    d, _ = cells.build_cells(rows, horses, prev_ens_ev=ens)
    pop = base.population_mask(d, ens)
    years = d["year"].to_numpy()
    odds = d["odds"].to_numpy(float)
    won = d["won"].to_numpy(bool).astype(float)
    ph = ens / odds
    # L_h on 2010-2018 pooled (same estimator as stage 1)
    base.PERIODS = {"fit": (2010, 2018)}
    per = base.build_periods(d, pop, won, ph, CELLS, include_diag=True)
    Lh = {c: float(per["fit"].l_adj(np.ones((1, per["fit"].D)), k)[0]) for k, c in enumerate(CELLS)}
    mult = {c: 1.0 + 0.5 * (Lh[c] - 1.0) for c in CELLS}
    w = pop & (years >= WIN[0]) & (years <= WIN[1])
    idx = np.flatnonzero(w)
    E = d.iloc[idx]
    ev = ens[idx]
    yr = years[idx]
    od = odds[idx]
    pay = won[idx] * od * 100.0
    dates = E["race_date"].astype(str).str.slice(0, 10).to_numpy()
    tie = np.lexsort((E["horse_number"].to_numpy(float), E["race_id"].to_numpy()))
    order_key = np.empty(len(idx), dtype=np.int64)
    order_key[tie] = np.arange(len(idx))
    I = E[CELLS].to_numpy(bool)
    m = np.ones(len(idx))
    for k, c in enumerate(CELLS):
        m = m * np.where(I[:, k], mult[c], 1.0)
    from horseracing_eval import attention_rules as ar
    s3 = ar.match_mask(ar.definition("S3"), ens_ev=ev, single_ev=np.full(len(idx), np.nan), odds=od,
                       days_since_last=np.full(len(idx), np.nan))
    n_by_year = {int(y): int(s3[yr == y].sum()) for y in np.unique(yr)}
    evp = ev * m
    sel = select_topn(evp, yr, order_key, n_by_year)
    obs, na, nb, ra, rb = stat(pay, sel, s3)
    out = {"label": "EXPLORATORY / NOT PRE-REGISTERED (deviation) — X04a/X04b were chosen after seeing 2019-2026H1 L",
           "L_h_2010_2018": Lh, "multipliers": mult, "n_by_year_S3": n_by_year,
           "observed": {"roi_A_minus_roi_B": obs, "n_A": na, "n_B": nb, "roi_A": ra, "roi_B": rb},
           "S3": roi_ci(pay, dates, s3), "EVprime_topN": roi_ci(pay, dates, sel),
           "A_only_EVprime": roi_ci(pay, dates, sel & ~s3), "B_only_S3": roi_ci(pay, dates, s3 & ~sel)}
    # null (i): permute multipliers within year x parent-odds-quartile, parent = ev > 1.2*m_min/m_max
    mmin, mmax = min(1.0, *mult.values()), max(1.0, *mult.values())
    parent = ev > 1.2 * mmin / mmax
    rng = np.random.default_rng(SEED)
    strata = np.full(len(idx), -1)
    pidx = np.flatnonzero(parent)
    sid = 0
    for y in np.unique(yr[parent]):
        py = pidx[yr[pidx] == y]
        qs = np.quantile(od[py], [0.25, 0.5, 0.75])
        qb = np.searchsorted(qs, od[py], side="right")
        for b in range(4):
            strata[py[qb == b]] = sid
            sid += 1
    groups = [np.flatnonzero(strata == s) for s in range(sid)]
    null_i = np.empty(R_I)
    for r in range(R_I):
        mp = m.copy()
        for g in groups:
            mp[g] = m[g][rng.permutation(g.size)]
        null_i[r] = stat(pay, select_topn(ev * mp, yr, order_key, n_by_year), s3)[0]
    # null (ii): shuffle the joint indicator vectors within year x odds band over the window population
    bnd = base.band_index(od)
    null_ii = np.empty(R_II)
    keys = yr * 10 + bnd
    gidx = [np.flatnonzero(keys == k) for k in np.unique(keys)]
    for r in range(R_II):
        Ip = I.copy()
        for g in gidx:
            Ip[g] = I[g][rng.permutation(g.size)]
        mp = np.ones(len(idx))
        for k, c in enumerate(CELLS):
            mp = mp * np.where(Ip[:, k], mult[c], 1.0)
        null_ii[r] = stat(pay, select_topn(ev * mp, yr, order_key, n_by_year), s3)[0]
    out["null_i"] = {"R": R_I, "p95": float(np.quantile(null_i, 0.95)), "mean": float(null_i.mean()),
                     "p_ge_obs": float((1 + (null_i >= obs).sum()) / (R_I + 1)), "parent_rows": int(parent.sum())}
    out["null_ii"] = {"R": R_II, "p95": float(np.quantile(null_ii, 0.95)), "mean": float(null_ii.mean()),
                      "p_ge_obs": float((1 + (null_ii >= obs).sum()) / (R_II + 1))}
    out["exceeds_both_p95"] = bool(obs > out["null_i"]["p95"] and obs > out["null_ii"]["p95"])
    # year-weighted ROI of S3 vs EV' top-N
    yw = {}
    for name, msk in (("S3", s3), ("EVprime_topN", sel)):
        vals = [pay[msk & (yr == y)].sum() / (100.0 * (msk & (yr == y)).sum()) for y in np.unique(yr) if (msk & (yr == y)).any()]
        yw[name] = float(np.mean(vals))
    out["year_weighted_roi"] = yw
    p = base.OUT / "exploratory_stage2_X04.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
    print(json.dumps(out, ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
