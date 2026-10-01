"""2007+ 市場連動モデルの上の買い方探索(docs/roi-pattern-exploration-20260930/design.md の機械化)。

手順: 候補集合を凍結 → 発見 D(2010–2015)/資格 Q(2016–2018)で生存を決め JSON に凍結 → 生存集合だけ確認 C(2019–)で
帰無中心化 day-cluster bootstrap 片側 p + Holm。非生存の C は別ファイル(判定に使わない)。

    cd training && uv run python ../scripts/roi_explore/patterns_2007.py [--null-reps 50]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import evaluate as ev  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
ART = REPO / "artifacts"
ROWS = ART / "market_ev" / "rows_2007_withp.parquet"
RES = ART / "roi_explore" / "results"
BASE_TAG = "armC_binary_drop-sameday+weightlive_serving_v2_2007"
VARIANTS = {  # F4: tag dir → label
    "seed2": "armC_binary_seed2_drop-sameday+weightlive_from2007",
    "seed3": "armC_binary_seed3_drop-sameday+weightlive_from2007",
    "r600": "armC_binary_drop-sameday+weightlive_from2007_r600",
    "regression": "armC_regression_drop-sameday+weightlive_from2007",
    "huber": "armC_huber_drop-sameday+weightlive_from2007",
    "withp": "armC_binary_withp_drop-sameday+weightlive_from2007",
    "rolling8": "armC_binary_drop-sameday+weightlive_from2007_win8",
}
WIN = {"D": (2010, 2015), "Q": (2016, 2018), "C": (2019, 2026)}
DEGRADE = {"min_hits": 20, "min_days": 100, "max_hit_share": 0.5}
OUT = RES / "patterns_2007"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# ----------------------------------------------------------------------------- data
def load(with_variants: bool = True) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    rows = pd.read_parquet(ROWS)
    base = pd.read_parquet(RES / BASE_TAG / "predictions.parquet")[["race_id", "horse_id", "pred"]]
    df = rows.merge(base, on=["race_id", "horse_id"], how="inner")
    assert len(df) == len(base), (len(df), len(base))
    df = df.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    preds = {"base": df["pred"].to_numpy(dtype=float)}
    if with_variants:
        for k, tag in VARIANTS.items():
            f = RES / tag / "predictions.parquet"
            if not f.exists():
                log(f"variant {k}: missing {f} (skipped)"); continue
            v = pd.read_parquet(f)[["race_id", "horse_id", "pred"]].rename(columns={"pred": "pv"})
            m = df[["race_id", "horse_id"]].merge(v, on=["race_id", "horse_id"], how="left")
            assert len(m) == len(df)
            preds[k] = m["pv"].to_numpy(dtype=float)
        if "seed2" in preds and "seed3" in preds:
            preds["avg3"] = (preds["base"] + preds["seed2"] + preds["seed3"]) / 3.0
    return df, preds


# ----------------------------------------------------------------------------- patterns
class P:
    __slots__ = ("pid", "family", "label", "mask", "stake")

    def __init__(self, pid, family, label, mask, stake=None):
        self.pid, self.family, self.label = pid, family, label
        self.mask = np.asarray(mask, dtype=bool)
        self.stake = np.full(len(self.mask), 100.0) if stake is None else np.asarray(stake, dtype=float)


def _band(x: np.ndarray, edges: list[float], labels: list[str]) -> dict[str, np.ndarray]:
    out = {}
    for lo, hi, lab in zip(edges[:-1], edges[1:], labels):
        out[lab] = (x >= lo) & (x < hi)
    return out


def race_max_mask(race_idx: np.ndarray, val: np.ndarray, within: np.ndarray | None = None) -> np.ndarray:
    v = val.copy()
    if within is not None:
        v[~within] = -np.inf
    v[np.isnan(v)] = -np.inf
    mx = pd.Series(v).groupby(race_idx).transform("max").to_numpy()
    return (v == mx) & np.isfinite(v)


def fit_recal(df: pd.DataFrame, ev_base: np.ndarray, won: np.ndarray, method: str) -> np.ndarray:
    """F5: 年 y の p̂ を、y より前の年の OOS 予測(EV>1.0 の候補行)と結果で再校正。返り値=再校正後 EV。"""
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression

    years = df["year"].to_numpy(); odds = df["odds"].to_numpy(dtype=float)
    p_hat = np.clip(ev_base / odds, 1e-6, 1 - 1e-6)
    cand = ev_base > 1.0
    out = np.full(len(df), np.nan)
    for y in range(2011, 2027):
        tr = cand & (years < y) & (years >= 2010); te = cand & (years == y)
        if tr.sum() < 500 or te.sum() == 0:
            continue
        if method == "isotonic":
            iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1 - 1e-6).fit(p_hat[tr], won[tr])
            pc = iso.predict(p_hat[te])
        else:
            Xtr = np.c_[np.log(p_hat[tr] / (1 - p_hat[tr])), np.log(odds[tr])]
            Xte = np.c_[np.log(p_hat[te] / (1 - p_hat[te])), np.log(odds[te])]
            lr = LogisticRegression(C=1e6, max_iter=1000).fit(Xtr, won[tr])
            pc = lr.predict_proba(Xte)[:, 1]
        out[te] = pc * odds[te]
    return out


def build_patterns(df: pd.DataFrame, preds: dict[str, np.ndarray], won: np.ndarray) -> list[P]:
    odds = df["odds"].to_numpy(dtype=float)
    race_idx = pd.factorize(df["race_id"])[0]
    evb = 1.0 + preds["base"] / 100.0
    p_hat = evb / odds
    base = evb > 1.2
    pats: list[P] = []

    # F1 threshold × cap × stake
    stakes = {"flat": np.full(len(df), 100.0), "inv": 100.0 / odds,
              "edge": np.clip((evb - 1.0) / np.maximum(odds - 1.0, 1e-9), 0, None) * 100.0}
    for t in (1.1, 1.2, 1.3, 1.5):
        for cap, cl in ((None, "nocap"), (21.0, "odds<21"), (50.0, "odds<50")):
            m = evb > t
            if cap:
                m = m & (odds < cap)
            for sk, sv in stakes.items():
                pats.append(P(f"F1.t{t}.{cl}.{sk}", "F1", f"EV>{t} {cl} stake={sk}", m, sv))

    # F2 segments (EV>1.2 & cell)
    seg: dict[str, dict[str, np.ndarray]] = {}
    seg["odds"] = _band(odds, [1, 3, 5, 10, 20, 50, np.inf], ["1-3", "3-5", "5-10", "10-20", "20-50", "50+"])
    pop = df["popularity"].to_numpy(dtype=float)
    seg["pop"] = _band(pop, [1, 2, 4, 7, 10, np.inf], ["1", "2-3", "4-6", "7-9", "10+"])
    fs = df["field_size"].to_numpy(dtype=float)
    seg["field"] = _band(fs, [1, 9, 13, 16, np.inf], ["<=8", "9-12", "13-15", "16+"])
    for col, key in (("track_type", "track"), ("dist_band", "dist"), ("race_class_canon", "class"),
                     ("venue_code", "venue"), ("going", "going"), ("sex", "sex")):
        s = df[col].astype(object)
        seg[key] = {str(v): (s == v).to_numpy() for v in sorted(s.dropna().unique())}
    mo = df["month"].to_numpy(dtype=float)
    seg["season"] = {"winter": np.isin(mo, [12, 1, 2]), "spring": np.isin(mo, [3, 4, 5]),
                     "summer": np.isin(mo, [6, 7, 8]), "autumn": np.isin(mo, [9, 10, 11])}
    rn = df["race_number"].to_numpy(dtype=float)
    seg["rno"] = _band(rn, [1, 5, 9, 13], ["1-4", "5-8", "9-12"])
    age = df["age"].to_numpy(dtype=float)
    seg["age"] = _band(age, [2, 3, 4, 5, np.inf], ["2", "3", "4", "5+"])
    cs = df["career_starts"].to_numpy(dtype=float)
    seg["career"] = _band(cs, [0, 1, 4, 10, 20, np.inf], ["0", "1-3", "4-9", "10-19", "20+"])
    dsl_ = df["days_since_last"].to_numpy(dtype=float)
    seg["gap"] = _band(dsl_, [0, 14, 36, 91, np.inf], ["<14", "14-35", "36-90", ">90"])
    ig = df["is_graded"].astype(bool).to_numpy()
    seg["graded"] = {"graded": ig, "not_graded": ~ig}
    jw = df["jockey_win_rate_365"].to_numpy(dtype=float)
    seg["jockey"] = _band(jw, [0, 0.05, 0.10, 0.15, np.inf], ["<5%", "5-10%", "10-15%", ">=15%"])
    pf = df["prev_finish"].to_numpy(dtype=float)
    seg["prevfin"] = _band(pf, [1, 2, 4, 7, 10, np.inf], ["1", "2-3", "4-6", "7-9", "10+"])
    cc = df["class_change"].to_numpy(dtype=float)
    seg["classchg"] = {"down": cc == -1, "same": cc == 0, "up": cc == 1}
    qe = df["q_entropy_norm"].to_numpy(dtype=float)
    seg["entropy"] = _band(qe, [0, 0.7, 0.8, 0.9, np.inf], ["<0.7", "0.7-0.8", "0.8-0.9", ">=0.9"])
    nq = pd.Series(base.astype(int)).groupby(race_idx).transform("sum").to_numpy()
    seg["nqual"] = {"1": nq == 1, "2": nq == 2, "3+": nq >= 3}
    for ax, cells in seg.items():
        for lab, cm in cells.items():
            pats.append(P(f"F2.{ax}.{lab}", "F2", f"EV>1.2 & {ax}={lab}", base & cm))

    # F3 within-race selection
    top_ev = race_max_mask(race_idx, evb)
    for t in (1.1, 1.2, 1.3):
        pats.append(P(f"F3.top1ev.t{t}", "F3", f"race top-1 by EV & EV>{t}", top_ev & (evb > t)))
    pats.append(P("F3.top1p_among", "F3", "EV>1.2, highest p̂ among qualifiers", race_max_mask(race_idx, p_hat, within=base)))
    pats.append(P("F3.p_race_fav", "F3", "EV>1.2 & p̂ is race max", base & race_max_mask(race_idx, p_hat)))

    # F4 model variants
    for k in ("seed2", "seed3", "avg3", "r600", "regression", "huber", "withp", "rolling8"):
        if k not in preds:
            continue
        evk = 1.0 + preds[k] / 100.0
        m = evk > 1.2
        pats.append(P(f"F4.{k}.ev1.2", "F4", f"variant {k}: EV>1.2", m))
        pats.append(P(f"F4.{k}.ev1.2.odds<21", "F4", f"variant {k}: EV>1.2 & odds<21", m & (odds < 21)))

    # F5 recalibration on prior-year OOS candidates
    for meth in ("isotonic", "logistic"):
        evc = fit_recal(df, evb, won, meth)
        for t in (1.1, 1.2):
            pats.append(P(f"F5.{meth}.ev{t}", "F5", f"recal({meth}) EV>{t}", np.nan_to_num(evc, nan=-1) > t))

    # F7 agreement with production p (2008+)
    pp = df["p"].to_numpy(dtype=float); evp = pp * odds
    prank = df["p_rank"].to_numpy(dtype=float)
    pats.append(P("F7.ev1.2.prod1.0", "F7", "EV>1.2 & prod p×odds>1.0", base & (np.nan_to_num(evp, nan=-1) > 1.0)))
    pats.append(P("F7.ev1.2.prod0.8", "F7", "EV>1.2 & prod p×odds>0.8", base & (np.nan_to_num(evp, nan=-1) > 0.8)))
    pats.append(P("F7.ev1.2.prodfav", "F7", "EV>1.2 & prod p rank 1", base & (np.nan_to_num(prank, nan=99) == 1)))
    pats.append(P("F7.ev1.1.prod1.0", "F7", "EV>1.1 & prod p×odds>1.0", (evb > 1.1) & (np.nan_to_num(evp, nan=-1) > 1.0)))
    return pats


def controls(df: pd.DataFrame, preds: dict[str, np.ndarray]) -> list[P]:
    odds = df["odds"].to_numpy(dtype=float); evb = 1.0 + preds["base"] / 100.0
    pop = df["popularity"].to_numpy(dtype=float)
    return [P("CTRL.all", "CTRL", "all horses", np.ones(len(df), bool)),
            P("CTRL.odds<21", "CTRL", "odds<21", odds < 21),
            P("CTRL.fav", "CTRL", "popularity==1", pop == 1),
            P("CTRL.base", "CTRL", "EV>1.2 flat (base policy)", evb > 1.2)]


# ----------------------------------------------------------------------------- scoring
def window_stats(p: P, payout_unit: np.ndarray, years: np.ndarray, day_idx: np.ndarray, w: tuple[int, int]) -> dict:
    sel = np.flatnonzero(p.mask & (years >= w[0]) & (years <= w[1]))
    if len(sel) == 0:
        return {"n_bets": 0}
    st = p.stake[sel]; po = payout_unit[sel] * st / 100.0
    tot_s, tot_p = st.sum(), po.sum()
    yrs = years[sel]
    yearly = {int(y): round(float(po[yrs == y].sum() / st[yrs == y].sum()), 4) for y in np.unique(yrs)}
    hits = po > 0
    mx = float(po.max()) if len(po) else 0.0
    return {"n_bets": int(len(sel)), "n_hits": int(hits.sum()), "roi": float(tot_p / tot_s),
            "n_days": int(len(np.unique(day_idx[sel]))), "yearly": yearly,
            "years_ge1": int(sum(v >= 1.0 for v in yearly.values())), "years_n": len(yearly),
            "max_hit_share": float(mx / tot_p) if tot_p > 0 else 0.0,
            "roi_loo": float((tot_p - mx) / tot_s), "mean_odds": float(np.mean(df_odds[sel]))}


def survives(st: dict) -> tuple[bool, str]:
    if st.get("n_bets", 0) == 0:
        return False, "no_bets"
    if st["n_hits"] < DEGRADE["min_hits"]:
        return False, "few_hits"
    if st["n_days"] < DEGRADE["min_days"]:
        return False, "few_days"
    if st["max_hit_share"] > DEGRADE["max_hit_share"]:
        return False, "hit_concentration"
    if st["roi"] < 1.0:
        return False, "roi<1"
    return True, "ok"


def confirm_stats(p: P, payout_unit: np.ndarray, years: np.ndarray, day_idx: np.ndarray, b: int) -> dict:
    w = WIN["C"]
    st = window_stats(p, payout_unit, years, day_idx, w)
    sel = np.flatnonzero(p.mask & (years >= w[0]) & (years <= w[1]))
    if len(sel) == 0:
        return st
    stake = p.stake; payout = payout_unit * stake / 100.0
    arr = {"_day_idx": day_idx}
    st["boot"] = ev.day_bootstrap(stake, payout, sel, arr, b=b)
    ds, dp = ev.day_aggregates(stake, payout, sel, day_idx)
    st["p_one_sided"] = ev.centered_pvalue(ds, dp, b=b)["p_one_sided"]
    # codex 2026-09-30 診断: 年ブロック別 ROI と leave-one-year-out ROI(一時期依存の検出・判定には使わない)
    ys = years[sel]; st_sel = stake[sel]; po_sel = payout[sel]
    blocks = {"2019-20": (2019, 2020), "2021-22": (2021, 2022), "2023-24": (2023, 2024), "2025-26": (2025, 2026)}
    st["year_blocks"] = {}
    for k, (a, bb) in blocks.items():
        m = (ys >= a) & (ys <= bb)
        st["year_blocks"][k] = round(float(po_sel[m].sum() / st_sel[m].sum()), 4) if st_sel[m].sum() > 0 else None
    st["loyo"] = {}
    for y in np.unique(ys):
        m = ys != y
        st["loyo"][int(y)] = round(float(po_sel[m].sum() / st_sel[m].sum()), 4) if st_sel[m].sum() > 0 else None
    st["loyo_min"] = min(v for v in st["loyo"].values() if v is not None) if st["loyo"] else None
    st["blocks_ge1"] = int(sum(1 for v in st["year_blocks"].values() if v is not None and v >= 1.0))
    return st


df_odds: np.ndarray = np.empty(0)


def run_pipeline(pats: list[P], payout_unit: np.ndarray, years: np.ndarray, day_idx: np.ndarray, *, b: int,
                 verbose: bool = True) -> dict:
    dq = {}
    for p in pats:
        d = window_stats(p, payout_unit, years, day_idx, WIN["D"]); q = window_stats(p, payout_unit, years, day_idx, WIN["Q"])
        okd, rd = survives(d); okq, rq = survives(q) if okd else (False, "not_reached")
        dq[p.pid] = {"family": p.family, "label": p.label, "D": d, "Q": q, "D_ok": okd, "D_reason": rd,
                     "Q_ok": okq, "Q_reason": rq, "survivor": bool(okd and okq)}
    surv = [p for p in pats if dq[p.pid]["survivor"]]
    if verbose:
        log(f"D survivors {sum(v['D_ok'] for v in dq.values())} / Q survivors {len(surv)} / total {len(pats)}")
    conf = {}
    for p in surv:
        conf[p.pid] = confirm_stats(p, payout_unit, years, day_idx, b)
    pv = {k: v["p_one_sided"] for k, v in conf.items() if "p_one_sided" in v}
    hol = ev.holm(pv, alpha=0.025) if pv else {}
    for k in conf:
        conf[k]["holm_reject"] = bool(hol.get(k, False))
        bt = conf[k].get("boot", {})
        conf[k]["weak_candidate"] = bool(conf[k].get("roi", 0) >= 1.0 and bt.get("ci_lo", 0) > 1.0)
    return {"dq": dq, "survivors": [p.pid for p in surv], "confirm": conf,
            "n_holm": int(sum(hol.values())) if hol else 0}


def synthetic_won(df: pd.DataFrame, seed: int) -> np.ndarray:
    """null world: 1 winner per race ~ market q (predictions fixed)."""
    rng = np.random.default_rng(seed)
    race_idx = pd.factorize(df["race_id"])[0]
    starts = np.r_[0, np.flatnonzero(np.diff(race_idx)) + 1, len(race_idx)]
    q = df["q"].to_numpy(dtype=float)
    cq = np.cumsum(q); race_cum0 = np.r_[0.0, cq[starts[1:-1] - 1]]
    race_tot = cq[starts[1:] - 1] - race_cum0
    u = rng.random(len(starts) - 1) * race_tot + race_cum0
    w_rows = np.clip(np.searchsorted(cq, u, side="right"), starts[:-1], starts[1:] - 1)
    won = np.zeros(len(df), dtype=bool); won[w_rows] = True
    return won


def main(argv=None) -> int:
    global df_odds
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--null-reps", type=int, default=0)
    ap.add_argument("--null-boot", type=int, default=2000)
    ap.add_argument("--no-variants", action="store_true")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    df, preds = load(with_variants=not args.no_variants)
    log(f"rows {len(df):,} / preds {sorted(preds)}")
    years = df["year"].to_numpy(); day_idx = pd.factorize(df["race_date"])[0]
    odds = df["odds"].to_numpy(dtype=float); df_odds = odds
    won = df["won"].to_numpy(dtype=bool)
    payout_unit = won * odds * 100.0  # per 100 yen
    pats = build_patterns(df, preds, won)
    (OUT / "patterns.json").write_text(json.dumps(
        [{"pid": p.pid, "family": p.family, "label": p.label} for p in pats], ensure_ascii=False, indent=1))
    log(f"frozen {len(pats)} patterns → patterns.json")
    ctrl = controls(df, preds)
    ctrl_stats = {c.pid: {w: window_stats(c, payout_unit, years, day_idx, WIN[w]) for w in WIN} for c in ctrl}
    res = run_pipeline(pats, payout_unit, years, day_idx, b=args.boot)
    (OUT / "all_DQ.json").write_text(json.dumps(res["dq"], ensure_ascii=False, indent=1))
    (OUT / "survivors.json").write_text(json.dumps(res["survivors"], ensure_ascii=False, indent=1))
    (OUT / "confirm.json").write_text(json.dumps(res["confirm"], ensure_ascii=False, indent=1))
    (OUT / "controls.json").write_text(json.dumps(ctrl_stats, ensure_ascii=False, indent=1))
    # diagnostics only: C for non-survivors (not used for the verdict)
    non = {p.pid: window_stats(p, payout_unit, years, day_idx, WIN["C"]) for p in pats if p.pid not in res["survivors"]}
    (OUT / "nonsurvivors_C.json").write_text(json.dumps(non, ensure_ascii=False, indent=1))
    summary = {"n_patterns": len(pats), "n_D": int(sum(v["D_ok"] for v in res["dq"].values())),
               "n_survivors": len(res["survivors"]), "n_holm": res["n_holm"],
               "n_weak": int(sum(v.get("weak_candidate", False) for v in res["confirm"].values())),
               "by_family": {}, "elapsed_s": round(time.time() - t0, 1)}
    for fam in sorted({p.family for p in pats}):
        ids = [p.pid for p in pats if p.family == fam]
        summary["by_family"][fam] = {"n": len(ids), "D_ok": int(sum(res["dq"][i]["D_ok"] for i in ids)),
                                     "survivors": int(sum(res["dq"][i]["survivor"] for i in ids)),
                                     "holm": int(sum(res["confirm"].get(i, {}).get("holm_reject", False) for i in ids))}
    if args.null_reps:
        log(f"null diagnostic: {args.null_reps} reps")
        nulls = []
        for r in range(args.null_reps):
            w0 = synthetic_won(df, 1000 + r)
            pu = w0 * odds * 100.0
            pats_r = build_patterns(df, preds, w0)
            rr = run_pipeline(pats_r, pu, years, day_idx, b=args.null_boot, verbose=False)
            nulls.append({"rep": r, "n_D": int(sum(v["D_ok"] for v in rr["dq"].values())),
                          "n_survivors": len(rr["survivors"]), "n_holm": rr["n_holm"],
                          "n_weak": int(sum(v.get("weak_candidate", False) for v in rr["confirm"].values())),
                          "all_horse_roi": float(pu.mean() / 100.0)})
            if (r + 1) % 5 == 0:
                log(f"  null rep {r + 1}: surv={nulls[-1]['n_survivors']} holm={nulls[-1]['n_holm']} weak={nulls[-1]['n_weak']}")
        (OUT / "null_diagnostic.json").write_text(json.dumps(nulls, ensure_ascii=False, indent=1))
        summary["null"] = {"reps": len(nulls), "mean_survivors": float(np.mean([n["n_survivors"] for n in nulls])),
                           "holm_ge1_rate": float(np.mean([n["n_holm"] >= 1 for n in nulls])),
                           "weak_ge1_rate": float(np.mean([n["n_weak"] >= 1 for n in nulls])),
                           "max_holm": int(max(n["n_holm"] for n in nulls))}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    log(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
