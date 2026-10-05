"""R06_plus10 — JRA プラス10(110 円の床)で超本命の複勝を買う(事前登録 prereg.json に従う)。

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r06_plus10.py

読み取りのみ(DB は SELECT だけ)。出力: artifacts/roi_explore/missed_20261004/R06_plus10/results.json
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import numpy as np
import pandas as pd
import sqlalchemy as sa

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "eval" / "src"))
from horseracing_eval.bootstrap import (  # noqa: E402
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
)

OUT = REPO / "artifacts/roi_explore/missed_20261004/R06_plus10"
PREREG = OUT / "prereg.json"
DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
B = 20000
SEED_P, SEED_M, SEED_PERM = 20261004, 20261005, 20261006
S1_FROM, S1_TO = "2008-01-01", "2026-06-26"
S1R_FROM = "2019-01-01"
S2_FROM, S2_TO = "2025-01-01", "2026-09-22"
CUT = "2026-06-26"
BANDS = ["B1", "B2", "B3"]
LAM084 = (0.8312, 0.7101)


def band_of(odds: pd.Series) -> pd.Series:
    b = pd.Series(pd.NA, index=odds.index, dtype=object)
    b[odds <= 1.2 + 1e-9] = "B1"
    b[(odds > 1.2 + 1e-9) & (odds <= 1.3 + 1e-9)] = "B2"
    b[(odds > 1.3 + 1e-9) & (odds <= 1.5 + 1e-9)] = "B3"
    return b


def per_day(df: pd.DataFrame, days: list[str], val: str) -> np.ndarray:
    s = df.groupby("race_date")[val].sum()
    return s.reindex(days, fill_value=0.0).to_numpy(dtype=float)


def ratio_boot(dfs: list[pd.DataFrame], days: list[str], num: str, den: str, seed: int):
    nm = np.vstack([per_day(d, days, num) for d in dfs])
    dn = np.vstack([per_day(d, days, den) for d in dfs])
    return race_block_ratio_bootstrap_ci_v1(nm, dn, days, b=B, seed=seed)


def ci(reps: np.ndarray) -> tuple[float, float]:
    r = reps[np.isfinite(reps)]
    return float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


def holm(ps: dict[str, float]) -> dict[str, float]:
    items = sorted(ps.items(), key=lambda kv: kv[1])
    m = len(items)
    out, run = {}, 0.0
    for j, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - j) * p))
        out[k] = run
    return out


def harville_top3(q: np.ndarray, lam2: float, lam3: float) -> np.ndarray:
    n = len(q)
    w2, w3 = q ** lam2, q ** lam3
    s2, s3 = w2.sum(), w3.sum()
    out = np.zeros(n)
    for i in range(n):
        p = q[i]
        for j in range(n):
            if j == i:
                continue
            d2 = s2 - w2[j]
            p += q[j] * w2[i] / d2
            for k in range(n):
                if k == i or k == j:
                    continue
                p += q[j] * (w2[k] / d2) * (w3[i] / (s3 - w3[j] - w3[k]))
        out[i] = p
    return out


def main() -> int:
    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    cols = ["race_id", "race_date", "year", "odds", "q", "odds_rank", "field_size", "race_ok",
            "result_status", "finish_order", "horse_number", "won", "dead_heat", "n_winners"]
    r = pd.read_parquet(REPO / "artifacts/market_ev/rows_2007.parquet", columns=cols)
    bad = r.groupby("race_id")["result_status"].transform(lambda s: s.isna().any())
    r = r[r["race_ok"].astype(bool) & ~bad].copy()
    r["band"] = band_of(r["odds"])
    fs = r["field_size"]
    r["placed"] = ((r["result_status"] == "finished")
                   & (((fs >= 8) & (r["finish_order"] <= 3)) | ((fs >= 5) & (fs <= 7) & (r["finish_order"] <= 2)))).astype(float)
    r["one"] = 1.0
    r["win_pay"] = np.where(r["won"] & (r["n_winners"] == 1), r["odds"], 0.0)
    res: dict = {"test_id": "R06_plus10", "prereg_sha256": sha}

    # ---------------- stage 1: P(top3) ----------------
    e8 = r[r["field_size"] >= 8]
    s1 = e8[(e8.race_date >= S1_FROM) & (e8.race_date <= S1_TO)]
    days1 = sorted(s1.race_date.unique())
    bdf1 = [s1[s1.band == b] for b in BANDS]
    bootP = ratio_boot(bdf1, days1, "placed", "one", SEED_P)
    s1r = s1[s1.race_date >= S1R_FROM]
    days1r = sorted(s1r.race_date.unique())
    bootPr = ratio_boot([s1r[s1r.band == b] for b in BANDS], days1r, "placed", "one", SEED_P)
    st1 = {}
    for i, b in enumerate(BANDS):
        d = bdf1[i]
        yr = d.groupby("year").agg(n=("one", "sum"), placed=("placed", "sum"))
        yr["P"] = yr["placed"] / yr["n"]
        st1[b] = {
            "n": int(len(d)), "placed": int(d.placed.sum()), "P": float(bootP.point[i]),
            "P_ci": [float(bootP.ci_low[i]), float(bootP.ci_high[i])],
            "P_recent_2019": float(bootPr.point[i]),
            "P_recent_ci": [float(bootPr.ci_low[i]), float(bootPr.ci_high[i])],
            "n_recent": int((d.race_date >= S1R_FROM).sum()),
            "P_year_weighted": float(yr["P"].mean()),
            "n_years": int(len(yr)),
            "per_year": {int(y): {"n": int(v.n), "placed": int(v.placed), "P": round(float(v.P), 4)}
                         for y, v in yr.iterrows()},
            "breakeven_P_at_1.1": 1 / 1.1,
        }
    res["stage1"] = {"window": [S1_FROM, S1_TO], "n_race_days": len(days1), "bands": st1}

    # 2007 control (P only)
    c7 = e8[(e8.year == 2007)]
    days7 = sorted(c7.race_date.unique())
    boot7 = ratio_boot([c7[c7.band == b] for b in BANDS], days7, "placed", "one", SEED_P)
    res["control_2007"] = {b: {"n": int((c7.band == b).sum()), "placed": int(c7[c7.band == b].placed.sum()),
                               "P": float(boot7.point[i]),
                               "P_ci": [float(boot7.ci_low[i]), float(boot7.ci_high[i])]}
                           for i, b in enumerate(BANDS)}

    # ---------------- stage 2: real place dividends ----------------
    with sa.create_engine(DB).connect() as c:
        dv = pd.read_sql(sa.text(
            "select race_id, (selection->>0)::int as horse_number, odds::float as dividend "
            "from exotic_odds where bet_type='place'"), c)
        pq = pd.read_sql(sa.text(
            "select race_id, horse_number, place_odds_low::float as pql, place_odds_high::float as pqh "
            "from race_horses where place_odds_low is not null"), c)
    cov = set(dv.race_id)
    s2all = r[(r.race_date >= S2_FROM) & (r.race_date <= S2_TO) & r.race_id.isin(cov)].copy()
    s2all = s2all.merge(dv, on=["race_id", "horse_number"], how="left")
    miss = s2all[(s2all.placed == 1) & s2all["dividend"].isna()]
    miss_band = miss[miss.band.notna()]
    drop_races = set(miss.race_id)  # any placed horse without a dividend -> race dropped (reported)
    n_drop_band_races = int(len(set(miss_band.race_id)))
    s2all = s2all[~s2all.race_id.isin(drop_races)].copy()
    s2all["pay"] = np.where(s2all.placed == 1, s2all["dividend"].fillna(0.0), 0.0)
    s2all["pay_nofloor"] = np.where(np.isclose(s2all.pay, 1.1), 1.0, s2all.pay)
    s2all["placed_pay"] = s2all["pay"]
    res["stage2_data"] = {"covered_races": int(s2all.race_id.nunique()),
                          "races_dropped_missing_dividend": int(len(drop_races)),
                          "of_which_band_horse_missing": n_drop_band_races,
                          "window": [S2_FROM, S2_TO]}

    def stage2_block(sub: pd.DataFrame, fs_mask, label: str, seed: int, top_n: int):
        s = sub[fs_mask(sub)]
        days = sorted(s.race_date.unique())
        bd = [s[s.band == b] for b in BANDS]
        bm = ratio_boot(bd, days, "pay", "placed", seed)          # M = Σpay/Σplaced
        bmn = ratio_boot(bd, days, "pay_nofloor", "placed", seed)
        bd_dir = ratio_boot(bd, days, "pay", "one", seed)         # direct ROI
        out = {}
        for i, b in enumerate(BANDS):
            d = bd[i]
            pl = d[d.placed == 1]
            dist = {"1.0": int(np.isclose(pl.pay, 1.0).sum()), "1.1": int(np.isclose(pl.pay, 1.1).sum()),
                    "1.2": int(np.isclose(pl.pay, 1.2).sum()), ">=1.3": int((pl.pay >= 1.25).sum())}
            yr = d.groupby("year").agg(n=("one", "sum"), pay=("pay", "sum"), placed=("placed", "sum"))
            yr["roi"] = yr.pay / yr.n
            out[b] = {
                "n": int(len(d)), "placed": int(d.placed.sum()),
                "P_same_sample": float(d.placed.mean()) if len(d) else None,
                "M": float(bm.point[i]), "M_ci": [float(bm.ci_low[i]), float(bm.ci_high[i])],
                "M_nofloor_lower": float(bmn.point[i]),
                "payout_dist_given_placed": dist,
                "R_direct": float(bd_dir.point[i]),
                "R_direct_ci": [float(bd_dir.ci_low[i]), float(bd_dir.ci_high[i])],
                "R_direct_p_one_sided": centered_one_sided_p_from_replicates(bd_dir.replicates[i], float(bd_dir.point[i])),
                "R_direct_year_weighted": float(yr.roi.mean()) if len(yr) else None,
                "per_year": {int(y): {"n": int(v.n), "placed": int(v.placed), "roi": round(float(v.roi), 4)}
                             for y, v in yr.iterrows()},
            }
        return out, bm, bmn, bd_dir, days, s

    st2, bootM, bootMn, bootDir, days2, s2_8 = stage2_block(s2all, lambda d: d.field_size >= 8, "8+", SEED_M, 3)
    res["stage2_primary_8plus"] = st2
    sens = {}
    for lab, lo, hi in [("2025-11-01..2026-09-22", "2025-11-01", S2_TO), ("2025-01-01..2026-06-26", S2_FROM, CUT),
                        ("2026-06-27..2026-09-22", "2026-06-27", S2_TO)]:
        sub = s2all[(s2all.race_date >= lo) & (s2all.race_date <= hi)]
        o, *_ = stage2_block(sub, lambda d: d.field_size >= 8, lab, SEED_M, 3)
        sens[lab] = {b: {k: o[b][k] for k in ("n", "placed", "M", "M_ci", "R_direct", "R_direct_ci",
                                               "payout_dist_given_placed")} for b in BANDS}
    res["stage2_sensitivity_8plus"] = sens

    # ---------------- primary: R_dec = P x M ----------------
    prim, pvals = {}, {}
    for i, b in enumerate(BANDS):
        prod = bootP.replicates[i] * bootM.replicates[i]
        point = float(bootP.point[i] * bootM.point[i])
        p = centered_one_sided_p_from_replicates(prod, point)
        prod_r = bootPr.replicates[i] * bootM.replicates[i]
        prod_nf = bootP.replicates[i] * bootMn.replicates[i]
        pvals[b] = p
        prim[b] = {
            "R_dec": point, "R_dec_ci": list(ci(prod)), "p_one_sided": p,
            "R_dec_recent_2019": float(bootPr.point[i] * bootM.point[i]), "R_dec_recent_ci": list(ci(prod_r)),
            "R_dec_year_weighted": float(st1[b]["P_year_weighted"] * bootM.point[i]),
            "R_nofloor_lower": float(bootP.point[i] * bootMn.point[i]), "R_nofloor_lower_ci": list(ci(prod_nf)),
            "floor_contribution_upper_bound": float(point - bootP.point[i] * bootMn.point[i]),
        }
    hp = holm(pvals)
    for b in BANDS:
        prim[b]["p_holm"] = hp[b]
        prim[b]["historical_candidate"] = bool(
            hp[b] < 0.05 and st2[b]["R_direct"] >= 1.0 and prim[b]["R_dec_recent_2019"] > 1.0
            and prim[b]["R_dec_year_weighted"] > 1.0)
    res["primary_R_dec"] = prim

    # ---------------- parent: 1st favourite place (stage-2 sample, 8+) ----------------
    par = s2_8[s2_8.odds_rank == 1].copy()
    days_par = sorted(par.race_date.unique())
    bpar = ratio_boot([par], days_par, "pay", "one", SEED_M)
    parent = {"n": int(len(par)), "placed": int(par.placed.sum()), "roi": float(bpar.point[0]),
              "roi_ci": [float(bpar.ci_low[0]), float(bpar.ci_high[0])]}
    rng = np.random.default_rng(SEED_PERM)
    pay = par.pay.to_numpy()
    strata = par.year.to_numpy()
    perm = {}
    for b in BANDS:
        lab = (par.band == b).to_numpy()
        if lab.sum() == 0 or (~lab).sum() == 0:
            continue
        obs = pay[lab].mean() - pay[~lab].mean()
        ge = 0
        idx_by = {y: np.flatnonzero(strata == y) for y in np.unique(strata)}
        nb = {y: int(lab[ix].sum()) for y, ix in idx_by.items()}
        for _ in range(B):
            l2 = np.zeros(len(pay), dtype=bool)
            for y, ix in idx_by.items():
                if nb[y]:
                    l2[rng.choice(ix, size=nb[y], replace=False)] = True
            if pay[l2].mean() - pay[~l2].mean() >= obs - 1e-12:
                ge += 1
        perm[b] = {"n_band_in_parent": int(lab.sum()), "roi_band": float(pay[lab].mean()),
                   "roi_rest": float(pay[~lab].mean()), "diff": float(obs), "p_one_sided": (1 + ge) / (B + 1)}
    res["parent_1st_fav_place"] = {"parent": parent, "band_vs_rest_permutation": perm}

    # ---------------- placebo: WIN bets same bands (stage-1 window, dead-heat-win races excluded) ----------------
    w1 = s1[s1.n_winners == 1]
    bw = ratio_boot([w1[w1.band == b] for b in BANDS], days1, "win_pay", "one", SEED_P)
    plc = {}
    for i, b in enumerate(BANDS):
        d = w1[w1.band == b]
        yr = d.groupby("year").agg(n=("one", "sum"), pay=("win_pay", "sum"))
        plc[b] = {"n": int(len(d)), "wins": int((d.win_pay > 0).sum()), "roi": float(bw.point[i]),
                  "roi_ci": [float(bw.ci_low[i]), float(bw.ci_high[i])],
                  "p_one_sided_gt1": centered_one_sided_p_from_replicates(bw.replicates[i], float(bw.point[i])),
                  "roi_year_weighted": float((yr.pay / yr.n).mean())}
    res["placebo_win"] = plc
    # paired place - win on stage-2 sample <= 2026-06-26 (dead-heat-win races excluded)
    pw = s2_8[(s2_8.race_date <= CUT) & (s2_8.n_winners == 1)].copy()
    pw["diff"] = pw.pay - pw.win_pay
    days_pw = sorted(pw.race_date.unique())
    bpw = ratio_boot([pw[pw.band == b] for b in BANDS], days_pw, "diff", "one", SEED_M)
    res["paired_place_minus_win_stage2_le0626"] = {
        b: {"n": int((pw.band == b).sum()), "place_roi": float(pw[pw.band == b].pay.mean()) if (pw.band == b).any() else None,
            "win_roi": float(pw[pw.band == b].win_pay.mean()) if (pw.band == b).any() else None,
            "diff": float(bpw.point[i]), "diff_ci": [float(bpw.ci_low[i]), float(bpw.ci_high[i])]}
        for i, b in enumerate(BANDS)}

    # ---------------- q-world diagnostic ----------------
    qd = {}
    s1b = s1[s1.band.notna()]
    race_q = {rid: g for rid, g in s1.groupby("race_id") if rid in set(s1b.race_id)}
    for lamname, (l2, l3) in {"lambda1": (1.0, 1.0), "lambda084": LAM084}.items():
        imp = []
        for rid, g in race_q.items():
            q = g.q.to_numpy(dtype=float)
            q = q / q.sum()
            p3 = harville_top3(q, l2, l3)
            m = g.band.notna().to_numpy()
            for bb, pp, pl in zip(g.band[m], p3[m], g.placed[m]):
                imp.append((bb, pp, pl))
        t = pd.DataFrame(imp, columns=["band", "imp", "placed"])
        qd[lamname] = {b: {"n": int((t.band == b).sum()), "P_implied_mean": float(t[t.band == b].imp.mean()),
                           "P_observed": float(t[t.band == b].placed.mean()),
                           "R_q_world": float(t[t.band == b].imp.mean() * bootM.point[i])}
                       for i, b in enumerate(BANDS)}
    res["q_world_diagnostic"] = qd

    # ---------------- 5-7 starters diagnostic ----------------
    e57 = r[(r.field_size >= 5) & (r.field_size <= 7)]
    s157 = e57[(e57.race_date >= S1_FROM) & (e57.race_date <= S1_TO)]
    d57 = {}
    st257, *_ = stage2_block(s2all, lambda d: (d.field_size >= 5) & (d.field_size <= 7), "5-7", SEED_M, 2)
    for b in BANDS:
        d = s157[s157.band == b]
        P = float(d.placed.mean()) if len(d) else None
        M = st257[b]["M"]
        d57[b] = {"n_stage1": int(len(d)), "P_top2": P, "n_stage2": st257[b]["n"], "M": M,
                  "R_dec": (P * M) if (P is not None and M == M) else None,
                  "payout_dist_given_placed": st257[b]["payout_dist_given_placed"]}
    res["diag_5to7_starters"] = d57

    # ---------------- pre-race place quote diagnostic ----------------
    qq = s2_8[s2_8.band.notna()].merge(pq, on=["race_id", "horse_number"], how="inner")
    res["diag_place_quote"] = {
        "n_band_horses_with_quote": int(len(qq)),
        "by_quote_low": {str(k): {"n": int(len(g)), "placed": int(g.placed.sum()), "roi": float(g.pay.mean()),
                                  "payout_values": sorted(map(float, g[g.placed == 1].pay.round(2)))}
                         for k, g in qq.groupby(qq.pql.round(1))},
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
