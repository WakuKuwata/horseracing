"""R08_frontier — loss-minimisation frontier under one settlement contract (pre-registered).

Prereg: artifacts/roi_explore/missed_20261004/R08_frontier/prereg.json (sha256 in prereg.sha256).
Read-only: SELECT only. Outputs under artifacts/roi_explore/missed_20261004/R08_frontier/.

Run: cd training && uv run python ../scripts/roi_explore/missed_20261004/r08_frontier.py
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "eval" / "src"))
from horseracing_eval import attention_rules as ar  # noqa: E402
from horseracing_eval.bootstrap import (  # noqa: E402
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
)

OUT = ROOT / "artifacts" / "roi_explore" / "missed_20261004" / "R08_frontier"
PREREG = OUT / "prereg.json"
DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
ROWS = ROOT / "artifacts" / "market_ev" / "rows_2007.parquet"
RES = ROOT / "artifacts" / "roi_explore" / "results"
ENS_RUNS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
SINGLE_RUN = "armC_binary_drop-sameday+weightlive_serving_v2_2007"
R05 = ROOT / "artifacts" / "roi_explore" / "missed_20261004" / "R05_settlement" / "affected_races.csv"
R01 = ROOT / "artifacts" / "roi_explore" / "missed_20261004" / "R01_premium_days" / "boost_day_table.csv"

W_LO, W_HI = "2025-01-01", "2026-09-22"
SEC_LO, SEC_HI = "2010-01-01", "2026-06-26"
B = 20000
SEED = 20261004
B_PERM = 10000
NORMAL_RATE = {"win": 0.80, "place": 0.80, "quinella": 0.775, "trio": 0.75}
ODDS_BANDS = [1.0, 2.0, 3.0, 5.0, 10.0, 21.0, np.inf]
QBANDS = {"B1": (0.0, 5.0), "B2": (5.0, 15.0), "B3": (15.0, np.inf)}


def log(*a):
    print(*a, flush=True)


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def r6(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), 6)


# ------------------------------------------------------------------------------------------------
# data
# ------------------------------------------------------------------------------------------------
def load_rows():
    cols = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "finish_order",
            "finished", "days_since_last", "race_ok", "dead_heat", "field_size", "odds_rank", "q", "fav_q"]
    d = pd.read_parquet(ROWS, columns=cols)
    d = d[(d.race_date >= SEC_LO) & (d.race_date <= W_HI)].copy()
    for s, tag in ENS_RUNS.items():
        v = pd.read_parquet(RES / tag / "predictions.parquet")[["race_id", "horse_id", "pred"]]
        d = d.merge(v.rename(columns={"pred": f"pred{s}"}), on=["race_id", "horse_id"], how="left",
                    validate="one_to_one")
    v = pd.read_parquet(RES / SINGLE_RUN / "predictions.parquet")[["race_id", "horse_id", "pred"]]
    d = d.merge(v.rename(columns={"pred": "pred_single"}), on=["race_id", "horse_id"], how="left",
                validate="one_to_one")
    d["race_date"] = d.race_date.astype(str).str.slice(0, 10)
    rdh = d.groupby("race_id").dead_heat.transform("any")
    d = d[d.race_ok & ~rdh].reset_index(drop=True)
    pc = [f"pred{s}" for s in ENS_RUNS] + ["pred_single"]
    miss = int(d[pc].isna().any(axis=1).sum())
    if miss:
        raise SystemExit(f"FAIL: {miss} rows lack predictions")
    evs = np.stack([1.0 + d[f"pred{s}"].to_numpy(float) / 100.0 for s in ENS_RUNS], axis=1)
    d["ens_ev"] = evs.mean(axis=1)
    d["max_ev"] = evs.max(axis=1)
    d["veto"] = d["max_ev"] < 0.8
    odds = d.odds.to_numpy(float)
    d["p_low"] = np.quantile(evs / odds[:, None], 0.2, axis=1)
    d["single_ev"] = 1.0 + d.pred_single.to_numpy(float) / 100.0
    d["horse_number"] = d.horse_number.astype(int)
    d["S"] = d.groupby("race_id").odds.transform(lambda s: (1.0 / s).sum())
    for rid in ("S1", "S3"):
        d[rid] = ar.match_mask(ar.definition(rid), ens_ev=d.ens_ev.to_numpy(float),
                               single_ev=d.single_ev.to_numpy(float), odds=odds,
                               days_since_last=d.days_since_last.to_numpy(float))
    return d


def load_db(race_ids_window, rids_2024):
    eng = create_engine(DB)
    with eng.connect() as c:
        races = pd.read_sql(text("select race_id, post_time from races where race_id = any(:r)"), c,
                            params={"r": list(race_ids_window)})
        ex = pd.read_sql(text("select race_id, bet_type, selection, odds from exotic_odds where race_id = any(:r)"),
                         c, params={"r": list(race_ids_window)})
        qq = pd.read_sql(text("select race_id, quotes, official_at from exotic_quotes where bet_type='quinella' "
                              "and race_id = any(:r)"), c,
                         params={"r": list(race_ids_window) + list(rids_2024)})
    return races, ex, qq


# ------------------------------------------------------------------------------------------------
# universe
# ------------------------------------------------------------------------------------------------
def build_universe(rows_w, ex, affected_map):
    ex_g = {k: g for k, g in ex.groupby("race_id")}
    info = {}
    excl = {}
    for rid, g in rows_w.groupby("race_id", sort=False):
        def bad(k):
            excl[k] = excl.get(k, 0) + 1
        e = ex_g.get(rid)
        if e is None or set(e.bet_type) != {"place", "quinella", "wide", "exacta", "trio", "trifecta"}:
            bad("no_full_exotic")
            continue
        n = len(g)
        if n < 5:
            bad("n_lt_5")
            continue
        fo = g[g.finished & g.finish_order.isin([1, 2, 3])]
        cnt = fo.finish_order.value_counts()
        if not all(cnt.get(k, 0) == 1 for k in (1, 2, 3)):
            bad("top3_not_unique")
            continue
        f = {int(o): int(h) for o, h in zip(fo.finish_order, fo.horse_number)}
        h1, h2, h3 = f[1], f[2], f[3]
        rows = {b: e[e.bet_type == b] for b in ["place", "quinella", "wide", "exacta", "trio", "trifecta"]}
        kplace = 3 if n >= 8 else 2
        if (len(rows["quinella"]) != 1 or len(rows["exacta"]) != 1 or len(rows["trio"]) != 1
                or len(rows["trifecta"]) != 1 or len(rows["place"]) != kplace or len(rows["wide"]) != 3):
            bad("payout_multiplicity")
            continue

        def sel(b):
            return [list(map(int, s)) for s in rows[b].selection]

        def od(b):
            return [float(x) for x in rows[b].odds]
        ok = (sorted(sel("quinella")[0]) == sorted([h1, h2]) and sel("exacta")[0] == [h1, h2]
              and sorted(sel("trio")[0]) == sorted([h1, h2, h3]) and sel("trifecta")[0] == [h1, h2, h3]
              and sorted(s[0] for s in sel("place")) == sorted([h1, h2, h3][:kplace])
              and sorted(tuple(sorted(s)) for s in sel("wide")) == sorted(
                  [tuple(sorted(p)) for p in [(h1, h2), (h1, h3), (h2, h3)]]))
        if not ok:
            bad("integrity_mismatch")
            continue
        info[rid] = {"h": (h1, h2, h3), "n": n, "place": {s[0]: o for s, o in zip(sel("place"), od("place"))},
                     "quinella": od("quinella")[0], "trio": od("trio")[0],
                     "affected": int(affected_map.get(rid, (0, False, np.nan))[0]),
                     "correctable": bool(affected_map.get(rid, (0, False, np.nan))[1]),
                     "official_win": affected_map.get(rid, (0, False, np.nan))[2]}
    return info, excl


# ------------------------------------------------------------------------------------------------
# tickets
# ------------------------------------------------------------------------------------------------
def make_tickets(rows_u, info, boost_rates, win_settle):
    """win_settle: 'stored' or 'official_if_affected'."""
    T = []
    g_by = {k: g for k, g in rows_u.groupby("race_id", sort=False)}
    for rid, g in g_by.items():
        inf = info[rid]
        date = g.race_date.iloc[0]
        rates = boost_rates.get(date, {})
        h1, h2, h3 = inf["h"]
        base = dict(race_id=rid, race_date=date, year=int(date[:4]))
        fav = g[g.odds_rank == 1].iloc[0]
        sec = g[g.odds_rank == 2].iloc[0]
        thr = g[g.odds_rank == 3].iloc[0]
        S = float(g.S.iloc[0])

        def win_pay(hr):
            if not hr.won:
                return 0.0
            if win_settle == "official_if_affected" and inf["affected"] == 1:
                return float(inf["official_win"])
            return float(hr.odds) * 100.0

        def win_ticket(pol, hr):
            T.append(dict(base, policy=pol, bet="win", sel_key=int(hr.horse_number), stake=100.0,
                          payout=win_pay(hr), odds=float(hr.odds), won=bool(hr.won), veto=bool(hr.veto),
                          p_low=float(hr.p_low), null_rate=1.0 / S))

        def place_ticket(pol, hr):
            hn = int(hr.horse_number)
            pay = inf["place"].get(hn, 0.0) * 100.0
            T.append(dict(base, policy=pol, bet="place", sel_key=hn, stake=100.0, payout=pay,
                          odds=float(hr.odds), won=pay > 0, veto=bool(hr.veto), p_low=np.nan,
                          null_rate=rates.get("place", NORMAL_RATE["place"])))

        win_ticket("P01_WIN_FAV", fav)
        for hr in g[g.odds < 21.0].sort_values("horse_number").itertuples():
            win_ticket("P02_WIN_CAP21", hr)
        place_ticket("P03_PLACE_FAV", fav)
        if float(fav.fav_q) >= 0.5:
            place_ticket("P04_PLACE_2ND_IF_FAVQ50", sec)
        q12 = sorted([int(fav.horse_number), int(sec.horse_number)])
        qhit = q12 == sorted([h1, h2])
        T.append(dict(base, policy="P05_QUIN_FAV12", bet="quinella", sel_key=q12[0] * 100 + q12[1], stake=100.0,
                      payout=inf["quinella"] * 100.0 if qhit else 0.0, odds=np.nan, won=qhit, veto=False,
                      p_low=np.nan, null_rate=rates.get("quinella", NORMAL_RATE["quinella"])))
        t3 = sorted([int(fav.horse_number), int(sec.horse_number), int(thr.horse_number)])
        thit = t3 == sorted([h1, h2, h3])
        trio_t = dict(base, policy="P06_TRIO_FAV123", bet="trio", sel_key=t3[0] * 10000 + t3[1] * 100 + t3[2],
                      stake=100.0, payout=inf["trio"] * 100.0 if thit else 0.0, odds=np.nan, won=thit,
                      veto=False, p_low=np.nan, null_rate=rates.get("trio", NORMAL_RATE["trio"]))
        T.append(trio_t)
        for hr in g[g.S3].sort_values("horse_number").itertuples():
            win_ticket("P07_S3_WIN", hr)
        for hr in g[g.S1].sort_values("horse_number").itertuples():
            win_ticket("P08_S1_WIN", hr)
        if rates.get("trio", 0.75) > 0.75:
            T.append(dict(trio_t, policy="P09_TRIO_FAV123_BOOST"))
            T.append(dict(base, policy="X_QUIN_FAV12_BOOST", bet="quinella", sel_key=q12[0] * 100 + q12[1],
                          stake=100.0, payout=inf["quinella"] * 100.0 if qhit else 0.0, odds=np.nan, won=qhit,
                          veto=False, p_low=np.nan, null_rate=rates.get("quinella", NORMAL_RATE["quinella"])))
        if not bool(fav.veto):
            win_ticket("P10a_WIN_FAV_VETO", fav)
            place_ticket("P10c_PLACE_FAV_VETO", fav)
        for hr in g[(g.odds < 21.0) & ~g.veto].sort_values("horse_number").itertuples():
            win_ticket("P10b_WIN_CAP21_VETO", hr)
    return pd.DataFrame(T)


def parse_quotes(qq, rows_any):
    """-> dict race_id -> list of (i, j, odds) for combos with both horses started and non-null odds."""
    started = rows_any.groupby("race_id").horse_number.apply(lambda s: set(int(x) for x in s)).to_dict()
    out = {}
    for rid, quotes, offa in zip(qq.race_id, qq.quotes, qq.official_at):
        st = started.get(rid)
        if st is None:
            continue
        lst = []
        for k, v in quotes.items():
            i, j = (int(x) for x in k.split("-"))
            if i in st and j in st and v is not None and v[0] is not None:
                lst.append((min(i, j), max(i, j), float(v[0])))
        out[rid] = (lst, offa)
    return out


def quin_band_tickets(rids, qmap, top2, payout_fn, extra):
    """tickets of the 3 bands + parent ('ALL') on given races."""
    T = []
    for rid in rids:
        lst, _ = qmap[rid]
        h1, h2 = top2[rid]
        hit = (min(h1, h2), max(h1, h2))
        for (i, j, o) in lst:
            ish = (i, j) == hit
            pay = payout_fn(rid, o) if ish else 0.0
            for bname, (lo, hi) in QBANDS.items():
                if lo <= o < hi:
                    T.append(dict(extra[rid], race_id=rid, band=bname, sel_key=i * 100 + j, stake=100.0,
                                  payout=pay, won=ish, quote=o))
            T.append(dict(extra[rid], race_id=rid, band="ALL", sel_key=i * 100 + j, stake=100.0, payout=pay,
                          won=ish, quote=o))
    return pd.DataFrame(T)


# ------------------------------------------------------------------------------------------------
# metrics
# ------------------------------------------------------------------------------------------------
def day_matrix(tk, policies, days, col="policy"):
    idx = {d: i for i, d in enumerate(days)}
    num = np.zeros((len(policies), len(days)))
    den = np.zeros((len(policies), len(days)))
    for k, p in enumerate(policies):
        s = tk[tk[col] == p]
        if len(s) == 0:
            continue
        agg = s.groupby("race_date")[["payout", "stake"]].sum()
        for d, (pay, st) in zip(agg.index, agg.to_numpy()):
            num[k, idx[d]] = pay
            den[k, idx[d]] = st
    return num, den


def streaks_dd(s, post_map):
    if len(s) == 0:
        return {}
    s = s.assign(pt=s.race_id.map(post_map)).sort_values(["race_date", "pt", "race_id", "sel_key"],
                                                        na_position="last")
    pnl = (s.payout - s.stake).to_numpy()
    cum = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    dd = float((peak - cum).max())
    lose = (s.payout.to_numpy() <= 0)
    best = cur = 0
    for x in lose:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    dp = s.groupby("race_date").apply(lambda x: (x.payout - x.stake).sum(), include_groups=False)
    dbest = dcur = 0
    for v in dp.to_numpy():
        dcur = dcur + 1 if v < 0 else 0
        dbest = max(dbest, dcur)
    return {"max_drawdown_yen": r6(dd), "longest_losing_ticket_streak": int(best),
            "longest_losing_day_streak": int(dbest)}


def log_growth(s):
    if len(s) == 0:
        return None
    O = s.odds.to_numpy(float)
    f = 0.25 * np.maximum(0.0, (s.p_low.to_numpy(float) * O - 1.0) / (O - 1.0))
    s = s.assign(f=f)
    feq = float(f.mean())
    out = {"n_tickets": int(len(s)), "n_kelly_positive": int((f > 0).sum()), "f_eq": r6(feq)}
    for name, col in (("kelly_q", "f"), ("equal", None)):
        tot = 0.0
        nr = 0
        for _, g in s.groupby("race_id", sort=False):
            ff = g.f.to_numpy() if col else np.full(len(g), feq)
            sf = ff.sum()
            if sf > 0.5:
                ff = ff * 0.5 / sf
                sf = 0.5
            fac = 1.0 - sf + float((ff * g.odds.to_numpy() * g.won.to_numpy()).sum())
            tot += math.log(fac)
            nr += 1
        out[name] = {"terminal_log_wealth": r6(tot), "mean_log_per_race": r6(tot / nr)}
    return out


def holm(ps):
    ps = np.asarray(ps, float)
    order = np.argsort(ps)
    m = len(ps)
    adj = np.empty(m)
    run = 0.0
    for r, i in enumerate(order):
        run = max(run, (m - r) * ps[i])
        adj[i] = min(1.0, run)
    return adj


def perm_test(t, rng, b):
    """t: tickets with payout, stake(=100), veto, year, odds. T = ROI(kept) - ROI(parent)."""
    pay = t.payout.to_numpy(float)
    st = t.stake.to_numpy(float)
    veto = t.veto.to_numpy(bool)
    band = np.digitize(t.odds.to_numpy(float), ODDS_BANDS[1:-1])
    strata = t.year.to_numpy() * 10 + band
    P, Sd = pay.sum(), st.sum()
    roi_parent = P / Sd
    kv = Sd - st[veto].sum()
    T_obs = (P - pay[veto].sum()) / kv - roi_parent
    vsum = np.zeros(b)
    for s in np.unique(strata):
        m = strata == s
        k = int(veto[m].sum())
        if k == 0:
            continue
        N = int(m.sum())
        wp = pay[m][pay[m] > 0]
        W = len(wp)
        if W == 0:
            continue
        nwin = rng.hypergeometric(W, N - W, k, size=b)
        for c0 in range(0, b, 1000):
            c1 = min(b, c0 + 1000)
            keys = rng.random((c1 - c0, W))
            order = np.argsort(keys, axis=1)
            sp = wp[order]
            cs = np.cumsum(sp, axis=1)
            nw = nwin[c0:c1]
            vsum[c0:c1] += np.where(nw > 0, cs[np.arange(c1 - c0), np.maximum(nw - 1, 0)], 0.0)
    T_perm = (P - vsum) / kv - roi_parent
    p = (1 + int((T_perm >= T_obs).sum())) / (b + 1)
    return {"T_obs": r6(T_obs), "p_one_sided": r6(p), "T_perm_mean": r6(T_perm.mean()),
            "T_perm_sd": r6(T_perm.std()), "n_parent": int(len(t)), "n_veto": int(veto.sum()),
            "veto_rate": r6(veto.mean()), "hits_veto": int((pay[veto] > 0).sum()),
            "roi_veto": r6(pay[veto].sum() / st[veto].sum()) if veto.any() else None,
            "roi_parent": r6(roi_parent), "roi_kept": r6((P - pay[veto].sum()) / kv)}


def summarize(tk, policies, days, boot, post_map, col="policy"):
    rows = []
    for k, p in enumerate(policies):
        s = tk[tk[col] == p]
        rec = {"policy": p, "n": int(len(s)), "hits": int((s.payout > 0).sum()),
               "n_races": int(s.race_id.nunique()), "stake": r6(s.stake.sum()), "payout": r6(s.payout.sum())}
        if len(s):
            rec["roi"] = r6(boot.point[k])
            rec["ci95"] = [r6(boot.ci_low[k]), r6(boot.ci_high[k])]
            rec["p_one_sided_vs_1"] = r6(centered_one_sided_p_from_replicates(boot.replicates[k], boot.point[k]))
            yr = {}
            for y in (2025, 2026):
                sy = s[s.year == y]
                yr[str(y)] = r6(sy.payout.sum() / sy.stake.sum()) if len(sy) else None
            rec["roi_by_year"] = yr
            vals = [v for v in yr.values() if v is not None]
            rec["roi_year_weighted"] = r6(np.mean(vals)) if vals else None
            if "null_rate" in s:
                rec["takeout_null"] = r6(s.null_rate.mean())
                rec["roi_minus_null"] = r6(boot.point[k] - s.null_rate.mean())
            dpnl = (pd.Series(0.0, index=days)
                    .add(s.groupby("race_date").apply(lambda x: (x.payout - x.stake).sum(), include_groups=False),
                         fill_value=0.0))
            dst = s.groupby("race_date").stake.sum()
            rec["daily_pnl_mean"] = r6(dpnl.mean())
            rec["daily_pnl_sd"] = r6(dpnl.std(ddof=1))
            rec["mean_daily_stake"] = r6(dst.reindex(days, fill_value=0.0).mean())
            sd = streaks_dd(s, post_map)
            rec.update(sd)
            rec["max_dd_over_mean_daily_stake"] = r6(sd["max_drawdown_yen"] / rec["mean_daily_stake"])
            rec["max_hit_share"] = r6(s.payout.max() / s.payout.sum()) if s.payout.sum() > 0 else None
        rows.append(rec)
    return rows


# ------------------------------------------------------------------------------------------------
def main():
    pre_sha = sha(PREREG)
    want = (OUT / "prereg.sha256").read_text().split()[0]
    if pre_sha != want:
        raise SystemExit(f"prereg changed: {pre_sha} != {want}")
    log("prereg", pre_sha)
    rows = load_rows()
    log("rows", len(rows))
    aff = pd.read_csv(R05, dtype={"race_id": str})
    affected_map = {r: (a, c, w) for r, a, c, w in zip(aff.race_id, aff.affected, aff.correctable,
                                                       aff.official_win_payout_yen)}
    bt = pd.read_csv(R01)
    boost_rates = {}
    for d, g in bt.groupby("race_date"):
        if d < W_LO or d > W_HI:
            continue
        # whole-day programs only inside the window (prereg); take the row covering all races
        r = g.sort_values("n_races").iloc[-1]
        boost_rates[d] = {"place": r.rate_place, "quinella": r.rate_quinella, "trio": r.rate_trio, "win": r.rate_win}
    log("boost days", boost_rates)

    rows_w = rows[(rows.race_date >= W_LO) & (rows.race_date <= W_HI)].copy()
    r24 = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "horse_number", "race_date", "finish_order",
                                         "finished", "race_ok", "dead_heat"])
    r24["race_date"] = r24.race_date.astype(str).str.slice(0, 10)
    r24 = r24[(r24.race_date >= "2024-01-01") & (r24.race_date <= "2024-12-31")]
    r24 = r24[r24.race_ok & ~r24.groupby("race_id").dead_heat.transform("any")]
    races_db, ex, qq = load_db(set(rows_w.race_id), set(r24.race_id))
    post_map = dict(zip(races_db.race_id, pd.to_datetime(races_db.post_time, utc=True)))
    log("exotic rows", len(ex), "quotes", len(qq))

    info, excl = build_universe(rows_w, ex, affected_map)
    log("universe candidates", len(info), excl)
    U = sorted([r for r, v in info.items() if v["affected"] == 0])
    SA = sorted([r for r, v in info.items() if v["affected"] == 0 or v["correctable"]])
    rows_u = rows_w[rows_w.race_id.isin(U)]
    days = sorted(rows_u.race_date.unique())
    log("U races", len(U), "days", len(days))

    tk = make_tickets(rows_u, info, boost_rates, "stored")

    # ---------- MS09 discovery 2024
    qmap = parse_quotes(qq, pd.concat([rows_w[["race_id", "horse_number"]], r24[["race_id", "horse_number"]]]))
    top2_24 = {}
    for rid, g in r24.groupby("race_id"):
        fo = g[g.finished & g.finish_order.isin([1, 2])]
        c = fo.finish_order.value_counts()
        if c.get(1, 0) == 1 and c.get(2, 0) == 1:
            top2_24[rid] = (int(fo[fo.finish_order == 1].horse_number.iloc[0]),
                            int(fo[fo.finish_order == 2].horse_number.iloc[0]))
    rids24 = sorted(r for r in top2_24 if r in qmap and len(qmap[r][0]) > 0)
    d24 = dict(zip(r24.race_id, r24.race_date))
    tq24 = quin_band_tickets(rids24, qmap, top2_24, lambda rid, o: o * 100.0,
                             {r: {"race_date": d24[r], "year": 2024} for r in rids24})
    disc = {}
    for bname in list(QBANDS) + ["ALL"]:
        s = tq24[tq24.band == bname]
        disc[bname] = {"n": int(len(s)), "hits": int(s.won.sum()), "roi": r6(s.payout.sum() / s.stake.sum())}
    sel_band = max(QBANDS, key=lambda b_: (disc[b_]["roi"], -list(QBANDS).index(b_)))
    log("MS09 discovery 2024", len(rids24), disc, "selected", sel_band)

    # ---------- MS09 replication 2025-26
    top2_u = {r: info[r]["h"][:2] for r in U}
    qfinal = []
    n_nonfinal = 0
    for r in U:
        if r not in qmap or not qmap[r][0]:
            continue
        pt = post_map.get(r)
        offa = pd.Timestamp(qmap[r][1]) if qmap[r][1] is not None else None
        if pt is not None and not pd.isna(pt) and offa is not None and offa < pt:
            n_nonfinal += 1
            continue
        qfinal.append(r)
    dU = {r: info[r] for r in U}
    rdate = dict(zip(rows_u.race_id, rows_u.race_date))
    tqU = quin_band_tickets(qfinal, qmap, top2_u, lambda rid, o: dU[rid]["quinella"] * 100.0,
                            {r: {"race_date": rdate[r], "year": int(rdate[r][:4])} for r in qfinal})
    # quote check: quote of realised combo vs dividend
    qc = tqU[(tqU.band == "ALL") & tqU.won]
    qdiff = (qc.quote * 100.0 - qc.payout).abs()
    quote_check = {"n_races": int(len(qc)), "share_within_10yen": r6((qdiff <= 10.0).mean()),
                   "median_abs_diff_yen": r6(qdiff.median()), "p90_abs_diff_yen": r6(qdiff.quantile(0.9))}
    log("quote check", quote_check, "non-final quotes excluded", n_nonfinal)
    p11 = tqU[tqU.band == sel_band].assign(policy="P11_MS09_QUIN_BAND", bet="quinella", veto=False,
                                            p_low=np.nan, odds=np.nan,
                                            null_rate=[boost_rates.get(d, {}).get("quinella", 0.775)
                                                       for d in tqU[tqU.band == sel_band].race_date])
    tk = pd.concat([tk, p11[tk.columns.intersection(p11.columns)]], ignore_index=True)
    for bname in list(QBANDS) + ["ALL"]:
        x = tqU[tqU.band == bname].assign(policy=f"X_MS09_{bname}", bet="quinella", veto=False, p_low=np.nan,
                                          odds=np.nan, null_rate=0.775)
        tk = pd.concat([tk, x[tk.columns.intersection(x.columns)]], ignore_index=True)

    MAIN = ["P01_WIN_FAV", "P02_WIN_CAP21", "P03_PLACE_FAV", "P04_PLACE_2ND_IF_FAVQ50", "P05_QUIN_FAV12",
            "P06_TRIO_FAV123", "P07_S3_WIN", "P08_S1_WIN", "P09_TRIO_FAV123_BOOST", "P10a_WIN_FAV_VETO",
            "P10b_WIN_CAP21_VETO", "P10c_PLACE_FAV_VETO", "P11_MS09_QUIN_BAND"]
    EXTRA = ["X_QUIN_FAV12_BOOST"] + [f"X_MS09_{b_}" for b_ in list(QBANDS) + ["ALL"]]
    POL = MAIN + EXTRA
    num, den = day_matrix(tk, POL, days)
    boot = race_block_ratio_bootstrap_ci_v1(num, den, days, block="race_day", b=B, seed=SEED)
    table = summarize(tk, POL, days, boot, post_map)

    # paired vs reference P02
    ref = POL.index("P02_WIN_CAP21")
    comp = [p for p in MAIN if p != "P02_WIN_CAP21"]
    pv = []
    paired = {}
    for p in comp:
        k = POL.index(p)
        d_obs = boot.point[k] - boot.point[ref]
        dr = boot.replicates[k] - boot.replicates[ref]
        dr = dr[np.isfinite(dr)]
        pval = (1 + int((np.abs(dr - d_obs) >= abs(d_obs)).sum())) / (len(dr) + 1)
        paired[p] = {"d": r6(d_obs), "ci95": [r6(np.percentile(dr, 2.5)), r6(np.percentile(dr, 97.5))],
                     "p_two_sided": r6(pval), "n_rep": int(len(dr))}
        pv.append(pval)
    adj = holm(pv)
    for p, a in zip(comp, adj):
        paired[p]["p_holm"] = r6(a)
        paired[p]["holm_sig"] = bool(a < 0.05)

    # MS09 replication: selected band vs parent ALL on same races
    ks = POL.index(f"X_MS09_{sel_band}")
    ka = POL.index("X_MS09_ALL")
    d_obs = boot.point[ks] - boot.point[ka]
    dr = boot.replicates[ks] - boot.replicates[ka]
    dr = dr[np.isfinite(dr)]
    pms = (1 + int((np.abs(dr - d_obs) >= abs(d_obs)).sum())) / (len(dr) + 1)
    lo, hi = np.percentile(dr, 2.5), np.percentile(dr, 97.5)
    ms09 = {"discovery_2024": {"n_races": len(rids24), **disc}, "selected_band": sel_band,
            "replication_races": len(qfinal), "nonfinal_quote_races_excluded": n_nonfinal,
            "selected_minus_parent": {"d": r6(d_obs), "ci95": [r6(lo), r6(hi)], "p_two_sided": r6(pms)},
            "replicated": bool(d_obs > 0 and lo > 0), "quote_check": quote_check}

    # MCU15 permutation (main window)
    rng = np.random.default_rng(SEED)
    mcu = {}
    pvm = []
    for vp, par in (("P10a_WIN_FAV_VETO", "P01_WIN_FAV"), ("P10b_WIN_CAP21_VETO", "P02_WIN_CAP21"),
                    ("P10c_PLACE_FAV_VETO", "P03_PLACE_FAV")):
        t = tk[tk.policy == par]
        res = perm_test(t, rng, B_PERM)
        kv, kp = POL.index(vp), POL.index(par)
        dr = boot.replicates[kv] - boot.replicates[kp]
        dr = dr[np.isfinite(dr)]
        res["paired_boot_ci95_T"] = [r6(np.percentile(dr, 2.5)), r6(np.percentile(dr, 97.5))]
        mcu[vp] = res
        pvm.append(res["p_one_sided"])
    for vp, a in zip(mcu, holm(pvm)):
        mcu[vp]["p_holm"] = r6(a)
    log("MCU15 main", json.dumps(mcu, ensure_ascii=False))

    # MCU15 secondary window 2010..2026-06-26 (win only, no exotic requirement)
    sec = rows[(rows.race_date >= SEC_LO) & (rows.race_date <= SEC_HI)].copy()
    sec = sec[~sec.race_id.map(lambda r: affected_map.get(r, (0,))[0] == 1)]
    sec["payout"] = np.where(sec.won, sec.odds * 100.0, 0.0)
    sec["stake"] = 100.0
    mcu2 = {}
    pv2 = []
    sdays = sorted(sec.race_date.unique())
    for vp, m in (("P10a_WIN_FAV_VETO", sec.odds_rank == 1), ("P10b_WIN_CAP21_VETO", sec.odds < 21.0)):
        t = sec[m].copy()
        res = perm_test(t, rng, B_PERM)
        tt = pd.concat([t.assign(policy="parent"), t[~t.veto].assign(policy="kept")])
        n2, d2 = day_matrix(tt, ["parent", "kept"], sdays)
        bb = race_block_ratio_bootstrap_ci_v1(n2, d2, sdays, block="race_day", b=B, seed=SEED)
        dr = bb.replicates[1] - bb.replicates[0]
        res["paired_boot_ci95_T"] = [r6(np.percentile(dr, 2.5)), r6(np.percentile(dr, 97.5))]
        res["parent_ci95"] = [r6(bb.ci_low[0]), r6(bb.ci_high[0])]
        res["kept_ci95"] = [r6(bb.ci_low[1]), r6(bb.ci_high[1])]
        yr = {}
        for y, g in t.groupby("year"):
            kept = g[~g.veto]
            yr[int(y)] = {"parent": r6(g.payout.sum() / g.stake.sum()),
                          "kept": r6(kept.payout.sum() / kept.stake.sum()), "n_veto": int(g.veto.sum())}
        res["by_year"] = yr
        res["year_weighted_T"] = r6(np.mean([v["kept"] - v["parent"] for v in yr.values()]))
        mcu2[vp] = res
        pv2.append(res["p_one_sided"])
    for vp, a in zip(mcu2, holm(pv2)):
        mcu2[vp]["p_holm"] = r6(a)
    log("MCU15 secondary", json.dumps(mcu2, ensure_ascii=False))

    # log growth (win policies)
    lg = {p: log_growth(tk[tk.policy == p]) for p in
          ["P01_WIN_FAV", "P02_WIN_CAP21", "P07_S3_WIN", "P08_S1_WIN", "P10a_WIN_FAV_VETO", "P10b_WIN_CAP21_VETO"]}

    # ---------- sensitivities
    rows_sa = rows_w[rows_w.race_id.isin(SA)]
    tk_sa = make_tickets(rows_sa, info, boost_rates, "official_if_affected")
    days_sa = sorted(rows_sa.race_date.unique())
    POL_SA = [p for p in MAIN if p != "P11_MS09_QUIN_BAND"]
    n_sa, d_sa = day_matrix(tk_sa, POL_SA, days_sa)
    b_sa = race_block_ratio_bootstrap_ci_v1(n_sa, d_sa, days_sa, block="race_day", b=B, seed=SEED)
    sa = [{"policy": p, "n": int((tk_sa.policy == p).sum()), "roi": r6(b_sa.point[k]),
           "ci95": [r6(b_sa.ci_low[k]), r6(b_sa.ci_high[k])]} for k, p in enumerate(POL_SA)]

    # SB: win policies on full rows universe (no exotic requirement), affected excluded
    rdate_all = dict(zip(rows_w.race_id, rows_w.race_date))
    fu = rows_w[~rows_w.race_id.map(lambda r: affected_map.get(r, (0,))[0] == 1)].copy()
    fu["payout"] = np.where(fu.won, fu.odds * 100.0, 0.0)
    fu["stake"] = 100.0
    fu["sel_key"] = fu.horse_number
    sbsel = {"P01_WIN_FAV": fu.odds_rank == 1, "P02_WIN_CAP21": fu.odds < 21.0, "P07_S3_WIN": fu.S3,
             "P08_S1_WIN": fu.S1, "P10a_WIN_FAV_VETO": (fu.odds_rank == 1) & ~fu.veto,
             "P10b_WIN_CAP21_VETO": (fu.odds < 21.0) & ~fu.veto}
    tsb = pd.concat([fu[m].assign(policy=p) for p, m in sbsel.items()], ignore_index=True)
    fdays = sorted(fu.race_date.unique())
    n_sb, d_sb = day_matrix(tsb, list(sbsel), fdays)
    b_sb = race_block_ratio_bootstrap_ci_v1(n_sb, d_sb, fdays, block="race_day", b=B, seed=SEED)
    sub25 = {r for r in info if r in rdate_all and rdate_all[r] < "2026-01-01"}
    sb = []
    for k, p in enumerate(sbsel):
        s = tsb[tsb.policy == p]
        s25 = s[s.year == 2025]
        s25u = s25[s25.race_id.isin(sub25)]
        s25o = s25[~s25.race_id.isin(sub25)]
        sb.append({"policy": p, "n": int(len(s)), "races": int(fu.race_id.nunique()), "roi": r6(b_sb.point[k]),
                   "ci95": [r6(b_sb.ci_low[k]), r6(b_sb.ci_high[k])],
                   "roi_2025_full": r6(s25.payout.sum() / s25.stake.sum()),
                   "roi_2025_in_exotic_subsample": r6(s25u.payout.sum() / s25u.stake.sum()) if len(s25u) else None,
                   "n_2025_in_subsample": int(len(s25u)),
                   "roi_2025_outside_subsample": r6(s25o.payout.sum() / s25o.stake.sum()) if len(s25o) else None,
                   "roi_2026": r6(s[s.year == 2026].payout.sum() / s[s.year == 2026].stake.sum())})

    out = {"test_id": "R08_frontier", "prereg_sha256": pre_sha,
           "inputs": {"rows_sha256": sha(ROWS)},
           "universe": {"window": [W_LO, W_HI], "candidates_with_full_exotics": len(info), "excluded": excl,
                        "U_races": len(U), "U_days": len(days),
                        "U_races_by_year": {y: sum(1 for r in U if rdate[r][:4] == y) for y in ("2025", "2026")},
                        "affected_in_candidates": sum(1 for v in info.values() if v["affected"] == 1),
                        "SA_races": len(SA)},
           "boost_days_in_window": boost_rates,
           "frontier": table, "paired_vs_P02": paired, "ms09": ms09,
           "mcu15_main": mcu, "mcu15_secondary_2010_20260626": mcu2, "log_growth": lg,
           "sens_SA_correctable": sa, "sens_SB_win_full": sb}
    (OUT / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    tk.to_parquet(OUT / "tickets_main.parquet")
    pd.DataFrame(table).to_csv(OUT / "frontier.csv", index=False)
    log("done")
    for r in table:
        log(r["policy"], r["n"], r["hits"], r.get("roi"), r.get("ci95"), r.get("roi_year_weighted"),
            r.get("takeout_null"))
    log(json.dumps(paired, ensure_ascii=False))
    log(json.dumps(ms09, ensure_ascii=False))


if __name__ == "__main__":
    main()
