"""ROI 広域探索 — 評価エンジン(numpy)。

入力: artifacts/roi_explore/rows.parquet(+ dividends.parquet)と DSL パターン JSON 群。
出力: artifacts/roi_explore/results/<run>/ に全パターンの窓別スコア(scores.parquet / scores.json)、
生存者(survivors.json)、対照(controls.json)、帰無シミュレーション(null.json)、拒否一覧(rejects.json)。

    cd training && uv run python ../scripts/roi_explore/evaluate.py --patterns ../artifacts/roi_explore/patterns --run a1

契約(design.md §3):
  1 点 100 円。回収率 = Σ払戻 / Σ賭け金。単勝払戻 = 確定オッズ×100。同着レースは母集団外。
  窓: 市場・文脈系 D=1986-2007 / Q=2008-2018 / C=2019-。モデル系 D=2008-2013 / Q=2014-2018 / C=2019-。
      実配当券種は D=2025 / C=2026。
  生存: D と Q の両方で 点推定 ≥1.00 ∧ 的中 ≥20 ∧ 開催日 ≥100 ∧ 最大 1 的中寄与 ≤50% → C で CI 下限>1 が候補。
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
import dsl  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
ART = REPO / "artifacts" / "roi_explore"

WINDOWS = {
    "market": {"D": (1986, 2007), "Q": (2008, 2018), "C": (2019, 2026)},
    "model": {"D": (2008, 2013), "Q": (2014, 2018), "C": (2019, 2026)},
    "exotic": {"D": (2025, 2025), "C": (2026, 2026)},
}
DEGRADE = {"min_hits": 20, "min_days": 100, "max_hit_share": 0.5}
CONTROLS = [
    {"pattern_id": "ctl.favorite", "family": "control", "label_ja": "1 番人気のみ", "bet_type": "win",
     "horse_filter": [{"field": "is_fav", "op": "is_true"}]},
    {"pattern_id": "ctl.cap21_all", "family": "control", "label_ja": "21 倍未満の全馬", "bet_type": "win",
     "horse_filter": [{"field": "odds", "op": "lt", "value": 21}]},
    {"pattern_id": "ctl.cap11_all", "family": "control", "label_ja": "11 倍未満の全馬", "bet_type": "win",
     "horse_filter": [{"field": "odds", "op": "lt", "value": 11}]},
    {"pattern_id": "ctl.all_horses", "family": "control", "label_ja": "全馬(控除床)", "bet_type": "win",
     "horse_filter": [{"field": "odds", "op": "gt", "value": 0}]},
    {"pattern_id": "ctl.model_fav", "family": "control", "label_ja": "モデル 1 位", "bet_type": "win",
     "horse_filter": [{"field": "p_rank", "op": "eq", "value": 1}]},
    {"pattern_id": "ctl.ev_ge_1", "family": "control", "label_ja": "EV≥1.0 全馬(現行政策)", "bet_type": "win",
     "horse_filter": [{"field": "ev", "op": "ge", "value": 1.0}]},
    {"pattern_id": "ctl.ev_ge_1_cap21", "family": "control", "label_ja": "EV≥1.0 × 21 倍未満", "bet_type": "win",
     "horse_filter": [{"field": "ev", "op": "ge", "value": 1.0}, {"field": "odds", "op": "lt", "value": 21}]},
    {"pattern_id": "ctl.place_fav", "family": "control", "label_ja": "1 番人気の複勝", "bet_type": "place",
     "horse_filter": [{"field": "is_fav", "op": "is_true"}]},
    {"pattern_id": "ctl.quinella_top2", "family": "control", "label_ja": "人気 1-2 の馬連", "bet_type": "quinella",
     "horse_filter": [{"field": "odds_rank", "op": "le", "value": 2}]},
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------------------------


def load_rows() -> dict:
    df = pd.read_parquet(ART / "rows.parquet")
    df = df[df["race_ok"] & ~df["dead_heat"]].reset_index(drop=True)
    df = df.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    arr = {}
    for c in df.columns:
        col = df[c]
        if pd.api.types.is_bool_dtype(col):
            arr[c] = col.fillna(False).to_numpy().astype(float)
        elif pd.api.types.is_numeric_dtype(col):
            arr[c] = col.to_numpy(dtype=float, na_value=np.nan)
        else:
            o = np.array(col.astype(object).tolist(), dtype=object)
            o[pd.isna(o)] = None
            arr[c] = o
    _, race_idx = np.unique(arr["race_id"], return_inverse=True)
    arr["_race_idx"] = race_idx.astype(np.int64)
    days, day_idx = np.unique(arr["race_date"], return_inverse=True)
    arr["_day_idx"] = day_idx.astype(np.int64)
    arr["_days"] = days
    arr["_day_year"] = np.asarray([int(d[:4]) for d in days])
    arr["_won"] = df["won"].to_numpy().astype(float)
    arr["_year"] = df["year"].to_numpy().astype(int)
    return arr


def load_dividends() -> dict:
    dv = pd.read_parquet(ART / "dividends.parquet")
    out = {}
    for bt, g in dv.groupby("bet_type"):
        d = {}
        for rid, sel, odds in zip(g["race_id"], g["selection"], g["odds"]):
            d.setdefault(str(rid), {})[tuple(int(x) for x in json.loads(sel))] = float(odds)
        out[bt] = d
    return out


# ---------------------------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------------------------


def stakes_for(p: dsl.Pattern, arr: dict, mask: np.ndarray) -> np.ndarray:
    n = len(mask)
    stake = np.zeros(n)
    idx = np.flatnonzero(mask)
    if p.stake == "flat" or len(idx) == 0:
        stake[idx] = 100.0
        return stake
    if p.stake == "inverse_odds":
        w = 1.0 / arr["odds"][idx]
    else:  # edge
        w = np.maximum(arr["ev"][idx] - 1.0, 0.0)
        w = np.where(np.isnan(w), 0.0, w)
    r = arr["_race_idx"][idx]
    nr = int(arr["_race_idx"].max()) + 1
    wsum = np.bincount(r, weights=w, minlength=nr)
    cnt = np.bincount(r, minlength=nr).astype(float)
    s = np.where(wsum[r] > 0, 100.0 * cnt[r] * w / np.where(wsum[r] > 0, wsum[r], 1.0), 100.0)
    stake[idx] = s
    return stake


def window_stats(stake: np.ndarray, payout: np.ndarray, idx: np.ndarray, arr: dict, years: tuple[int, int]) -> dict:
    yr = arr["_year"][idx]
    sel = idx[(yr >= years[0]) & (yr <= years[1])]
    if len(sel) == 0:
        return {"n_bets": 0}
    s = stake[sel]; po = payout[sel]
    ssum = float(s.sum()); psum = float(po.sum())
    hits = int((po > 0).sum())
    mx = float(po.max()) if len(po) else 0.0
    days = int(len(np.unique(arr["_day_idx"][sel])))
    races = int(len(np.unique(arr["_race_idx"][sel])))
    yrs = arr["_year"][sel]
    ys = np.bincount(yrs - years[0], weights=s, minlength=years[1] - years[0] + 1)
    yp = np.bincount(yrs - years[0], weights=po, minlength=years[1] - years[0] + 1)
    yearly = {int(years[0] + i): (round(float(yp[i] / ys[i]), 4) if ys[i] > 0 else None) for i in range(len(ys))}
    return {
        "n_bets": int(len(sel)), "n_races": races, "n_days": days, "n_hits": hits,
        "stake": ssum, "payout": psum, "roi": psum / ssum if ssum > 0 else None,
        "max_hit_share": (mx / psum) if psum > 0 else None,
        "loho_roi": (psum - mx) / ssum if ssum > 0 else None,
        "yearly": yearly,
        "years_ge1": int(sum(1 for v in yearly.values() if v is not None and v >= 1.0)),
        "years_n": int(sum(1 for v in yearly.values() if v is not None)),
        "_sel": sel,
    }


def passes(st: dict) -> tuple[bool, str]:
    if st.get("n_bets", 0) == 0:
        return False, "not_fired"
    if st["roi"] is None or st["roi"] < 1.0:
        return False, "roi_lt_1"
    if st["n_hits"] < DEGRADE["min_hits"]:
        return False, "hits_lt_20"
    if st["n_days"] < DEGRADE["min_days"]:
        return False, "days_lt_100"
    if st["max_hit_share"] is not None and st["max_hit_share"] > DEGRADE["max_hit_share"]:
        return False, "single_hit_gt_50pct"
    return True, "pass"


def day_bootstrap(stake: np.ndarray, payout: np.ndarray, sel: np.ndarray, arr: dict, *, b: int = 10000,
                  seed: int = 20260923) -> dict:
    d = arr["_day_idx"][sel]
    ud, inv = np.unique(d, return_inverse=True)
    s = np.bincount(inv, weights=stake[sel]); po = np.bincount(inv, weights=payout[sel])
    n = len(ud)
    rng = np.random.default_rng(seed)
    out = np.empty(b)
    chunk = 1000
    for i in range(0, b, chunk):
        k = min(chunk, b - i)
        cnt = rng.multinomial(n, np.full(n, 1.0 / n), size=k).astype(float)
        out[i:i + k] = (cnt @ po) / (cnt @ s)
    point = po.sum() / s.sum()
    return {"ci_lo": float(np.percentile(out, 2.5)), "ci_hi": float(np.percentile(out, 97.5)),
            "sd": float(out.std()), "p_le_1": float(np.mean(out <= 1.0)), "point": float(point), "n_days": int(n)}


def centered_pvalue(day_stake: np.ndarray, day_payout: np.ndarray, *, b: int = 10000, seed: int = 20260924) -> dict:
    """One-sided p-value for H0: ROI <= 1 via null-centred day-cluster bootstrap (109 FR-003 の型).

    Payouts are rescaled so the pooled ROI is exactly 1.0; p = (1 + #{ROI*_b >= roi_hat}) / (B + 1)."""
    s = day_stake; po = day_payout
    roi_hat = float(po.sum() / s.sum())
    po_c = po / roi_hat
    n = len(s); rng = np.random.default_rng(seed); cnt_ge = 0
    for i in range(0, b, 1000):
        k = min(1000, b - i)
        cnt = rng.multinomial(n, np.full(n, 1.0 / n), size=k).astype(float)
        roi_b = (cnt @ po_c) / (cnt @ s)
        cnt_ge += int(np.sum(roi_b >= roi_hat))
    return {"roi_hat": roi_hat, "p_one_sided": (1 + cnt_ge) / (b + 1), "b": b}


def day_aggregates(stake: np.ndarray, payout: np.ndarray, sel: np.ndarray, day_idx: np.ndarray):
    d = day_idx[sel]
    ud, inv = np.unique(d, return_inverse=True)
    return np.bincount(inv, weights=stake[sel]), np.bincount(inv, weights=payout[sel])


def holm(pvals: dict[str, float], alpha: float = 0.025) -> dict[str, bool]:
    items = sorted(pvals.items(), key=lambda t: t[1]); m = len(items); out = {}
    rejected = True
    for i, (k, pv) in enumerate(items):
        thr = alpha / (m - i)
        rejected = rejected and (pv <= thr)
        out[k] = bool(rejected)
    return out


def score_win(p: dsl.Pattern, arr: dict, kind: str) -> dict:
    m = dsl.base_mask(p, arr)
    m, _ = dsl.select(p, arr, m, need_rank=False)
    stake = stakes_for(p, arr, m)
    payout = np.where(m, arr["_won"] * arr["odds"] * stake, 0.0)
    idx = np.flatnonzero(m)
    res = {"pattern_id": p.pattern_id, "kind": kind, "bet_type": "win", "n_bets_total": int(len(idx))}
    for w, yrs in WINDOWS[kind].items():
        res[w] = window_stats(stake, payout, idx, arr, yrs)
    res["_stake"] = stake; res["_payout"] = payout; res["_idx"] = idx
    return res


_COVERAGE_CACHE: dict[str, np.ndarray] = {}


def coverage_mask(bt: str, arr: dict, table: dict) -> np.ndarray:
    key = f"{bt}:{len(arr['race_id'])}"
    if key not in _COVERAGE_CACHE:
        _COVERAGE_CACHE[key] = np.isin(arr["race_id"], list(table.keys()))
    return _COVERAGE_CACHE[key]


def score_exotic(p: dsl.Pattern, arr: dict, div: dict) -> dict:
    bt = p.bet_type
    table = div.get(bt, {})
    m = dsl.base_mask(p, arr) & coverage_mask(bt, arr, table)
    m, rank = dsl.select(p, arr, m)
    race_ids = arr["race_id"]; hn = arr["horse_number"].astype(int)
    idx = np.flatnonzero(m)
    # group selected horses per race ordered by rank
    order = np.lexsort((rank[idx], arr["_race_idx"][idx]))
    idx = idx[order]
    r = arr["_race_idx"][idx]
    bounds = np.flatnonzero(np.r_[True, r[1:] != r[:-1], True]) if len(idx) else np.array([], dtype=int)
    bet_race, bet_pay, bet_day, bet_year = [], [], [], []
    for a, b in zip(bounds[:-1], bounds[1:]):
        rows = idx[a:b]
        rid = race_ids[rows[0]]
        horses = [int(x) for x in hn[rows]]
        combos = dsl.combos_for_race(horses, bt, p.combo_type or "box")
        if not combos:
            continue
        won = table[rid]
        for c in combos:
            bet_race.append(arr["_race_idx"][rows[0]]); bet_day.append(arr["_day_idx"][rows[0]])
            bet_year.append(arr["_year"][rows[0]])
            bet_pay.append(100.0 * won.get(c, 0.0))
    n = len(bet_pay)
    res = {"pattern_id": p.pattern_id, "kind": "exotic", "bet_type": bt, "n_bets_total": n}
    if n == 0:
        for w in WINDOWS["exotic"]:
            res[w] = {"n_bets": 0}
        res["_bets"] = None
        return res
    pay = np.asarray(bet_pay); yrs = np.asarray(bet_year); days = np.asarray(bet_day); races = np.asarray(bet_race)
    for w, (y0, y1) in WINDOWS["exotic"].items():
        sel = (yrs >= y0) & (yrs <= y1)
        if not sel.any():
            res[w] = {"n_bets": 0}; continue
        po = pay[sel]; ssum = 100.0 * sel.sum(); psum = float(po.sum()); mx = float(po.max())
        res[w] = {"n_bets": int(sel.sum()), "n_races": int(len(np.unique(races[sel]))),
                  "n_days": int(len(np.unique(days[sel]))), "n_hits": int((po > 0).sum()),
                  "stake": ssum, "payout": psum, "roi": psum / ssum, "max_hit_share": mx / psum if psum > 0 else None,
                  "loho_roi": (psum - mx) / ssum, "yearly": {int(y0): round(psum / ssum, 4)},
                  "years_ge1": int(psum / ssum >= 1.0), "years_n": 1, "_sel": np.flatnonzero(sel)}
    res["_bets"] = {"pay": pay, "days": days, "years": yrs}
    return res


def exotic_bootstrap(bets: dict, sel: np.ndarray, *, b: int = 10000, seed: int = 20260923) -> dict:
    d = bets["days"][sel]; pay = bets["pay"][sel]
    ud, inv = np.unique(d, return_inverse=True)
    s = np.bincount(inv, weights=np.full(len(sel), 100.0)); po = np.bincount(inv, weights=pay)
    n = len(ud); rng = np.random.default_rng(seed); out = np.empty(b)
    for i in range(0, b, 1000):
        k = min(1000, b - i)
        cnt = rng.multinomial(n, np.full(n, 1.0 / n), size=k).astype(float)
        out[i:i + k] = (cnt @ po) / (cnt @ s)
    return {"ci_lo": float(np.percentile(out, 2.5)), "ci_hi": float(np.percentile(out, 97.5)),
            "sd": float(out.std()), "p_le_1": float(np.mean(out <= 1.0)), "point": float(po.sum() / s.sum()),
            "n_days": int(n)}


def strip(res: dict) -> dict:
    out = {}
    for k, v in res.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            out[k] = {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------------------------
# null simulation (winner ~ market q)
# ---------------------------------------------------------------------------------------------


def null_simulation(pats: list[dsl.Pattern], results: dict, arr: dict, *, reps: int, seed: int,
                    max_nnz: int = 250_000_000) -> dict:
    """Draw one winner per race from market q; count patterns that would pass D∧Q by point-estimate/degrade.

    Uses the observed masks/stakes (which never depend on outcomes). max_hit_share rule is omitted
    (conservative: more null survivors)."""
    from scipy import sparse

    cand = [p for p in pats if p.bet_type == "win" and results[p.pattern_id]["D"].get("n_bets", 0) > 0
            and results[p.pattern_id]["Q"].get("n_bets", 0) > 0]
    nnz = sum(len(results[p.pattern_id]["D"]["_sel"]) + len(results[p.pattern_id]["Q"]["_sel"]) for p in cand)
    rng = np.random.default_rng(seed)
    if nnz > max_nnz:
        keep = rng.choice(len(cand), size=max(1, int(len(cand) * max_nnz / nnz)), replace=False)
        cand = [cand[i] for i in sorted(keep)]
    if not cand:
        return {"reps": reps, "n_patterns": 0}
    rows_D, cols_D, vals_D, rows_Q, cols_Q, vals_Q = [], [], [], [], [], []
    for i, p in enumerate(cand):
        r = results[p.pattern_id]
        for W, (ro, co, va) in (("D", (rows_D, cols_D, vals_D)), ("Q", (rows_Q, cols_Q, vals_Q))):
            sel = r[W]["_sel"]
            ro.append(np.full(len(sel), i, dtype=np.int32)); co.append(sel.astype(np.int32))
            va.append(r["_stake"][sel])
    n_rows = len(arr["race_id"])
    MD = sparse.csr_matrix((np.concatenate(vals_D), (np.concatenate(rows_D), np.concatenate(cols_D))),
                           shape=(len(cand), n_rows))
    MQ = sparse.csr_matrix((np.concatenate(vals_Q), (np.concatenate(rows_Q), np.concatenate(cols_Q))),
                           shape=(len(cand), n_rows))
    ID = sparse.csr_matrix((np.ones(MD.nnz), MD.indices, MD.indptr), shape=MD.shape)
    IQ = sparse.csr_matrix((np.ones(MQ.nnz), MQ.indices, MQ.indptr), shape=MQ.shape)
    stakeD = np.asarray(MD.sum(axis=1)).ravel(); stakeQ = np.asarray(MQ.sum(axis=1)).ravel()
    daysD = np.asarray([results[p.pattern_id]["D"]["n_days"] for p in cand])
    daysQ = np.asarray([results[p.pattern_id]["Q"]["n_days"] for p in cand])
    # race structure for drawing winners from q
    race_idx = arr["_race_idx"]; q = arr["q"]; odds = arr["odds"]
    nr = int(race_idx.max()) + 1
    starts = np.r_[0, np.flatnonzero(np.diff(race_idx)) + 1, len(race_idx)]
    cq = np.cumsum(q)
    race_cum0 = np.r_[0.0, cq[starts[1:-1] - 1]]
    race_tot = cq[starts[1:] - 1] - race_cum0
    pass_counts = np.zeros(len(cand), dtype=int)
    n_pass_per_rep = np.zeros(reps, dtype=int)
    best_D = np.zeros(reps)
    for rep in range(reps):
        u = rng.random(nr) * race_tot + race_cum0
        # winner row = first row in race whose cumulative q exceeds u
        w_rows = np.searchsorted(cq, u, side="right")
        w_rows = np.minimum(w_rows, starts[1:] - 1)
        w_rows = np.maximum(w_rows, starts[:-1])
        w = np.zeros(len(race_idx)); w[w_rows] = 1.0
        payD = MD @ (w * odds); payQ = MQ @ (w * odds)
        hitD = ID @ w; hitQ = IQ @ w
        roiD = payD / stakeD; roiQ = payQ / stakeQ
        ok = (roiD >= 1.0) & (roiQ >= 1.0) & (hitD >= DEGRADE["min_hits"]) & (hitQ >= DEGRADE["min_hits"]) \
            & (daysD >= DEGRADE["min_days"]) & (daysQ >= DEGRADE["min_days"])
        pass_counts += ok
        n_pass_per_rep[rep] = int(ok.sum())
        best_D[rep] = float(np.max(np.where(hitD >= DEGRADE["min_hits"], roiD, 0.0)))
    return {
        "reps": reps, "n_patterns": len(cand), "nnz": int(MD.nnz + MQ.nnz),
        "mean_pass_DQ": float(n_pass_per_rep.mean()), "p_any_pass_DQ": float(np.mean(n_pass_per_rep > 0)),
        "pass_hist": {int(k): int(v) for k, v in zip(*np.unique(n_pass_per_rep, return_counts=True))},
        "max_roi_D_quantiles": {"p50": float(np.percentile(best_D, 50)), "p95": float(np.percentile(best_D, 95)),
                                "p99": float(np.percentile(best_D, 99)), "max": float(best_D.max())},
        "top_null_passers": sorted(
            [(cand[i].pattern_id, int(pass_counts[i])) for i in np.argsort(-pass_counts)[:15] if pass_counts[i] > 0],
            key=lambda t: -t[1]),
        "note": "DIAGNOSTIC ONLY (codex 2026-09-23): winner ~ market q is a world with E[ROI]≈1/overround<1, "
                "not the boundary null ROI=1; it shows how the D∧Q screen behaves when the market is exactly right. "
                "Observed masks/stakes are reused; max_hit_share rule omitted (conservative).",
    }


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patterns", default=str(ART / "patterns"))
    ap.add_argument("--run", default="a1")
    ap.add_argument("--null-reps", type=int, default=200)
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--near-miss", type=int, default=40)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    out_dir = ART / "results" / args.run
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log("loading rows …")
    arr = load_rows()
    div = load_dividends()
    log(f"rows={len(arr['race_id']):,} races={int(arr['_race_idx'].max()) + 1:,} days={len(arr['_days']):,}")
    files = sorted(pathlib.Path(args.patterns).glob("*.json"))
    pats, rejects = dsl.load_pattern_files(files)
    ctl, rej2 = [], []
    for d in CONTROLS:
        ctl.append(dsl.parse_pattern(d, source="controls"))
    if args.limit:
        pats = pats[:args.limit]
    log(f"patterns={len(pats)} rejected={len(rejects)} controls={len(ctl)} files={len(files)}")
    (out_dir / "rejects.json").write_text(json.dumps(rejects, ensure_ascii=False, indent=1))
    results: dict[str, dict] = {}
    everything = pats + ctl
    for i, p in enumerate(everything):
        if p.bet_type == "win":
            kind = "model" if p.uses_model else "market"
            results[p.pattern_id] = score_win(p, arr, kind)
        else:
            results[p.pattern_id] = score_exotic(p, arr, div)
        if (i + 1) % 200 == 0:
            log(f"scored {i + 1}/{len(everything)} ({time.time() - t0:.0f}s)")
    # ---- survivors / near misses
    rows = []
    survivors, weak = [], []
    for p in everything:
        r = results[p.pattern_id]
        rec = strip(r)
        rec.update({"family": p.family, "label_ja": p.label_ja, "source": p.source, "stake": p.stake,
                    "select": p.select_rule, "uses_model": p.uses_model, "notes": p.notes})
        if r["kind"] == "exotic":
            okD, whyD = passes(r["D"]); okC, whyC = passes(r["C"])
            rec["stage"] = "C" if okD else "D"; rec["stage_reason"] = whyD if not okD else whyC
            rec["screen_pass"] = okD
            if okD and r["C"].get("n_bets", 0) > 0 and r["_bets"] is not None:
                rec["C_boot"] = exotic_bootstrap(r["_bets"], r["C"]["_sel"], b=args.boot)
                rec["confirm_point_ge1"] = bool(r["C"]["roi"] >= 1.0)
                rec["confirm_ci_lo_gt1"] = bool(rec["C_boot"]["ci_lo"] > 1.0)
                (survivors if rec["confirm_ci_lo_gt1"] else weak if rec["confirm_point_ge1"] else []).append(rec)
        else:
            okD, whyD = passes(r["D"])
            okQ, whyQ = passes(r["Q"]) if okD else (False, "not_reached")
            rec["screen_pass"] = okD and okQ
            rec["stage"] = "C" if (okD and okQ) else ("Q" if okD else "D")
            rec["stage_reason"] = whyD if not okD else (whyQ if not okQ else "reached_C")
            if okD and okQ and r["C"].get("n_bets", 0) > 0:
                rec["C_boot"] = day_bootstrap(r["_stake"], r["_payout"], r["C"]["_sel"], arr, b=args.boot)
                rec["confirm_point_ge1"] = bool(r["C"]["roi"] is not None and r["C"]["roi"] >= 1.0)
                rec["confirm_ci_lo_gt1"] = bool(rec["C_boot"]["ci_lo"] > 1.0)
                (survivors if rec["confirm_ci_lo_gt1"] else weak if rec["confirm_point_ge1"] else []).append(rec)
        rows.append(rec)
    # near misses: best min(D,Q) roi among patterns with support in both windows (win) or D (exotic)
    def score_key(rec):
        if rec["kind"] == "exotic":
            d = rec["D"]
            return d["roi"] if d.get("n_bets", 0) >= 50 and d.get("n_hits", 0) >= 5 and d["roi"] is not None else -1
        d, q = rec["D"], rec["Q"]
        if d.get("n_hits", 0) < DEGRADE["min_hits"] or q.get("n_hits", 0) < DEGRADE["min_hits"]:
            return -1
        return min(d["roi"], q["roi"])
    near = sorted([r for r in rows if r["family"] != "control"], key=score_key, reverse=True)[:args.near_miss]
    for rec in near:
        r = results[rec["pattern_id"]]
        if "C_boot" not in rec and r.get("C", {}).get("n_bets", 0) > 0:
            if r["kind"] == "exotic":
                rec["C_boot"] = exotic_bootstrap(r["_bets"], r["C"]["_sel"], b=args.boot)
            else:
                rec["C_boot"] = day_bootstrap(r["_stake"], r["_payout"], r["C"]["_sel"], arr, b=args.boot)
    # ---- confirmatory test on the frozen set that reached C: null-centred one-sided p + Holm (FWER 2.5%)
    frozen = [rec for rec in rows if rec.get("screen_pass") and rec["family"] != "control" and "C_boot" in rec]
    pvals = {}
    for rec in frozen:
        r = results[rec["pattern_id"]]
        if r["kind"] == "exotic":
            sel = r["C"]["_sel"]; ds, dp = day_aggregates(np.full(len(r["_bets"]["pay"]), 100.0), r["_bets"]["pay"], sel, r["_bets"]["days"])
        else:
            ds, dp = day_aggregates(r["_stake"], r["_payout"], r["C"]["_sel"], arr["_day_idx"])
        rec["C_test"] = centered_pvalue(ds, dp, b=args.boot)
        pvals[rec["pattern_id"]] = rec["C_test"]["p_one_sided"]
    hm = holm(pvals) if pvals else {}
    for rec in frozen:
        rec["confirm_holm"] = hm.get(rec["pattern_id"], False)
    n_holm = int(sum(hm.values()))
    controls = []
    for p in ctl:
        r = results[p.pattern_id]; rec = strip(r); rec["label_ja"] = p.label_ja
        for w in ("D", "Q", "C"):
            if w in r and r[w].get("n_bets", 0) > 0:
                if r["kind"] == "exotic":
                    rec[f"{w}_boot"] = exotic_bootstrap(r["_bets"], r[w]["_sel"], b=2000)
                else:
                    rec[f"{w}_boot"] = day_bootstrap(r["_stake"], r["_payout"], r[w]["_sel"], arr, b=2000)
        controls.append(rec)
    log(f"survivors(CI>1)={len(survivors)} weak(point>=1)={len(weak)}; null simulation …")
    null = null_simulation(pats, results, arr, reps=args.null_reps, seed=7) if args.null_reps > 0 else {}
    # ---- write
    stage_counts = pd.Series([r["stage"] for r in rows if r["family"] != "control"]).value_counts().to_dict()
    reason_counts = pd.Series([r["stage_reason"] for r in rows if r["family"] != "control"]).value_counts().to_dict()
    summary = {
        "run": args.run, "n_patterns": len(pats), "n_rejected": len(rejects), "n_controls": len(ctl),
        "stage_counts": stage_counts, "stage_reasons": reason_counts,
        "n_survivors_ci": len(survivors), "n_weak_point": len(weak),
        "n_reached_C": len(frozen), "n_confirmed_holm": n_holm,
        "windows": WINDOWS, "degrade": DEGRADE, "elapsed_s": round(time.time() - t0, 1),
        "by_kind": pd.Series([r["kind"] for r in rows if r["family"] != "control"]).value_counts().to_dict(),
        "by_source": pd.Series([r["source"] for r in rows if r["family"] != "control"]).value_counts().to_dict(),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=_default))
    (out_dir / "survivors.json").write_text(json.dumps({"ci_gt1": survivors, "point_ge1": weak}, ensure_ascii=False,
                                                       indent=1, default=_default))
    (out_dir / "near_misses.json").write_text(json.dumps(near, ensure_ascii=False, indent=1, default=_default))
    (out_dir / "controls.json").write_text(json.dumps(controls, ensure_ascii=False, indent=1, default=_default))
    (out_dir / "null.json").write_text(json.dumps(null, ensure_ascii=False, indent=1, default=_default))
    flat = []
    for rec in rows:
        f = {k: v for k, v in rec.items() if k not in ("D", "Q", "C", "C_boot")}
        for w in ("D", "Q", "C"):
            st = rec.get(w, {})
            for kk in ("n_bets", "n_races", "n_days", "n_hits", "roi", "max_hit_share", "loho_roi", "years_ge1", "years_n"):
                f[f"{w}_{kk}"] = st.get(kk)
        if "C_boot" in rec:
            f["C_ci_lo"] = rec["C_boot"]["ci_lo"]; f["C_ci_hi"] = rec["C_boot"]["ci_hi"]
        flat.append(f)
    pd.DataFrame(flat).to_parquet(out_dir / "scores.parquet", index=False)
    pd.DataFrame(flat).to_csv(out_dir / "scores.csv", index=False)
    log(f"done: {json.dumps(summary, ensure_ascii=False, default=_default)[:600]}")
    return 0


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, np.bool_):
        return bool(o)
    return str(o)


if __name__ == "__main__":
    sys.exit(main())
