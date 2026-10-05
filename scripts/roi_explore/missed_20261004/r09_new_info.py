"""R09_new_info — 新しい情報源のセルを全行の較正残差でふるう(段 1)。通過セルがあれば段 2(EV 補正 1 本)。

事前登録: artifacts/roi_explore/missed_20261004/R09_new_info/prereg.json(sha256 を report に記録)。

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r09_new_info.py --stage cells
    cd training && uv run python ../scripts/roi_explore/missed_20261004/r09_new_info.py --stage sieve
    cd training && uv run python ../scripts/roi_explore/missed_20261004/r09_new_info.py --stage fpr

DB は読み取り(SELECT)のみ。製品コードは変更しない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import subprocess
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r09_cells as cells  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
ROWS = ROOT / "artifacts" / "market_ev" / "rows_2007.parquet"
RES = ROOT / "artifacts" / "roi_explore" / "results"
OUT = ROOT / "artifacts" / "roi_explore" / "missed_20261004" / "R09_new_info"
PREREG = OUT / "prereg.json"
ENS_RUNS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
DB_URL = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
SETTLE_LAST = "2026-06-26"
PERIODS = {"discovery": (2010, 2015), "qualification": (2016, 2018), "diag_2019_2026H1": (2019, 2026)}
BANDS = [(0.0, 8.0), (8.0, 20.0), (20.0, 40.0), (40.0, math.inf)]
BAND_LABELS = ["<8", "8-20", "20-40", ">=40"]
BOOT_B = 4000
BOOT_SEED = 20261004
ALPHA = 0.05
HINDSIGHT = {"X08a_owner_low_ae"}


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def load_all():
    rows = pd.read_parquet(ROWS)
    rows["race_id"] = rows["race_id"].astype(str)
    rows["horse_id"] = rows["horse_id"].astype(str)
    ens = None
    inputs = []
    for s, tag in ENS_RUNS.items():
        f = RES / tag / "predictions.parquet"
        v = pd.read_parquet(f)[["race_id", "horse_id", "pred"]]
        v["race_id"] = v["race_id"].astype(str)
        v["horse_id"] = v["horse_id"].astype(str)
        v = v.rename(columns={"pred": f"pred{s}"})
        inputs.append({"seed": s, "path": str(f.relative_to(ROOT)), "sha256": sha(f)})
        rows = rows.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    P = np.column_stack([1.0 + rows[f"pred{s}"].to_numpy(float) / 100.0 for s in ENS_RUNS])
    ens = P.mean(axis=1)  # NaN if any seed missing
    rows = rows.drop(columns=[f"pred{s}" for s in ENS_RUNS])
    from sqlalchemy import create_engine, text
    eng = create_engine(DB_URL)
    with eng.connect() as c:
        horses = pd.read_sql(text("select horse_id, horse_name, birth_year, sire_name, owner_name from horses"), c)
    eng.dispose()
    return rows, ens, horses, inputs


def population_mask(d: pd.DataFrame, ens: np.ndarray) -> np.ndarray:
    return (d["race_ok"].to_numpy(bool) & ~d["dead_heat"].to_numpy(bool) & (d["year"].to_numpy() >= 2010)
            & (d["race_date"].astype(str).str.slice(0, 10).to_numpy() <= SETTLE_LAST) & np.isfinite(ens))


def band_index(odds: np.ndarray) -> np.ndarray:
    b = np.full(odds.shape, -1)
    for i, (lo, hi) in enumerate(BANDS):
        b[(odds >= lo) & (odds < hi)] = i
    return b


# ------------------------------------------------------------------------------------------ estimator
class Period:
    """Per-day aggregates of one period for the complement-calibrated ratio L_adj."""

    def __init__(self, day_idx, year_of_day, band, won, ph, cell_mask_matrix):
        # day_idx: 0..D-1 within period; won/ph: per row; cell_mask_matrix: (rows, K) bool
        self.D = int(day_idx.max()) + 1
        self.year_of_day = year_of_day
        idx = day_idx * 4 + band
        n = self.D * 4
        self.tot_w = np.bincount(idx, weights=won, minlength=n).reshape(self.D, 4)
        self.tot_p = np.bincount(idx, weights=ph, minlength=n).reshape(self.D, 4)
        K = cell_mask_matrix.shape[1]
        self.cw = np.zeros((K, self.D, 4))
        self.cp = np.zeros((K, self.D, 4))
        for k in range(K):
            m = cell_mask_matrix[:, k]
            self.cw[k] = np.bincount(idx[m], weights=won[m], minlength=n).reshape(self.D, 4)
            self.cp[k] = np.bincount(idx[m], weights=ph[m], minlength=n).reshape(self.D, 4)
        self.years = np.unique(year_of_day)

    def l_adj(self, wts: np.ndarray, k: int) -> np.ndarray:
        """wts: (B, D) day weights. Returns L_adj per replicate for cell k (complement calibration per year×band)."""
        out_num = np.zeros(wts.shape[0])
        out_den = np.zeros(wts.shape[0])
        cw, cp = self.cw[k], self.cp[k]
        ow, op = self.tot_w - cw, self.tot_p - cp
        for y in self.years:
            sel = self.year_of_day == y
            W = wts[:, sel]
            cW = W @ cw[sel]  # (B,4)
            cP = W @ cp[sel]
            oW = W @ ow[sel]
            oP = W @ op[sel]
            with np.errstate(invalid="ignore", divide="ignore"):
                c = np.where(oP > 0, oW / oP, 1.0)
            out_num += cW.sum(axis=1)
            out_den += (cP * c).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return out_num / out_den

    def l_adj_by_year(self, k: int) -> dict:
        res = {}
        cw, cp = self.cw[k], self.cp[k]
        ow, op = self.tot_w - cw, self.tot_p - cp
        for y in self.years:
            sel = self.year_of_day == y
            c = np.where(op[sel].sum(0) > 0, ow[sel].sum(0) / np.maximum(op[sel].sum(0), 1e-12), 1.0)
            den = (cp[sel].sum(0) * c).sum()
            res[int(y)] = (float(cw[sel].sum() / den) if den > 0 else None, float(cw[sel].sum()), float(den))
        return res

    def l_adj_by_band(self, k: int) -> list:
        out = []
        cw, cp = self.cw[k], self.cp[k]
        ow, op = self.tot_w - cw, self.tot_p - cp
        for b in range(4):
            num = 0.0
            den = 0.0
            for y in self.years:
                sel = self.year_of_day == y
                o_p = op[sel, b].sum()
                c = ow[sel, b].sum() / o_p if o_p > 0 else 1.0
                num += cw[sel, b].sum()
                den += cp[sel, b].sum() * c
            out.append({"band": BAND_LABELS[b], "wins": num, "expected_adj": den,
                        "L_adj": (num / den) if den > 0 else None})
        return out

    def boot_weights(self, B: int, rng: np.random.Generator) -> np.ndarray:
        W = np.zeros((B, self.D))
        for y in self.years:
            idx = np.flatnonzero(self.year_of_day == y)
            W[:, idx] = rng.multinomial(idx.size, np.full(idx.size, 1.0 / idx.size), size=B)
        return W


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def holm(pvals: dict, alpha: float) -> dict:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out = {k: False for k in pvals}
    for i, (k, p) in enumerate(items):
        if p <= alpha / (m - i):
            out[k] = True
        else:
            break
    adj = {}
    running = 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adj[k] = running
    return out, adj


def sieve(periods: dict, cell_ids, B: int, seed: int) -> dict:
    """Stage-1 decision for all cells given Period objects for discovery/qualification (+diag)."""
    res = {}
    rng = np.random.default_rng(seed)
    wts = {name: P.boot_weights(B, rng) for name, P in periods.items()}
    ones = {name: np.ones((1, P.D)) for name, P in periods.items()}
    for k, cid in enumerate(cell_ids):
        r = {}
        for name, P in periods.items():
            point = float(P.l_adj(ones[name], k)[0])
            reps = P.l_adj(wts[name], k)
            reps = reps[np.isfinite(reps)]
            se = float(np.std(reps, ddof=1)) if reps.size > 1 else float("nan")
            r[name] = {"L_adj": point, "se": se, "z": (point - 1.0) / se if se and se > 0 else float("nan"),
                       "ci95": [float(np.quantile(reps, 0.025)), float(np.quantile(reps, 0.975))] if reps.size else None,
                       "wins": float(P.cw[k].sum()),
                       "expected_adj": float(P.cp[k].sum())}
        res[cid] = r
    # decisions
    pq = {}
    for cid in cell_ids:
        dsc, qual = res[cid]["discovery"], res[cid]["qualification"]
        disc_ok = np.isfinite(dsc["z"]) and abs(dsc["L_adj"] - 1.0) > 2.0 * dsc["se"]
        sign = 1.0 if dsc["L_adj"] > 1.0 else -1.0
        if disc_ok and np.isfinite(qual["z"]):
            p_one = norm_sf(sign * qual["z"])
        else:
            p_one = 1.0
        same_sign = (qual["L_adj"] - 1.0) * (dsc["L_adj"] - 1.0) > 0
        res[cid]["decision"] = {"discovery_2se": bool(disc_ok), "direction": "over" if sign > 0 else "under",
                                "qual_same_sign": bool(same_sign),
                                "qual_2se": bool(np.isfinite(qual["z"]) and abs(qual["L_adj"] - 1.0) > 2.0 * qual["se"]),
                                "qual_p_one_sided": p_one}
        pq[cid] = p_one
    rej, adj = holm(pq, ALPHA)
    for cid in cell_ids:
        dd = res[cid]["decision"]
        dd["holm_adj_p"] = adj[cid]
        dd["holm_reject"] = rej[cid]
        dd["passed"] = bool(dd["discovery_2se"] and dd["qual_same_sign"] and dd["qual_2se"] and rej[cid])
    return res


def build_periods(d, pop, won, ph, cell_ids, include_diag=True):
    out = {}
    years = d["year"].to_numpy()
    dates = d["race_date"].astype(str).str.slice(0, 10).to_numpy()
    band = band_index(d["odds"].to_numpy(float))
    M = d[list(cell_ids)].to_numpy(bool)
    for name, (y0, y1) in PERIODS.items():
        if name.startswith("diag") and not include_diag:
            continue
        m = pop & (years >= y0) & (years <= y1)
        ud, di = np.unique(dates[m], return_inverse=True)
        yod = np.array([int(x[:4]) for x in ud])
        out[name] = Period(di, yod, band[m], won[m], ph[m], M[m])
        out[name].n_rows = M[m].sum(axis=0)
        out[name].n_days = len(ud)
        out[name].day_keys = ud
    return out


def stage_cells(args):
    rows, ens, horses, inputs = load_all()
    log(f"rows {len(rows)}; building cells (covariates only)")
    d, meta = cells.build_cells(rows, horses, prev_ens_ev=ens)
    pop = population_mask(d, ens)
    keep = ["race_id", "horse_id"] + list(cells.CELL_IDS) + ["jockey_delta", "prev_level", "owner_ae", "sire_dirt_ae",
                                                            "trainer_region"]
    d[keep].to_parquet(OUT / "cells.parquet", index=False)
    years = d["year"].to_numpy()
    counts = {}
    for name, (y0, y1) in PERIODS.items():
        m = pop & (years >= y0) & (years <= y1)
        counts[name] = {c: int(d.loc[m, c].sum()) for c in cells.CELL_IDS} | {"_population": int(m.sum())}
    # pairwise indicator correlation over the population (covariates)
    M = d.loc[pop, list(cells.CELL_IDS)].to_numpy(float)
    corr = np.corrcoef(M.T)
    out = {"meta_quantiles": meta, "counts": counts,
           "indicator_corr_max_offdiag": float(np.nanmax(np.abs(corr - np.eye(len(corr))))),
           "inputs": inputs}
    (OUT / "cells_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
    print(json.dumps(out, ensure_ascii=False, indent=1, default=float))


def stage_sieve(args):
    prereg_sha = sha(PREREG)
    rows, ens, horses, inputs = load_all()
    d, meta = cells.build_cells(rows, horses, prev_ens_ev=ens)
    pop = population_mask(d, ens)
    odds = d["odds"].to_numpy(float)
    won = d["won"].to_numpy(bool).astype(float)
    ph = ens / odds
    q = d["q"].to_numpy(float)
    # race-normalised p̂
    rid = d["race_id"].to_numpy()
    s = pd.Series(np.where(pop, ph, 0.0)).groupby(rid).transform("sum").to_numpy()
    ph_norm = np.where(pop & (s > 0), ph / np.where(s > 0, s, 1.0), np.nan)
    cell_ids = list(cells.CELL_IDS)
    log("primary sieve (p̂ ens15, complement-calibrated by year×band, year-stratified day bootstrap)")
    periods = build_periods(d, pop, won, ph, cell_ids)
    res = sieve(periods, cell_ids, BOOT_B, BOOT_SEED)
    years = d["year"].to_numpy()
    for cid in cell_ids:
        for name, P in periods.items():
            k = cell_ids.index(cid)
            r = res[cid][name]
            r["n"] = int(P.n_rows[k])
            byy = P.l_adj_by_year(k)
            vals = [v[0] for v in byy.values() if v[0] is not None]
            r["year_weighted_L_adj"] = float(np.mean(vals)) if vals else None
            r["by_year"] = {str(y): v[0] for y, v in byy.items()}
            r["by_band"] = P.l_adj_by_band(k)
            m = pop & (years >= PERIODS[name][0]) & (years <= PERIODS[name][1]) & d[cid].to_numpy(bool)
            r["L_raw"] = float(won[m].sum() / ph[m].sum()) if m.any() else None
            r["L_norm"] = float(won[m].sum() / ph_norm[m].sum()) if m.any() else None
            r["L_q"] = float(won[m].sum() / q[m].sum()) if m.any() else None
            r["mean_odds"] = float(odds[m].mean()) if m.any() else None
    # eval-implementation cross-check (fixed calibration c, race_block_ratio_bootstrap_ci_v1) for discovery/qual
    from horseracing_eval.bootstrap import race_block_ratio_bootstrap_ci_v1
    for name in ("discovery", "qualification"):
        P = periods[name]
        for k, cid in enumerate(cell_ids):
            cw, cp = P.cw[k], P.cp[k]
            ow, op = P.tot_w - cw, P.tot_p - cp
            c = np.ones((P.D, 4))
            for y in P.years:
                sel = P.year_of_day == y
                cc = ow[sel].sum(0) / np.maximum(op[sel].sum(0), 1e-12)
                c[sel] = cc
            num = cw.sum(1)
            den = (cp * c).sum(1)
            br = race_block_ratio_bootstrap_ci_v1(num, den, list(P.day_keys), block="race_day", b=4000, seed=BOOT_SEED)
            reps = br.replicates[0]
            res[cid][name]["evalimpl_fixed_c"] = {"point": float(br.point[0]), "ci95": [float(br.ci_low[0]), float(br.ci_high[0])],
                                                  "se": float(np.nanstd(reps, ddof=1))}
    # descriptive ROI (diagnostic only): win, stored odds × 100 yen, races <= SETTLE_LAST (population already)
    from horseracing_eval.bootstrap import centered_one_sided_p_from_replicates
    band = band_index(odds)
    pay = won * odds * 100.0
    dates = d["race_date"].astype(str).str.slice(0, 10).to_numpy()
    for name, (y0, y1) in PERIODS.items():
        base = pop & (years >= y0) & (years <= y1)
        for cid in cell_ids:
            cm = base & d[cid].to_numpy(bool)
            comp = base & ~d[cid].to_numpy(bool)
            if not cm.any():
                continue
            # stratum-matched parent ROI: complement ROI per (year, band) weighted by the cell's bet counts
            exp_ret = 0.0
            for y in range(y0, y1 + 1):
                for b in range(4):
                    s_c = cm & (years == y) & (band == b)
                    nc = int(s_c.sum())
                    if nc == 0:
                        continue
                    s_o = comp & (years == y) & (band == b)
                    exp_ret += nc * (pay[s_o].sum() / (100.0 * s_o.sum()) if s_o.any() else 1.0)
            n = int(cm.sum())
            g = pd.DataFrame({"d": dates[cm], "pay": pay[cm]}).groupby("d", sort=True)["pay"].agg(["sum", "size"])
            br = race_block_ratio_bootstrap_ci_v1(g["sum"].to_numpy(float), 100.0 * g["size"].to_numpy(float),
                                                  list(g.index), block="race_day", b=20000, seed=20260905)
            pt = float(br.point[0])
            yr = []
            for y in range(y0, y1 + 1):
                sy = cm & (years == y)
                if sy.any():
                    yr.append(pay[sy].sum() / (100.0 * sy.sum()))
            res[cid][name]["roi_diag"] = {
                "n": n, "hits": int(won[cm].sum()), "roi": pt, "ci95": [float(br.ci_low[0]), float(br.ci_high[0])],
                "p_roi_gt_1": float(centered_one_sided_p_from_replicates(br.replicates[0], pt)),
                "year_weighted_roi": float(np.mean(yr)) if yr else None,
                "parent_matched_roi": exp_ret / n, "roi_over_parent": pt / (exp_ret / n) if exp_ret > 0 else None}
    passed = [c for c in cell_ids if res[c]["decision"]["passed"]]
    eligible_stage2 = [c for c in passed if c not in HINDSIGHT]
    out = {"test_id": "R09_new_info", "prereg_sha256": prereg_sha, "git_commit": git_commit(),
           "rows_sha256": sha(ROWS), "inputs": inputs, "meta_quantiles": meta,
           "population_rows": {n: int((pop & (years >= a) & (years <= b)).sum()) for n, (a, b) in PERIODS.items()},
           "n_days": {n: int(P.n_days) for n, P in periods.items()},
           "overall_raw_L": {n: float(won[pop & (years >= a) & (years <= b)].sum() / ph[pop & (years >= a) & (years <= b)].sum())
                             for n, (a, b) in PERIODS.items()},
           "cells": res, "passed": passed, "eligible_stage2": eligible_stage2,
           "stage2_run": bool(eligible_stage2)}
    (OUT / "sieve.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
    log(f"passed={passed} eligible_stage2={eligible_stage2}")
    for cid in cell_ids:
        r = res[cid]
        dd = r["decision"]
        print(f"{cid:34s} n_d={r['discovery']['n']:6d} L_d={r['discovery']['L_adj']:.3f}±{r['discovery']['se']:.3f} "
              f"n_q={r['qualification']['n']:6d} L_q={r['qualification']['L_adj']:.3f}±{r['qualification']['se']:.3f} "
              f"diag={r['diag_2019_2026H1']['L_adj']:.3f}±{r['diag_2019_2026H1']['se']:.3f} "
              f"p1={dd['qual_p_one_sided']:.4f} holm={dd['holm_adj_p']:.4f} pass={dd['passed']}")


def stage_fpr(args):
    """Pipeline false-pass rate: winners re-drawn from race-normalised p̂ (cells fixed), same sieve."""
    rows, ens, horses, inputs = load_all()
    d, meta = cells.build_cells(rows, horses, prev_ens_ev=ens)
    pop = population_mask(d, ens)
    odds = d["odds"].to_numpy(float)
    ph = ens / odds
    cell_ids = list(cells.CELL_IDS)
    years = d["year"].to_numpy()
    sub = pop & (years <= 2018)
    idx = np.flatnonzero(sub)
    rid = d["race_id"].to_numpy()[idx]
    order = np.argsort(rid, kind="stable")
    idx = idx[order]
    rid = rid[order]
    starts = np.flatnonzero(np.r_[True, rid[1:] != rid[:-1]])
    ends = np.r_[starts[1:], len(rid)]
    p = ph[idx]
    gsum = np.add.reduceat(p, starts)
    pn = p / np.repeat(gsum, ends - starts)
    cum = np.cumsum(pn)
    base = np.repeat(np.r_[0.0, cum[ends - 1][:-1]], ends - starts)
    within = cum - base  # cumulative within race, last = 1
    rng = np.random.default_rng(args.fpr_seed)
    reps = args.fpr_reps
    n_pass = np.zeros(len(cell_ids), dtype=int)
    any_pass = 0
    for r in range(reps):
        u = rng.random(len(starts))
        uu = np.repeat(u, ends - starts)
        prev_within = within - pn
        won_sim_sorted = ((uu >= prev_within) & (uu < within)).astype(float)
        won_sim = np.zeros(len(d))
        won_sim[idx] = won_sim_sorted
        periods = build_periods(d, sub, won_sim, ph, cell_ids, include_diag=False)
        res = sieve(periods, cell_ids, args.fpr_b, BOOT_SEED + 1000 + r)
        pv = np.array([res[c]["decision"]["passed"] for c in cell_ids])
        n_pass += pv
        any_pass += int(pv.any())
        if (r + 1) % 10 == 0:
            log(f"fpr rep {r + 1}/{reps}: any_pass so far {any_pass}")
    out = {"reps": reps, "boot_b": args.fpr_b, "seed": args.fpr_seed, "familywise_false_pass_rate": any_pass / reps,
           "per_cell_false_pass_rate": {c: int(n) / reps for c, n in zip(cell_ids, n_pass)},
           "prereg_sha256": sha(PREREG)}
    (OUT / "fpr.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["cells", "sieve", "fpr"], required=True)
    ap.add_argument("--fpr-reps", type=int, default=200)
    ap.add_argument("--fpr-b", type=int, default=1000)
    ap.add_argument("--fpr-seed", type=int, default=20261005)
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    {"cells": stage_cells, "sieve": stage_sieve, "fpr": stage_fpr}[args.stage](args)


if __name__ == "__main__":
    main()
