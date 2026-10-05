"""R01_premium_days — JRA 払戻率上乗せ日(プレミアム/スーパー/ウルトラ)の実効払戻率の検算と健全性確認。

事前登録: artifacts/roi_explore/missed_20261004/R01_premium_days/prereg.json(sha256 は prereg.sha256)
カレンダー: 同ディレクトリ boost_calendar.json(JRA 公式ページから作成・結果は見ていない)

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r01_premium_days.py

読み取りのみ(DB は SELECT だけ)。出力: artifacts/roi_explore/missed_20261004/R01_premium_days/
  results.json / stage1_programs.csv / stage1_days.csv / stage1_quinella_days.csv / stage2_cells.csv
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import sys

import numpy as np
import pandas as pd
import sqlalchemy as sa

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "eval" / "src"))
from horseracing_eval import attention_rules as ar  # noqa: E402
from horseracing_eval.bootstrap import race_block_ratio_bootstrap_ci_v1  # noqa: E402

OUT = REPO / "artifacts/roi_explore/missed_20261004/R01_premium_days"
PREREG = OUT / "prereg.json"
CAL = OUT / "boost_calendar.json"
ROWS = REPO / "artifacts/market_ev/rows_2007.parquet"
RES = REPO / "artifacts/roi_explore/results"
ENS_RUNS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
B, SEED = 20000, 20260905
QSEED, QREPS = 20261004, 200
WIN_CUT = "2026-06-26"
NORMAL = {"win": 0.80, "place": 0.80, "bracket": 0.775, "quinella": 0.775, "wide": 0.775,
          "exacta": 0.75, "trio": 0.75, "trifecta": 0.725}
EXOTIC_TYPES = ["bracket", "quinella", "wide", "exacta", "trio", "trifecta"]
TOL = 0.015


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def daterange(a: str, b: str) -> list[str]:
    d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def r6(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), 6)


# ------------------------------------------------------------------------------------------------
# data
# ------------------------------------------------------------------------------------------------
def load_rows() -> pd.DataFrame:
    cols = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "age", "race_ok",
            "dead_heat", "race_number", "grade", "race_class", "days_since_last", "finish_order"]
    d = pd.read_parquet(ROWS, columns=cols)
    d["race_date"] = d["race_date"].astype(str).str.slice(0, 10)
    return d


def load_db(engine) -> dict:
    with engine.connect() as c:
        names = pd.read_sql(sa.text("select race_id, race_date, race_name, race_number, venue_code from races "
                                    "where race_date >= '2014-12-01'"), c)
        qq = pd.read_sql(sa.text("select race_id, bet_type, quotes::text as quotes, official_at from exotic_quotes "
                                 "where bet_type in ('quinella','trio')"), c)
        div = pd.read_sql(sa.text("select race_id, bet_type, selection::text as selection, odds from exotic_odds "
                                  "where bet_type in ('quinella','place')"), c)
    names["race_date"] = names["race_date"].astype(str)
    return {"names": names, "quotes": qq, "div": div}


# ------------------------------------------------------------------------------------------------
# race table + boost labels (outcome-free)
# ------------------------------------------------------------------------------------------------
def race_table(rows: pd.DataFrame, names: pd.DataFrame) -> pd.DataFrame:
    v = rows[np.isfinite(rows.odds.astype(float)) & (rows.odds > 0)].copy()
    v["inv"] = 1.0 / v.odds.astype(float)
    g = v.groupby("race_id")
    rt = pd.DataFrame({
        "race_date": g.race_date.first(),
        "race_number": g.race_number.first(),
        "grade": g.grade.first(),
        "race_class": g.race_class.first(),
        "n_runners": g.size(),
        "S_w": g.inv.sum(),
        "age_min": g.age.min(), "age_max": g.age.max(),
        "race_ok": g.race_ok.all(),
    })
    rt["is_2yo"] = (rt.age_min == 2) & (rt.age_max == 2)
    rt["is_3yo"] = (rt.age_min == 3) & (rt.age_max == 3)
    rt = rt.join(names.set_index("race_id")[["race_name"]], how="left")
    # races known to the DB but absent from rows (e.g. 2026-09-22 extra) for calendar counting
    extra = names[~names.race_id.isin(rt.index)].set_index("race_id")
    extra = extra.assign(n_runners=0, S_w=np.nan, is_2yo=False, is_3yo=False, grade=None, race_class=None,
                         race_ok=False)[["race_date", "race_number", "grade", "race_class", "n_runners", "S_w",
                                         "race_ok", "is_2yo", "is_3yo", "race_name"]]
    rt = pd.concat([rt, extra])
    rt["year"] = rt.race_date.str.slice(0, 4).astype(int)
    return rt


def select_program(rt: pd.DataFrame, p: dict) -> pd.Index:
    days = set(p.get("dates", []))
    if "range" in p:
        days |= set(daterange(*p["range"]))
    days |= set(p.get("extra_dates", []))
    sub = rt[rt.race_date.isin(days)]
    sel = p["selector"]
    nm = sub.race_name.fillna("")
    if sel.startswith("all races"):
        m = np.ones(len(sub), bool)
    elif sel.startswith("race_number == 12"):
        m = sub.race_number.astype(float) == 12
    elif sel.startswith("2歳限定戦"):
        m = sub.is_2yo.to_numpy(bool)
    elif "金杯" in sel:
        m = nm.str.contains("金杯")
    elif "阪神ジュベナイル" in sel:
        m = nm.str.contains("阪神ジュベナイル") | nm.str.contains("朝日杯")
    elif sel == "桜花賞":
        m = nm.str.contains("桜花賞")
    elif sel == "皐月賞":
        m = nm.str.contains("皐月賞")
    elif sel.startswith("オークス"):
        m = nm.str.contains("優駿牝馬") | nm.str.contains("オークス")
    elif sel.startswith("3歳限定重賞"):
        graded = sub.grade.isin(["G1", "G2", "G3"]) | (sub.race_class == "重賞")
        if "リステッド" in sel:
            graded = graded | (sub.grade == "L")
        m = graded & sub.is_3yo
    else:
        raise ValueError(sel)
    return sub.index[np.asarray(m, bool)]


def label_boosts(rt: pd.DataFrame, cal: dict) -> tuple[pd.DataFrame, list[dict]]:
    rt = rt.copy()
    rt["super"] = False
    rt["ultra"] = False
    rt["programs"] = [[] for _ in range(len(rt))]
    prem_types = {i: set() for i in rt.index}
    counts = []
    for p in cal["premium5_programs"]:
        idx = select_program(rt, p)
        for i in idx:
            prem_types[i] |= set(p["bet_types"])
            rt.at[i, "programs"].append(p["id"])
        counts.append({"program": p["id"], "mechanism": "premium5", "bet_types": "+".join(p["bet_types"]),
                       "jra_races": p.get("jra_races"), "mapped_races": len(idx),
                       "first_date": min(rt.loc[idx, "race_date"]) if len(idx) else None,
                       "last_date": max(rt.loc[idx, "race_date"]) if len(idx) else None})
    for s in cal["super80_days"]:
        idx = rt.index[rt.race_date == s["date"]]
        rt.loc[idx, "super"] = True
        for i in idx:
            rt.at[i, "programs"].append(f"super_{s['date']}")
        counts.append({"program": f"super_{s['date']}", "mechanism": "super80", "bet_types": "all",
                       "jra_races": None, "mapped_races": len(idx), "first_date": s["date"], "last_date": s["date"]})
    for u in cal["ultra85"]:
        if "race_names_by_date" in u:
            idx = []
            for d, nm in u["race_names_by_date"].items():
                hit = rt.index[(rt.race_date == d) & (rt.race_name.fillna("") == nm)]
                idx += list(hit)
            idx = pd.Index(idx)
        else:
            idx = rt.index[rt.race_date.isin(u["dates"])]
        rt.loc[idx, "ultra"] = True
        for i in idx:
            rt.at[i, "programs"].append(u["id"])
        counts.append({"program": u["id"], "mechanism": "ultra85", "bet_types": "all", "jra_races": u.get("jra_races"),
                       "mapped_races": len(idx), "first_date": min(rt.loc[idx, "race_date"]) if len(idx) else None,
                       "last_date": max(rt.loc[idx, "race_date"]) if len(idx) else None})
    # effective official rates per bet type
    sup_or_ult = (rt["super"] | rt["ultra"]).to_numpy(bool)
    ult = rt["ultra"].to_numpy(bool)
    for bt in ["win", "place"] + EXOTIC_TYPES:
        prem = np.array([bt in prem_types[i] for i in rt.index], bool)
        base = np.where(sup_or_ult, 0.80, NORMAL[bt])
        rt[f"rate_{bt}"] = base + np.where(ult | prem, 0.05, 0.0)
    rt["win_boost"] = rt.rate_win > 0.80 + 1e-9
    rt["quin_boost"] = rt.rate_quinella > 0.775 + 1e-9
    rt["wide_boost"] = rt.rate_wide > 0.775 + 1e-9
    rt["any_boost"] = rt[[f"rate_{bt}" for bt in ["win", "place"] + EXOTIC_TYPES]].gt(
        pd.Series({f"rate_{bt}": NORMAL[bt] + 1e-9 for bt in ["win", "place"] + EXOTIC_TYPES})).any(axis=1)
    rt["prog_str"] = rt.programs.apply(lambda x: ";".join(x))
    return rt, counts


# ------------------------------------------------------------------------------------------------
# stage 1
# ------------------------------------------------------------------------------------------------
def implied(r_normal: float, s_cmp: float, s_boost: float) -> float:
    return r_normal * s_cmp / s_boost


def verdict(r_adj: float, r_boost: float, r_normal: float) -> str:
    if not np.isfinite(r_adj):
        return "NOT_VERIFIABLE"
    if abs(r_boost - r_normal) < 1e-9:
        return "UNCHANGED_OK" if abs(r_adj - r_normal) <= TOL else "UNCHANGED_DEVIATES"
    if abs(r_adj - r_boost) <= TOL and abs(r_adj - r_normal) > TOL:
        return "VERIFIED"
    if abs(r_adj - r_normal) <= TOL:
        return "CONTRADICTED"
    return "INDETERMINATE"


def stage1_win(rt: pd.DataFrame, cal: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    w = rt[rt.race_ok & rt.S_w.notna() & (rt.n_runners >= 2)].copy()
    unb = w[~w.win_boost]
    year_med = unb.groupby("year").S_w.median()
    day_unb_med = unb.groupby("race_date").S_w.median()
    day_unb_n = unb.groupby("race_date").size()
    # (1) per win-boost program/class: pooled over its races
    prog_rows = []
    groups = {}
    for p in cal["premium5_programs"]:
        if "win" in p["bet_types"]:
            groups[p["id"]] = w.index[w.prog_str.str.contains(p["id"], regex=False)]
    for u in cal["ultra85"]:
        groups[u["id"]] = w.index[w.prog_str.str.contains(u["id"], regex=False)]
    for gid, idx in groups.items():
        sub = w.loc[idx]
        sub = sub[sub.win_boost]
        if sub.empty:
            continue
        r_boost = float(sub.rate_win.median())
        same = sub.race_date.map(day_unb_med)
        has_same = same.notna()
        s_b = float(sub.S_w.median())
        s_same = float(np.median(unb[unb.race_date.isin(sub.race_date.unique())].S_w)) if has_same.any() else np.nan
        s_year = float(np.median(unb[unb.year.isin(sub.year.unique())].S_w))
        # natural control for 2yo programs: unboosted 2yo races in the same years
        u2 = unb[unb.is_2yo & unb.year.isin(sub.year.unique())]
        s_2yo = float(u2.S_w.median()) if len(u2) >= 20 else np.nan
        ra_same = implied(0.80, s_same, s_b) if np.isfinite(s_same) else np.nan
        ra_year = implied(0.80, s_year, s_b)
        mid = (s_b + s_same) / 2 if np.isfinite(s_same) else np.nan
        prog_rows.append({
            "class": gid, "n_races": len(sub), "n_days": sub.race_date.nunique(),
            "official_win_rate": r_boost, "median_S_boost": s_b,
            "median_S_sameday_unboosted": s_same, "median_S_sameyear_unboosted": s_year,
            "median_S_unboosted_2yo_sameyears": s_2yo,
            "implied_rate_vs_sameday": ra_same, "implied_rate_vs_sameyear": ra_year,
            "implied_rate_vs_unboosted_2yo": implied(0.80, s_2yo, s_b) if np.isfinite(s_2yo) else np.nan,
            "verdict_sameday": verdict(ra_same, r_boost, 0.80),
            "verdict_sameyear": verdict(ra_year, r_boost, 0.80),
            "share_races_below_mid": float(np.mean(sub.S_w < mid)) if np.isfinite(mid) else np.nan,
        })
    prog = pd.DataFrame(prog_rows)
    # (2) per boost day (super / ultra / hopeful days): win median vs same-year unboosted
    day_rows = []
    boost_days = sorted(set(rt.loc[rt.super | rt.ultra, "race_date"]) |
                        set(rt.loc[rt.prog_str.str.contains("hopeful_day"), "race_date"]))
    for d in boost_days:
        sub = w[w.race_date == d]
        if sub.empty:
            day_rows.append({"date": d, "n_races": 0})
            continue
        yr = int(d[:4])
        for kind, ss in [("win_boosted", sub[sub.win_boost]), ("win_unboosted", sub[~sub.win_boost])]:
            if ss.empty:
                continue
            r_off = float(ss.rate_win.median())
            s_b = float(ss.S_w.median())
            ra = implied(0.80, float(year_med.get(yr, np.nan)), s_b)
            day_rows.append({"date": d, "subset": kind, "n_races": len(ss), "official_win_rate": r_off,
                             "median_S": s_b, "median_S_year_unboosted": float(year_med.get(yr, np.nan)),
                             "implied_rate_vs_year": ra, "verdict": verdict(ra, r_off, 0.80),
                             "programs": ";".join(sorted({x for pl in ss.programs for x in pl}))})
    days = pd.DataFrame(day_rows)
    # (3) overall: by year, unboosted vs W_2YO vs W_OTHER medians
    w["cls"] = np.where(w.win_boost & w.is_2yo, "W_2YO", np.where(w.win_boost, "W_OTHER", "UNBOOSTED"))
    by_year = (w[w.year >= 2010].groupby(["year", "cls"]).S_w.agg(["median", "size"]).unstack("cls"))
    by_year.columns = [f"{a}_{b}" for a, b in by_year.columns]
    # 2yo vs non-2yo in unboosted years (structural difference check)
    struct = (w[(~w.win_boost) & (w.year >= 2010)].groupby(["year", "is_2yo"]).S_w.median().unstack("is_2yo"))
    struct.columns = ["non2yo", "2yo"]
    return prog, days, {"by_year": by_year.reset_index().to_dict(orient="records"),
                        "unboosted_2yo_vs_non2yo_by_year": struct.reset_index().to_dict(orient="records"),
                        "normal_median_S_win_2010_2026": float(w[(~w.win_boost) & (w.year >= 2010)].S_w.median())}


def parse_quotes(qq: pd.DataFrame) -> pd.DataFrame:
    out = []
    for r in qq.itertuples(index=False):
        q = json.loads(r.quotes)
        vals = np.array([v[0] for v in q.values() if v and v[0] is not None and float(v[0]) > 0], float)
        if vals.size == 0:
            continue
        best = min(q.items(), key=lambda kv: (kv[1][0] if kv[1] and kv[1][0] else np.inf))
        n_best = sum(1 for v in q.values() if v and v[0] is not None and abs(float(v[0]) - float(best[1][0])) < 1e-9)
        out.append({"race_id": r.race_id, "bet_type": r.bet_type, "S": float(np.sum(1.0 / vals)), "n_comb": int(vals.size),
                    "fav_combo": best[0], "fav_quote": float(best[1][0]), "fav_tie": n_best > 1,
                    "official_at": r.official_at})
    return pd.DataFrame(out)


def stage1_exotic(rt: pd.DataFrame, qp: pd.DataFrame, bt: str) -> tuple[pd.DataFrame, dict]:
    rate_col = f"rate_{bt}"
    normal = NORMAL[bt]
    x = qp[qp.bet_type == bt].merge(rt[["race_date", "year", rate_col, "super", "ultra", "prog_str", "n_runners"]],
                                    left_on="race_id", right_index=True, how="inner")
    x = x[x.n_comb >= 3]
    unb = x[np.isclose(x[rate_col], normal)]
    year_med = unb.groupby("year").S.median()
    rows = []
    for d, sub in x[~np.isclose(x[rate_col], normal)].groupby("race_date"):
        yr = int(d[:4])
        dd = dt.date.fromisoformat(d)
        nb = [(dd + dt.timedelta(days=k)).isoformat() for k in range(-8, 9) if k != 0]
        neigh = unb[unb.race_date.isin(nb)]
        same = unb[unb.race_date == d]
        for r_off, ss in sub.groupby(rate_col):
            s_b = float(ss.S.median())
            s_same = float(same.S.median()) if len(same) >= 3 else np.nan
            s_nb = float(neigh.S.median()) if len(neigh) >= 10 else np.nan
            s_yr = float(year_med.get(yr, np.nan))
            ra_same = implied(normal, s_same, s_b) if np.isfinite(s_same) else np.nan
            ra_nb = implied(normal, s_nb, s_b) if np.isfinite(s_nb) else np.nan
            ra_yr = implied(normal, s_yr, s_b)
            primary = ra_same if np.isfinite(ra_same) else ra_yr
            rows.append({"date": d, "bet_type": bt, "official_rate": float(r_off), "n_races": len(ss),
                         "median_S": s_b, "median_S_sameday_unboosted": s_same, "median_S_pm8days_unboosted": s_nb,
                         "median_S_year_unboosted": s_yr, "implied_vs_sameday": ra_same, "implied_vs_pm8days": ra_nb,
                         "implied_vs_year": ra_yr, "verdict_primary": verdict(primary, float(r_off), normal),
                         "verdict_pm8days": verdict(ra_nb, float(r_off), normal),
                         "programs": ";".join(sorted(set(";".join(ss.prog_str).split(";")) - {""}))})
    summ = {"n_quoted_races": int(len(x)), "n_unboosted": int(len(unb)),
            "median_S_unboosted_by_year": {int(k): float(v) for k, v in year_med.items()},
            "implied_rate_unboosted_raw": float(1.0 / unb.S.median()) if len(unb) else None}
    pooled = []
    for r_off, ss in x.groupby(rate_col):
        pooled.append({"official_rate": float(r_off), "n_races": int(len(ss)), "median_S": float(ss.S.median()),
                       "raw_implied_1_over_median_S": float(1.0 / ss.S.median()),
                       "implied_vs_unboosted": implied(normal, float(unb.S.median()), float(ss.S.median()))})
    summ["pooled_by_official_rate"] = pooled
    return pd.DataFrame(rows), summ


# ------------------------------------------------------------------------------------------------
# stage 2 helpers
# ------------------------------------------------------------------------------------------------
def per_day(df: pd.DataFrame, days: list[str], col: str) -> np.ndarray:
    return df.groupby("race_date")[col].sum().reindex(days, fill_value=0.0).to_numpy(float)


def cell_boot(bset: pd.DataFrame, pset: pd.DataFrame) -> dict:
    days = sorted(set(bset.race_date) | set(pset.race_date))
    num = np.vstack([per_day(bset, days, "pay"), per_day(pset, days, "pay")] * 2)
    den = np.vstack([per_day(bset, days, "stake"), per_day(pset, days, "stake"),
                     per_day(bset, days, "E"), per_day(pset, days, "E")])
    res = race_block_ratio_bootstrap_ci_v1(num, den, days, b=B, seed=SEED)
    rep = res.replicates
    with np.errstate(divide="ignore", invalid="ignore"):
        R = rep[0] / rep[1]
        T = rep[2] / rep[3]

    def pct(a):
        a = a[np.isfinite(a)]
        return [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))] if a.size else [None, None]
    roi_b, roi_p = float(res.point[0]), float(res.point[1])
    eb, ep = float(bset.E.mean()), float(pset.E.mean())
    return {"n_b": int(len(bset)), "hits_b": int((bset.pay > 0).sum()), "roi_b": roi_b, "roi_b_ci": [r6(res.ci_low[0]), r6(res.ci_high[0])],
            "n_p": int(len(pset)), "hits_p": int((pset.pay > 0).sum()), "roi_p": roi_p, "roi_p_ci": [r6(res.ci_low[1]), r6(res.ci_high[1])],
            "E_b": eb, "E_p": ep, "rho": eb / ep, "R": roi_b / roi_p, "R_ci": pct(R),
            "T": float(res.point[2] / res.point[3]), "T_ci": pct(T), "n_days": len(days)}


def year_weighted(df: pd.DataFrame) -> dict:
    g = df.groupby(df.race_date.str.slice(0, 4)).agg(pay=("pay", "sum"), stake=("stake", "sum"))
    g = g[g.stake >= 20]
    yw = float((g.pay / g.stake).mean()) if len(g) else np.nan
    s19 = df[df.race_date >= "2019-01-01"]
    return {"year_weighted_roi": r6(yw), "n_years": int(len(g)),
            "roi_2019plus": r6(float(s19.pay.sum() / s19.stake.sum())) if len(s19) else None, "n_2019plus": int(len(s19))}


def q_null_T(bset: pd.DataFrame, pset: pd.DataFrame, runners: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
    """T under the q-world: winners re-drawn from q in every race that carries a bet."""
    races = pd.Index(sorted(set(bset.race_id) | set(pset.race_id)))
    rr = runners[runners.race_id.isin(races)].sort_values(["race_id", "horse_id"])
    rid = rr.race_id.to_numpy()
    q = (1.0 / rr.odds.to_numpy(float))
    starts = np.flatnonzero(np.r_[True, rid[1:] != rid[:-1]])
    ends = np.r_[starts[1:], len(rid)]
    S = np.add.reduceat(q, starts)
    qn = q / np.repeat(S, ends - starts)
    cq = np.cumsum(qn)
    base = np.repeat(np.r_[0.0, cq[ends[:-1] - 1]], ends - starts)
    cq_local = cq - base
    race_order = rid[starts]
    hid = rr.horse_id.to_numpy()
    out = np.empty(QREPS)
    bh = bset[["race_id", "horse_id", "odds", "E"]]
    ph = pset[["race_id", "horse_id", "odds", "E"]]
    for k in range(QREPS):
        u = rng.random(len(starts))
        # winner = first runner whose within-race cumulative q reaches u (count of runners below u)
        thr = np.repeat(u, ends - starts)
        below = np.add.reduceat((cq_local < thr).astype(np.int64), starts)
        first = np.minimum(starts + below, ends - 1)
        win_map = pd.Series(hid[first], index=race_order)
        wb = (bh.horse_id.to_numpy() == win_map.reindex(bh.race_id).to_numpy())
        wp = (ph.horse_id.to_numpy() == win_map.reindex(ph.race_id).to_numpy())
        tb = np.sum(wb * bh.odds.to_numpy(float)) / bh.E.sum()
        tp = np.sum(wp * ph.odds.to_numpy(float)) / ph.E.sum()
        out[k] = tb / tp
    return out


def main() -> int:
    prereg_sha = sha(PREREG)
    cal = json.loads(CAL.read_text())
    assert sha(CAL) == json.loads(PREREG.read_text())["inputs"]["boost_calendar"]["sha256"], "calendar changed"
    engine = sa.create_engine(DB)
    rows = load_rows()
    db = load_db(engine)
    rt = race_table(rows, db["names"])
    rt, counts = label_boosts(rt, cal)
    counts_df = pd.DataFrame(counts)
    counts_df.to_csv(OUT / "calendar_mapping_counts.csv", index=False)

    # --------------------------- stage 1 (outcome-free) ---------------------------
    prog, days, s1_extra = stage1_win(rt, cal)
    prog.to_csv(OUT / "stage1_win_programs.csv", index=False)
    days.to_csv(OUT / "stage1_win_days.csv", index=False)
    qp = parse_quotes(db["quotes"])
    q_days, q_summ = stage1_exotic(rt, qp, "quinella")
    t_days, t_summ = stage1_exotic(rt, qp, "trio")
    q_days.to_csv(OUT / "stage1_quinella_days.csv", index=False)
    t_days.to_csv(OUT / "stage1_trio_days.csv", index=False)

    # boost-day table (dates 2015-2026, official rates by bet type)
    btab = (rt[rt.any_boost & (rt.year >= 2015)]
            .groupby(["race_date", "prog_str"] + [f"rate_{b}" for b in ["win", "place"] + EXOTIC_TYPES])
            .size().rename("n_races").reset_index())
    btab.to_csv(OUT / "boost_day_table.csv", index=False)

    # --------------------------- stage 2 (outcome-dependent, sanity) ---------------------------
    d = rows.merge(rt[["win_boost", "is_2yo", "S_w", "rate_win"]], left_on="race_id", right_index=True, how="left")
    d = d[d.race_ok & ~d.dead_heat & (d.year >= 2010) & (d.race_date <= WIN_CUT)
          & np.isfinite(d.odds.astype(float)) & (d.odds > 0)].copy()
    for s, tag in ENS_RUNS.items():
        v = pd.read_parquet(RES / tag / "predictions.parquet", columns=["race_id", "horse_id", "pred"])
        d = d.merge(v.rename(columns={"pred": f"pred{s}"}), on=["race_id", "horse_id"], how="left", validate="one_to_one")
    ens_ev = np.mean([1.0 + d[f"pred{s}"].to_numpy(float) / 100.0 for s in ENS_RUNS], axis=0)
    n_missing_ev = int(np.isnan(ens_ev).sum())
    d["won"] = d.won.astype(bool)
    d["pay"] = d.won * d.odds.astype(float)
    d["stake"] = 1.0
    d["E"] = 1.0 / d.S_w
    # policies
    mn = d.groupby("race_id").odds.transform("min")
    n_mn = d.assign(_m=(d.odds == mn)).groupby("race_id")._m.transform("sum")
    pol = {
        "P1_fav_win": (d.odds == mn) & (n_mn == 1),
        "P2_cap21_win": d.odds < 21,
        "P4_S3_win": pd.Series(ar.match_mask(ar.definition("S3"), ens_ev=ens_ev, single_ev=None,
                                             odds=d.odds.to_numpy(float),
                                             days_since_last=d.days_since_last.to_numpy(float)), index=d.index),
        "P4b_S1_win": pd.Series(ar.match_mask(ar.definition("S1"), ens_ev=ens_ev, single_ev=None,
                                              odds=d.odds.to_numpy(float),
                                              days_since_last=d.days_since_last.to_numpy(float)), index=d.index),
    }
    runners = d[["race_id", "horse_id", "odds"]]
    rng = np.random.default_rng(QSEED)
    cells = []

    def run_cell(name, pname, bmask, pmask, qnull=True):
        sel = pol[pname]
        bset, pset = d[sel & bmask], d[sel & pmask]
        if len(bset) == 0 or len(pset) == 0:
            cells.append({"cell": name, "policy": pname, "n_b": int(len(bset)), "n_p": int(len(pset))})
            return
        res = cell_boot(bset, pset)
        res.update({"cell": name, "policy": pname})
        res["boost_year_weighting"] = year_weighted(bset)
        res["parent_year_weighting"] = year_weighted(pset)
        if qnull:
            tq = q_null_T(bset, pset, runners, rng)
            res["T_qnull_pct"] = [float(np.percentile(tq, 2.5)), float(np.percentile(tq, 97.5))]
            res["T_qnull_two_sided_share"] = float(np.mean(np.abs(tq - 1) >= abs(res["T"] - 1)))
        res["decision"] = ("CONSISTENT" if res["T_ci"][0] is not None and res["T_ci"][0] <= 1 <= res["T_ci"][1]
                           else "INCONSISTENT")
        res["R_ci_excludes_1"] = bool(res["R_ci"][0] is not None and (res["R_ci"][0] > 1 or res["R_ci"][1] < 1))
        cells.append(res)
        print(f"{name:28s} {pname:14s} nb={res['n_b']:6d} roi_b={res['roi_b']:.4f} roi_p={res['roi_p']:.4f} "
              f"rho={res['rho']:.4f} R={res['R']:.4f} {np.round(res['R_ci'], 4)} T={res['T']:.4f} "
              f"{np.round(res['T_ci'], 4)} {res['decision']}", flush=True)

    w2 = d.win_boost & d.is_2yo
    u2 = ~d.win_boost & d.is_2yo
    wo = d.win_boost & ~d.is_2yo
    uo22 = ~d.win_boost & ~d.is_2yo & (d.race_date >= "2022-01-01")
    for pn in ["P1_fav_win", "P2_cap21_win", "P4_S3_win"]:
        run_cell("W_2YO_vs_unboosted_2yo", pn, w2, u2)
    for pn in ["P1_fav_win", "P2_cap21_win", "P4_S3_win"]:
        run_cell("W_OTHER_vs_unboosted_non2yo_2022+", pn, wo, uo22)

    # DiD (secondary): 2yo boosted 2017-2023 vs 2yo unboosted years; same split for non-2yo
    yb = (d.year >= 2017) & (d.year <= 2023)
    yn = (d.year <= 2014) | (d.year >= 2024)
    did = []
    for pn in ["P1_fav_win", "P2_cap21_win"]:
        sel = pol[pn]
        sets = [d[sel & w2 & yb], d[sel & u2 & yn], d[sel & ~d.win_boost & ~d.is_2yo & yb], d[sel & ~d.win_boost & ~d.is_2yo & yn]]
        days_ = sorted(set().union(*[set(s.race_date) for s in sets]))
        num = np.vstack([per_day(s, days_, "pay") for s in sets])
        den = np.vstack([per_day(s, days_, "E") for s in sets])
        res = race_block_ratio_bootstrap_ci_v1(num, den, days_, b=B, seed=SEED)
        rep = res.replicates
        with np.errstate(divide="ignore", invalid="ignore"):
            tdid = (rep[0] / rep[1]) / (rep[2] / rep[3])
        pt = (res.point[0] / res.point[1]) / (res.point[2] / res.point[3])
        a = tdid[np.isfinite(tdid)]
        did.append({"policy": pn, "n": [int(len(s)) for s in sets], "T_DiD": float(pt),
                    "T_DiD_ci": [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))],
                    "pay_over_E": [float(x) for x in res.point],
                    "decision": "CONSISTENT" if np.percentile(a, 2.5) <= 1 <= np.percentile(a, 97.5) else "INCONSISTENT"})
        print("DiD", did[-1], flush=True)

    # quinella favourite (real dividends, 2025+)
    divs = db["div"].copy()
    divs["sel"] = divs.selection.apply(lambda s: tuple(sorted(int(x) for x in json.loads(s))) if s else None)
    qd = qp[(qp.bet_type == "quinella") & ~qp.fav_tie].merge(
        rt[["race_date", "rate_quinella", "super", "ultra", "race_ok"]], left_on="race_id", right_index=True)
    qd = qd[(qd.race_date >= "2025-01-01") & qd.race_ok]
    qdiv = divs[divs.bet_type == "quinella"]
    paid = {r: dict(zip(g.sel, g.odds.astype(float))) for r, g in qdiv.groupby("race_id")}
    qd = qd[qd.race_id.isin(list(paid))].copy()
    qd["combo"] = qd.fav_combo.apply(lambda c: tuple(sorted(int(x) for x in c.split("-"))))
    qd["pay"] = [paid[r].get(c, 0.0) for r, c in zip(qd.race_id, qd.combo)]
    qd["stake"] = 1.0
    qd["E"] = 1.0 / qd.S
    qb = qd[qd.rate_quinella > 0.775 + 1e-9]
    qpar = qd[np.isclose(qd.rate_quinella, 0.775)]
    if len(qb) and len(qpar):
        res = cell_boot(qb, qpar)
        res.update({"cell": "Q_SUPER+ULTRA_vs_unboosted_2025+", "policy": "P5_fav_quinella",
                    "decision": "CONSISTENT" if res["T_ci"][0] <= 1 <= res["T_ci"][1] else "INCONSISTENT",
                    "R_ci_excludes_1": bool(res["R_ci"][0] > 1 or res["R_ci"][1] < 1),
                    "boost_days": sorted(qb.race_date.unique().tolist())})
        cells.append(res)
        print("Q", {k: res[k] for k in ["n_b", "roi_b", "roi_p", "rho", "R", "R_ci", "T", "T_ci"]}, flush=True)

    # place favourite on 2025-05-04 (R only)
    pdiv = divs[divs.bet_type == "place"]
    ppaid = {r: dict(zip(g.sel, g.odds.astype(float))) for r, g in pdiv.groupby("race_id")}
    pr = rows[(rows.race_date >= "2025-01-01") & rows.race_ok & np.isfinite(rows.odds.astype(float)) & (rows.odds > 0)].copy()
    pr = pr[pr.race_id.isin(list(ppaid))]
    m2 = pr.groupby("race_id").odds.transform("min")
    nm2 = pr.assign(_m=(pr.odds == m2)).groupby("race_id")._m.transform("sum")
    pf = pr[(pr.odds == m2) & (nm2 == 1)].merge(rt[["rate_place"]], left_on="race_id", right_index=True)
    pf["pay"] = [ppaid[r].get((int(h),), 0.0) for r, h in zip(pf.race_id, pf.horse_number)]
    pf["stake"] = 1.0
    pf["E"] = 1.0
    pb, pp = pf[pf.rate_place > 0.80 + 1e-9], pf[np.isclose(pf.rate_place, 0.80)]
    if len(pb) and len(pp):
        res = cell_boot(pb, pp)
        res = {k: res[k] for k in ["n_b", "hits_b", "roi_b", "roi_b_ci", "n_p", "hits_p", "roi_p", "roi_p_ci", "R", "R_ci"]}
        res.update({"cell": "PLACE_2025-05-04_vs_unboosted_2025+", "policy": "P3_fav_place",
                    "expected_R_if_boost_only": 0.85 / 0.80, "boost_days": sorted(pb.race_date.unique().tolist())})
        cells.append(res)
        print("P", res, flush=True)

    # diagnostic: subsidy in S1/S3
    subsidy = {}
    for pn in ["P4b_S1_win", "P4_S3_win"]:
        sel = d[pol[pn]]
        days_ = sorted(sel.race_date.unique())
        parts = [sel, sel[~sel.win_boost], sel[sel.win_boost]]
        res = race_block_ratio_bootstrap_ci_v1(np.vstack([per_day(s, days_, "pay") for s in parts]),
                                               np.vstack([per_day(s, days_, "stake") for s in parts]), days_, b=B, seed=SEED)
        subsidy[pn] = {"n_all": int(len(sel)), "n_boosted": int(sel.win_boost.sum()),
                       "share_boosted": float(sel.win_boost.mean()),
                       "roi_all": float(res.point[0]), "roi_all_ci": [r6(res.ci_low[0]), r6(res.ci_high[0])],
                       "roi_excl_boosted": float(res.point[1]), "roi_excl_ci": [r6(res.ci_low[1]), r6(res.ci_high[1])],
                       "roi_boosted_only": float(res.point[2]), "roi_boosted_ci": [r6(res.ci_low[2]), r6(res.ci_high[2])],
                       "mechanical_subsidy_pt": float(sel.win_boost.mean() * (1 - 0.80 / 0.85) * res.point[2] * 100)}
        print("subsidy", pn, subsidy[pn], flush=True)

    out = {"test_id": "R01_premium_days", "prereg_sha256": prereg_sha, "calendar_sha256": sha(CAL),
           "rows_sha256": sha(ROWS), "n_missing_ens_ev_in_stage2_rows": n_missing_ev,
           "calendar_mapping_counts": counts,
           "stage1": {"win_programs": prog.to_dict(orient="records"), "win_days": days.to_dict(orient="records"),
                      "win_extra": s1_extra, "quinella_days": q_days.to_dict(orient="records"), "quinella_summary": q_summ,
                      "trio_days": t_days.to_dict(orient="records"), "trio_summary": t_summ},
           "stage2": {"cells": cells, "did": did, "subsidy_diagnostic": subsidy}}
    (OUT / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=lambda o: (
        o.item() if hasattr(o, "item") else str(o))))
    pd.DataFrame([{k: v for k, v in c.items() if not isinstance(v, dict)} for c in cells]).to_csv(
        OUT / "stage2_cells.csv", index=False)
    print("prereg", prereg_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
