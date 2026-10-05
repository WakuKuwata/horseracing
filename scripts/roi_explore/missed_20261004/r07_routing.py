"""R07_routing — same-event bet-type routing with real dividends (pre-registered).

Prereg: artifacts/roi_explore/missed_20261004/R07_routing/prereg.json (sha256 in prereg.sha256).
Read-only: SELECT only. Outputs under artifacts/roi_explore/missed_20261004/R07_routing/.

Run: cd training && uv run python ../scripts/roi_explore/missed_20261004/r07_routing.py
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "eval" / "src"))
from horseracing_eval.bootstrap import (  # noqa: E402
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
)

OUT = ROOT / "artifacts" / "roi_explore" / "missed_20261004" / "R07_routing"
PREREG = OUT / "prereg.json"
DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"

B_MAIN = 20000
SEED = 20261004
ALPHA = 0.05
N_SIM = 200
B_SIM = 2000

NULL_C = {"a": 0.75 / 0.775, "b": 0.725 / 0.75, "c": 0.775 / 0.80, "d": 0.75 / 0.80}
PAIRS = ["a", "b", "c", "d"]
CELLS = [f"{p}{k}" for p in PAIRS for k in (1, 2, 3)]

W1 = ("2025-01-01", "2025-12-31")
W2 = ("2026-01-01", "2026-10-04")
WIN_CUTOFF = "2026-06-26"  # (d) only uses races <= this date (stored win odds settlement rule)
W2A = ("2026-01-01", "2026-06-26")
W2B = ("2026-07-23", "2026-10-04")


def band3(p: int) -> int:
    return 1 if p <= 3 else (2 if p <= 6 else 3)


def load():
    eng = create_engine(DB)
    with eng.connect() as c:
        races = pd.read_sql(text(
            "select race_id, race_date from races where race_date between '2025-01-01' and '2026-10-04' "
            "and race_id in (select distinct race_id from exotic_odds)"), c)
        rids = list(races.race_id)
        rh = pd.read_sql(text(
            "select race_id, horse_id, horse_number, popularity, odds, entry_status from race_horses "
            "where race_id = any(:r)"), c, params={"r": rids})
        rr = pd.read_sql(text(
            "select race_id, horse_id, finish_order, result_status from race_results where race_id = any(:r)"),
            c, params={"r": rids})
        ex = pd.read_sql(text(
            "select race_id, bet_type, selection, odds from exotic_odds where race_id = any(:r)"),
            c, params={"r": rids})
        qq = pd.read_sql(text(
            "select q.race_id, r.race_date, q.quotes from exotic_quotes q join races r using(race_id) "
            "where q.bet_type='quinella' and r.race_date between '2025-01-01' and '2026-10-04'"), c)
    races["race_date"] = races.race_date.astype(str)
    qq["race_date"] = qq.race_date.astype(str)
    return races, rh, rr, ex, qq


def build_events(races, rh, rr, ex):
    excl = defaultdict(int)
    d_drop = set()
    ev_rows = []
    tick_rows = []
    rh_g = {k: g for k, g in rh.groupby("race_id")}
    rr_g = {k: g for k, g in rr.groupby("race_id")}
    ex_g = {k: g for k, g in ex.groupby("race_id")}
    for rid, rdate in zip(races.race_id, races.race_date):
        h = rh_g.get(rid)
        e = ex_g.get(rid)
        if h is None or e is None:
            excl["missing_tables"] += 1
            continue
        bt = set(e.bet_type)
        if not {"place", "quinella", "wide", "exacta", "trio", "trifecta"} <= bt:
            excl["missing_bet_type"] += 1
            continue
        st = h[h.entry_status == "started"]
        n = len(st)
        if n < 5:
            excl["n_lt_5"] += 1
            continue
        if st.popularity.isna().any() or st.horse_number.isna().any():
            excl["null_popularity_or_number"] += 1
            continue
        canc = h[(h.entry_status == "cancelled") & h.odds.notna()]
        if len(canc):
            excl["post_close_cancel"] += 1
            continue
        res = rr_g.get(rid)
        if res is None:
            excl["no_results"] += 1
            continue
        res = res.merge(st[["horse_id", "horse_number"]], on="horse_id", how="left")
        top = res[res.finish_order.isin([1, 2, 3])]
        cnt = top.finish_order.value_counts()
        if not all(cnt.get(k, 0) == 1 for k in (1, 2, 3)) or top.horse_number.isna().any():
            excl["dead_heat_or_top3_anomaly"] += 1
            continue
        f = {int(o): int(hn) for o, hn in zip(top.finish_order, top.horse_number)}
        h1, h2, h3 = f[1], f[2], f[3]
        rows = {b: e[e.bet_type == b] for b in ["place", "quinella", "wide", "exacta", "trio", "trifecta"]}
        kplace = 3 if n >= 8 else 2
        if (len(rows["quinella"]) != 1 or len(rows["exacta"]) != 1 or len(rows["trio"]) != 1
                or len(rows["trifecta"]) != 1 or len(rows["place"]) != kplace or len(rows["wide"]) != 3):
            excl["payout_multiplicity_anomaly"] += 1
            continue

        def sel(b):
            return [list(map(int, s)) for s in rows[b].selection]

        def od(b):
            return [float(x) for x in rows[b].odds]

        ok = (sorted(sel("quinella")[0]) == sorted([h1, h2])
              and sel("exacta")[0] == [h1, h2]
              and sorted(sel("trio")[0]) == sorted([h1, h2, h3])
              and sel("trifecta")[0] == [h1, h2, h3]
              and sorted(s[0] for s in sel("place")) == sorted([h1, h2, h3][:kplace])
              and sorted(tuple(sorted(s)) for s in sel("wide")) == sorted(
                  [tuple(sorted(p)) for p in [(h1, h2), (h1, h3), (h2, h3)]]))
        if not ok:
            excl["integrity_mismatch"] += 1
            continue
        dq = od("quinella")[0]
        de = od("exacta")[0]
        dtrio = od("trio")[0]
        dtri = od("trifecta")[0]
        place = {s[0]: o for s, o in zip(sel("place"), od("place"))}
        wide = {tuple(sorted(s)): o for s, o in zip(sel("wide"), od("wide"))}
        pop = {int(hn): int(p) for hn, p in zip(st.horse_number, st.popularity)}
        win_odds = {int(hn): (float(o) if o is not None and not pd.isna(o) else np.nan)
                    for hn, o in zip(st.horse_number, st.odds)}
        P1, P2, P3 = pop[h1], pop[h2], pop[h3]
        base = dict(race_id=rid, race_date=rdate, n=n)

        # pre-race ticket counts
        pops = np.array(list(pop.values()))
        k3 = int((pops <= 3).sum())
        m = n - k3
        c2 = math.comb
        tick = {"a1": c2(k3, 2), "a2": k3 * m, "a3": c2(m, 2),
                "b1": c2(k3, 2) * m + c2(k3, 3), "b2": k3 * c2(m, 2), "b3": c2(m, 3),
                "d1": int((pops <= 3).sum()), "d2": int(((pops >= 4) & (pops <= 6)).sum()),
                "d3": int((pops >= 7).sum())}
        if n >= 8:
            tick.update({"c1": tick["d1"], "c2": tick["d2"], "c3": tick["d3"]})
        for cell, v in tick.items():
            tick_rows.append(dict(base, cell=cell, pair=cell[0], tickets=v))

        # (a)
        ka = int(P1 <= 3) + int(P2 <= 3)
        sa = {2: 1, 1: 2, 0: 3}[ka]
        ev_rows.append(dict(base, pair="a", cell=f"a{sa}", A=dq, B=0.5 * de,
                            A_adj=dq + 0.05, B_adj=0.5 * (de + 0.05), hit_key=f"{h1}-{h2}",
                            A_raw=dq, B_raw=de))
        # (b)
        kb = int(P1 <= 3) + int(P2 <= 3) + int(P3 <= 3)
        sb = 1 if kb >= 2 else (2 if kb == 1 else 3)
        ev_rows.append(dict(base, pair="b", cell=f"b{sb}", A=dtrio, B=dtri / 6.0,
                            A_adj=dtrio + 0.05, B_adj=(dtri + 0.05) / 6.0, hit_key=f"{h1}-{h2}-{h3}",
                            A_raw=dtrio, B_raw=dtri))
        # (c)
        if n >= 8:
            for i, others in ((h1, (h2, h3)), (h2, (h1, h3)), (h3, (h1, h2))):
                w1 = wide[tuple(sorted((i, others[0])))]
                w2 = wide[tuple(sorted((i, others[1])))]
                ev_rows.append(dict(base, pair="c", cell=f"c{band3(pop[i])}", A=place[i],
                                    B=(w1 + w2) / (n - 1), A_adj=place[i] + 0.05,
                                    B_adj=(w1 + w2 + 0.10) / (n - 1), hit_key=str(i),
                                    A_raw=place[i], B_raw=w1 + w2))
        # (d)
        wo = win_odds.get(h1, np.nan)
        if rdate <= WIN_CUTOFF:
            if not (wo == wo) or wo <= 0:
                excl["d_win_odds_missing"] += 1
                d_drop.add(rid)
            else:
                ev_rows.append(dict(base, pair="d", cell=f"d{band3(P1)}", A=wo, B=de / (n - 1),
                                    A_adj=wo + 0.05, B_adj=(de + 0.05) / (n - 1), hit_key=str(h1),
                                    A_raw=wo, B_raw=de))
    ev = pd.DataFrame(ev_rows)
    tk = pd.DataFrame(tick_rows)
    # (d) tickets only where (d) is settled
    tk = tk[~((tk.pair == "d") & ((tk.race_date > WIN_CUTOFF) | tk.race_id.isin(d_drop)))]
    return ev, tk, dict(excl)


def premium_flags(qq):
    s = []
    for rid, d, q in zip(qq.race_id, qq.race_date, qq.quotes):
        vals = [v[0] for v in q.values() if isinstance(v, list) and v and v[0] is not None and v[0] > 0]
        if len(vals) >= 3:
            s.append((rid, d, sum(1.0 / x for x in vals)))
    df = pd.DataFrame(s, columns=["race_id", "race_date", "inv_sum"])
    day = df.groupby("race_date").inv_sum.median()
    thr = float(day.median()) - 0.02
    flagged = sorted(day[day < thr].index.tolist())
    return flagged, thr, float(day.median()), int(len(day)), day


def day_matrix(df, days, valcol):
    idx = {d: j for j, d in enumerate(days)}
    out = np.zeros(len(days))
    g = df.groupby("race_date")[valcol].sum()
    for d, v in g.items():
        if d in idx:
            out[idx[d]] = v
    return out


def holm(pvals: dict, alpha=ALPHA):
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    rej = {k: False for k in pvals}
    adj = {}
    running = 0.0
    for r, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - r) * p))
        adj[k] = running
    for r, (k, p) in enumerate(items):
        if p <= alpha / (m - r):
            rej[k] = True
        else:
            break
    return rej, adj


def window_days(ev, pair, lo, hi):
    sub = ev[(ev.pair == pair) & (ev.race_date >= lo) & (ev.race_date <= hi)]
    return sorted(sub.race_date.unique().tolist())


def window_bounds(pair, w):
    lo, hi = w
    if pair == "d" and hi > WIN_CUTOFF:
        hi = WIN_CUTOFF
    return lo, hi


def eval_window(ev, tk, w, *, b=B_MAIN, seed=SEED, exclude_days=(), bcol="B", acol="A"):
    """Per-cell point/CI/reps for one window. Returns dict cell -> stats, plus reps for tests."""
    out = {}
    for pair in PAIRS:
        lo, hi = window_bounds(pair, w)
        e = ev[(ev.pair == pair) & (ev.race_date >= lo) & (ev.race_date <= hi)
               & ~ev.race_date.isin(exclude_days)]
        t = tk[(tk.pair == pair) & (tk.race_date >= lo) & (tk.race_date <= hi)
               & ~tk.race_date.isin(exclude_days)]
        days = sorted(set(e.race_date) | set(t.race_date))
        if len(days) == 0:
            continue
        c = NULL_C[pair]
        rows_cells = [f"{pair}{k}" for k in (1, 2, 3)]
        A = np.vstack([day_matrix(e[e.cell == cl], days, acol) for cl in rows_cells]
                      + [day_matrix(e, days, acol)])
        Bm = np.vstack([day_matrix(e[e.cell == cl], days, bcol) for cl in rows_cells]
                       + [day_matrix(e, days, bcol)])
        T = np.vstack([day_matrix(t[t.cell == cl], days, "tickets") for cl in rows_cells]
                      + [day_matrix(t, days, "tickets")])
        th = race_block_ratio_bootstrap_ci_v1(Bm / c, A, days, b=b, seed=seed)
        thi = race_block_ratio_bootstrap_ci_v1(A * c, Bm, days, b=b, seed=seed)
        rA = race_block_ratio_bootstrap_ci_v1(A, T, days, b=b, seed=seed)
        rB = race_block_ratio_bootstrap_ci_v1(Bm, T, days, b=b, seed=seed)
        rR = race_block_ratio_bootstrap_ci_v1(Bm, A, days, b=b, seed=seed)
        for k, cl in enumerate(rows_cells + [f"{pair}_parent"]):
            ec = e if cl.endswith("parent") else e[e.cell == cl]
            sa, sb = float(ec[acol].sum()), float(ec[bcol].sum())
            st = dict(
                pair=pair, cell=cl, window=f"{lo}..{hi}", n_days=len(days),
                n_races=int(ec.race_id.nunique()), n_hits=int(len(ec)),
                n_tickets=float(T[k].sum()), sumA=sa, sumB=sb,
                R=(sb / sa) if sa > 0 else float("nan"), c=c,
                theta=float(th.point[k]), theta_lo=float(th.ci_low[k]), theta_hi=float(th.ci_high[k]),
                R_lo=float(rR.ci_low[k]), R_hi=float(rR.ci_high[k]),
                roiA=float(rA.point[k]), roiA_lo=float(rA.ci_low[k]), roiA_hi=float(rA.ci_high[k]),
                roiB=float(rB.point[k]), roiB_lo=float(rB.ci_low[k]), roiB_hi=float(rB.ci_high[k]),
            )
            if len(ec) and sa > 0 and sb > 0:
                st["max_share_A"] = float(ec[acol].max() / sa)
                st["max_share_B"] = float(ec[bcol].max() / sb)
                st["R_drop_maxB"] = float((sb - ec[bcol].max()) / (sa - ec.loc[ec[bcol].idxmax(), acol]))
                st["R_drop_maxA"] = float((sb - ec.loc[ec[acol].idxmax(), bcol]) / (sa - ec[acol].max()))
            out[cl] = dict(stats=st, reps_theta=th.replicates[k], reps_inv=thi.replicates[k], reps_R=rR.replicates[k],
                           point_inv=float(thi.point[k]), point_R=float(rR.point[k]))
        # stratum-minus-parent log diff (same draws)
        for k, cl in enumerate(rows_cells):
            if cl in out:
                with np.errstate(divide="ignore", invalid="ignore"):
                    d = np.log(th.replicates[k]) - np.log(th.replicates[3])
                d = d[np.isfinite(d)]
                pt = math.log(th.point[k]) - math.log(th.point[3]) if th.point[k] > 0 else float("nan")
                out[cl]["stats"].update(
                    logdiff_parent=pt,
                    logdiff_parent_lo=float(np.percentile(d, 2.5)) if d.size else float("nan"),
                    logdiff_parent_hi=float(np.percentile(d, 97.5)) if d.size else float("nan"))
    return out


def run_tests(w1, w2):
    sign = {}
    pv = {}
    for cl in CELLS:
        if cl not in w1 or cl not in w2:
            continue
        t1 = w1[cl]["stats"]["theta"]
        s = 1 if (not (t1 == t1) or t1 >= 1.0) else -1
        sign[cl] = s
        if s == 1:
            pv[cl] = centered_one_sided_p_from_replicates(w2[cl]["reps_theta"], w2[cl]["stats"]["theta"])
        else:
            pv[cl] = centered_one_sided_p_from_replicates(w2[cl]["reps_inv"], w2[cl]["point_inv"])
    rej, adj = holm(pv)
    # secondary actionable
    act = {cl for cl in pv if w1[cl]["stats"]["R"] > 1.0}
    pv2 = {cl: centered_one_sided_p_from_replicates(w2[cl]["reps_R"], w2[cl]["point_R"]) for cl in act}
    rej2, adj2 = holm(pv2) if pv2 else ({}, {})
    return sign, pv, rej, adj, pv2, rej2, adj2


def classify(cl, sign, rej, rej2, w2):
    if not rej.get(cl):
        return "NOT_CONFIRMED" + ("_UNDERPOWERED" if w2[cl]["stats"]["n_hits"] < 30 else "")
    if sign[cl] == 1:
        return "ROUTE_TO_B_CANDIDATE" if rej2.get(cl) else "PRICING_DEVIATION_CONFIRMED(theta>1,R<=1 or R>1 unconfirmed)"
    return "A_PREFERRED_BEYOND_TAKEOUT"


def calibration(ev, tk):
    """Exchangeable-ratio null: permute r within cell (W1+W2 pooled), rerun the pipeline."""
    fw = 0
    per = defaultdict(int)
    rng_master = np.random.default_rng(0)
    for s in range(1, N_SIM + 1):
        rng = np.random.default_rng(s)
        evs = ev.copy()
        for cl in CELLS:
            pair = cl[0]
            mask = (evs.cell == cl) & (((evs.race_date >= W1[0]) & (evs.race_date <= W1[1]))
                                       | ((evs.race_date >= W2[0]) & (evs.race_date <= window_bounds(pair, W2)[1])))
            idx = np.flatnonzero(mask.values)
            if idx.size == 0:
                continue
            A = evs.A.values[idx]
            r = evs.B.values[idx] / (NULL_C[pair] * A)
            kappa = float((A * r).sum() / A.sum())
            perm = rng.permutation(idx.size)
            evs.loc[evs.index[idx], "B"] = NULL_C[pair] * A * r[perm] / kappa
        w1 = eval_window(evs, tk, W1, b=50, seed=SEED)  # W1: sign only (point estimate)
        w2 = eval_window(evs, tk, W2, b=B_SIM, seed=SEED + s)
        sign, pv, rej, adj, *_ = run_tests(w1, w2)
        if any(rej.values()):
            fw += 1
        for cl, v in rej.items():
            per[cl] += int(v)
    _ = rng_master
    return {"n_sim": N_SIM, "b_boot": B_SIM, "familywise_fpr": fw / N_SIM,
            "per_cell_rejection_rate": {cl: per[cl] / N_SIM for cl in CELLS}}


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def main():
    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    races, rh, rr, ex, qq = load()
    ev, tk, excl = build_events(races, rh, rr, ex)
    ev.to_parquet(OUT / "events.parquet", index=False)
    flagged, thr, dmed, ndays_q, daymed = premium_flags(qq)

    w1 = eval_window(ev, tk, W1)
    w2 = eval_window(ev, tk, W2)
    sign, pv, rej, adj, pv2, rej2, adj2 = run_tests(w1, w2)

    # diagnostics
    w1adj = eval_window(ev, tk, W1, bcol="B_adj", acol="A_adj")
    w2adj = eval_window(ev, tk, W2, bcol="B_adj", acol="A_adj")
    w2a = eval_window(ev, tk, W2A)
    w2b = eval_window(ev, tk, W2B)
    w1p = eval_window(ev, tk, W1, exclude_days=flagged)
    w2p = eval_window(ev, tk, W2, exclude_days=flagged)
    sign_p, pv_p, rej_p, adj_p, pv2_p, rej2_p, adj2_p = run_tests(w1p, w2p)

    rows = []
    for cl in CELLS + [f"{p}_parent" for p in PAIRS]:
        base = {"cell": cl}
        for tag, W in (("W1", w1), ("W2", w2), ("W1adj", w1adj), ("W2adj", w2adj), ("W2a", w2a),
                       ("W2b", w2b), ("W1prem", w1p), ("W2prem", w2p)):
            if cl in W:
                for k, v in W[cl]["stats"].items():
                    if k in ("pair", "cell"):
                        continue
                    base[f"{tag}_{k}"] = v
        if cl in pv:
            t1, t2 = w1[cl]["stats"]["theta"], w2[cl]["stats"]["theta"]
            base.update(sign_W1=sign[cl], p_W2=pv[cl], p_holm=adj[cl], holm_reject=rej[cl],
                        p_actionable=pv2.get(cl), p_actionable_holm=adj2.get(cl),
                        actionable_reject=rej2.get(cl), verdict=classify(cl, sign, rej, rej2, w2),
                        eqyear_log_theta=(math.log(t1) + math.log(t2)) / 2,
                        eqyear_theta=math.exp((math.log(t1) + math.log(t2)) / 2),
                        prem_sign=sign_p.get(cl), prem_p=pv_p.get(cl), prem_holm_reject=rej_p.get(cl))
        rows.append(base)
    cells = pd.DataFrame(rows)
    cells.to_csv(OUT / "cells.csv", index=False)

    # plus10 / 1.0 floor counts on place
    pl = ev[ev.pair == "c"]
    plus = {"place_eq_1.0": int((pl.A_raw.round(2) == 1.0).sum()),
            "place_eq_1.1": int((pl.A_raw.round(2) == 1.1).sum()), "place_events": int(len(pl))}

    calib = calibration(ev, tk)
    (OUT / "calibration_sims.json").write_text(json.dumps(clean(calib), indent=1, ensure_ascii=False))

    res = {
        "test_id": "R07_routing", "prereg_sha256": sha, "exclusions": excl,
        "n_races_eligible": {"W1": int(ev[(ev.race_date <= W1[1])].race_id.nunique()),
                             "W2": int(ev[(ev.race_date >= W2[0])].race_id.nunique())},
        "premium": {"flagged_days": flagged, "threshold": thr, "median_of_day_medians": dmed,
                    "n_days_with_quotes": ndays_q},
        "plus10": plus,
        "tests": {cl: {"sign_W1": sign.get(cl), "p_W2": pv.get(cl), "p_holm": adj.get(cl),
                       "holm_reject": rej.get(cl), "p_actionable": pv2.get(cl),
                       "actionable_reject": rej2.get(cl),
                       "verdict": classify(cl, sign, rej, rej2, w2) if cl in pv else None}
                  for cl in CELLS},
        "premium_sensitivity_tests": {cl: {"sign_W1": sign_p.get(cl), "p_W2": pv_p.get(cl),
                                           "holm_reject": rej_p.get(cl)} for cl in CELLS},
        "calibration": calib,
        "cells": {r["cell"]: r for r in rows},
    }
    (OUT / "results.json").write_text(json.dumps(clean(res), indent=1, ensure_ascii=False))
    pd.set_option("display.width", 250)
    show = ["cell", "W1_n_hits", "W1_R", "W1_theta", "W2_n_hits", "W2_R", "W2_theta", "W2_theta_lo",
            "W2_theta_hi", "sign_W1", "p_W2", "p_holm", "holm_reject", "verdict"]
    print(cells[[c for c in show if c in cells.columns]].to_string())
    print(json.dumps(clean({k: res[k] for k in ("exclusions", "n_races_eligible", "premium", "plus10")}),
                     ensure_ascii=False))
    print(json.dumps(clean(calib), ensure_ascii=False))


if __name__ == "__main__":
    main()
