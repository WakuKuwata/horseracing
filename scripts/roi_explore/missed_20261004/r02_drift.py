"""R02_odds_drift: pre-race (judged) -> final win-odds drift and what it does to S1..S5.

Pre-registered: artifacts/roi_explore/missed_20261004/R02_odds_drift/prereg.json (sha256 in prereg.sha256).
Inputs come from r02_build.py (production features + read-only DB extracts) and R05_settlement.

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r02_drift.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time

import numpy as np
import pandas as pd
from horseracing_eval import attention_rules as ar
from horseracing_eval.bootstrap import (
    block_bootstrap_counts,
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
)
from scipy import stats

from horseracing_training import market_ev

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/roi_explore/missed_20261004/R02_odds_drift"
R05 = ROOT / "artifacts/roi_explore/missed_20261004/R05_settlement"
RES = ROOT / "artifacts/roi_explore/results"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
ENS_DIR = ROOT / "artifacts/market_ev/mev-ens15-v1"
SINGLE_DIR = ROOT / "artifacts/market_ev/mev-binary-v2"
FREEZE = ENS_DIR / "rules_S1_S5_freeze.json"
ENS_RUNS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
SINGLE_RUN = "armC_binary_drop-sameday+weightlive_serving_v2_2007"

SEED = 20261004
B_SMALL = 2000
B_ROI = 20000
SEED_ROI = 20260905
N_PERM = 10000
TB_EDGES = [0.0, 1.0, 4.0, 10.0, np.inf]
TB = ["T1", "T2", "T3", "T4"]
OB_EDGES = [1.0, 5.0, 10.0, 20.0, 40.0, 100.0, np.inf]
OB = ["B1_1-5", "B2_5-10", "B3_10-20", "B4_20-40", "B5_40-100", "B6_100+"]
EV_EDGES = [-np.inf, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, np.inf]
RULES = ["S1", "S2", "S3", "S4", "S5"]
SIGMA_CURVE = {}  # filled from the freeze
MARKET_COLS = ["field_size", "race_ok", "q", "odds_rank", "fav_odds", "second_odds", "odds_gap12", "fav_q",
               "q_share_of_fav", "q_entropy_norm", "n_fav_under_2", "n_odds_under_10"]


def r6(x):
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return x
    return None if not np.isfinite(xf) else round(xf, 6)


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def band(x, edges, labels):
    idx = np.searchsorted(np.asarray(edges[1:-1]), np.asarray(x, float), side="right")
    return np.asarray(labels, dtype=object)[idx]


# ------------------------------------------------------------------ market block (copy of build_features)

def market_block(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Recompute the race-level market-structure columns exactly as market_ev.build_features does,
    grouping by ``key`` (a race or a race x version id). ``df`` must be ordered by (key, horse_number)."""
    df = df.copy()
    g = df.groupby(key, sort=False)
    df["field_size"] = g["horse_id"].transform("size").astype(float)
    df["race_ok"] = g["odds"].transform(lambda s: bool(s.notna().all() and (s > 0).all()))
    inv = 1.0 / df["odds"]
    df["q"] = inv / inv.groupby(df[key]).transform("sum")
    order = df.sort_values([key, "odds", "horse_number"]).index
    rank = np.empty(len(df), dtype=float)
    pos = df.index.get_indexer(order)
    rank[pos] = df.loc[order].groupby(key, sort=False).cumcount().to_numpy() + 1
    df["odds_rank"] = rank
    fav = df[df["odds_rank"] == 1].set_index(key)["odds"]
    sec = df[df["odds_rank"] == 2].set_index(key)["odds"]
    df["fav_odds"] = df[key].map(fav).astype(float)
    df["second_odds"] = df[key].map(sec).astype(float)
    df["odds_gap12"] = df["second_odds"] - df["fav_odds"]
    df["fav_q"] = df.groupby(key, sort=False)["q"].transform("max")
    df["q_share_of_fav"] = df["q"] / df["fav_q"]
    ent = (-(df["q"] * np.log(df["q"].clip(lower=1e-12)))).groupby(df[key]).transform("sum")
    df["q_entropy_norm"] = ent / np.log(df["field_size"].clip(lower=2))
    df["n_fav_under_2"] = (df["odds"] < 2.0).astype(float).groupby(df[key]).transform("sum")
    df["n_odds_under_10"] = (df["odds"] < 10.0).astype(float).groupby(df[key]).transform("sum")
    return df


# ------------------------------------------------------------------ models

class Scorer:
    def __init__(self):
        self.ens = market_ev.EnsembleMarketEvModel.load(ENS_DIR, "mev-ens15-v1")
        man = self.ens.booster_manifest_for_year(2026)
        self.ens_boosters, self.ens_manifest_sha = self.ens.load_members(man)
        self.single = market_ev.MarketEvModel.load(SINGLE_DIR, "mev-binary-v2")
        path = self.single.booster_path_for_year(2026)
        self.single_booster, self.single_sha = market_ev._read_booster(path)
        self.provenance = {"ens_manifest": man.name, "ens_manifest_sha256": self.ens_manifest_sha,
                           "single_booster": path.name, "single_booster_sha256": self.single_sha}

    def score(self, feats: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        X = self.ens.design_matrix(feats)
        p_ens = np.mean(np.vstack([b.predict(X) for b in self.ens_boosters]), axis=0)
        Xs = self.single.design_matrix(feats)
        p_single = self.single_booster.predict(Xs)
        return p_ens, p_single


def select(ens_ev, single_ev, odds, dsl) -> dict[str, np.ndarray]:
    return {r: ar.match_mask(ar.definition(r), ens_ev=ens_ev, single_ev=single_ev, odds=odds,
                             days_since_last=dsl) for r in RULES}


# ------------------------------------------------------------------ bootstrap helpers

def weighted_median(v: np.ndarray, w: np.ndarray) -> float:
    m = w > 0
    if not m.any():
        return np.nan
    v, w = v[m], w[m]
    o = np.argsort(v, kind="mergesort")
    v, w = v[o], w[o]
    cw = np.cumsum(w)
    return float(v[np.searchsorted(cw, 0.5 * cw[-1])])


def day_index(dates: np.ndarray, universe: list[str]) -> np.ndarray:
    pos = {d: i for i, d in enumerate(universe)}
    return np.array([pos[d] for d in dates], dtype=np.int64)


def per_day(values: np.ndarray, didx: np.ndarray, n_days: int) -> np.ndarray:
    return np.bincount(didx, weights=values.astype(float), minlength=n_days)


def ratio_ci(num_rows, den_rows, didx, days, b=B_SMALL, seed=SEED):
    num = per_day(num_rows, didx, len(days))
    den = per_day(den_rows, didx, len(days))
    if den.sum() <= 0:
        return {"point": None, "ci95": [None, None]}
    bb = race_block_ratio_bootstrap_ci_v1(num, den, days, b=b, seed=seed)
    return {"point": r6(bb.point[0]), "ci95": [r6(bb.ci_low[0]), r6(bb.ci_high[0])]}


# ------------------------------------------------------------------ main

def main() -> None:
    t0 = time.time()
    prereg_sha = sha(OUT / "prereg.json")
    freeze = json.loads(FREEZE.read_text())
    for r in RULES:
        SIGMA_CURVE[r] = {float(k): v["overlap_with_unperturbed"] for k, v in freeze["rules"][r]["price_noise_ALL"].items()}

    feats = pd.read_parquet(OUT / "feats_cand.parquet")
    chaos = pd.read_parquet(OUT / "db_chaos.parquet")
    rh = pd.read_parquet(OUT / "db_race_horses.parquet")
    resdb = pd.read_parquet(OUT / "db_results.parquet")
    mev = pd.read_parquet(OUT / "db_mev_pending.parquet")
    picks = pd.read_parquet(OUT / "db_attention_picks.parquet")
    aff = pd.read_csv(R05 / "affected_races.csv", dtype={"race_id": str})
    arc = pd.read_csv(R05 / "archived_final_odds.csv", dtype={"race_id": str, "horse_id": str})
    feats = feats.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    feats["odds"] = feats["odds"].astype(float)
    feats["popularity"] = feats["popularity"].astype(float)
    out: dict = {"test_id": "R02_odds_drift", "prereg_sha256": prereg_sha,
                 "inputs": {"feats_cand_sha256": sha(OUT / "feats_cand.parquet"),
                            "affected_races_sha256": sha(R05 / "affected_races.csv"),
                            "archived_final_odds_sha256": sha(R05 / "archived_final_odds.csv"),
                            "freeze_sha256": sha(FREEZE)}}
    scorer = Scorer()
    out["models"] = scorer.provenance

    # ---------------------------------------------------------------- M0: market block reproduces builder
    m0 = market_block(feats[["race_id", "horse_id", "horse_number", "odds"]], "race_id")
    m0_diff = {}
    for c in MARKET_COLS:
        a = m0[c].to_numpy(float)
        b_ = feats[c].astype(float).to_numpy()
        same = (a == b_) | (np.isnan(a) & np.isnan(b_))
        m0_diff[c] = int((~same).sum())
    out["check_M0_market_block_mismatched_rows"] = m0_diff
    print("M0", m0_diff, flush=True)

    # ---------------------------------------------------------------- race population
    win = resdb[(resdb.result_status == "finished") & (resdb.finish_order == 1)]
    nwin = win.groupby("race_id").size()
    races = feats.groupby("race_id").agg(race_date=("race_date", "first"), n=("horse_id", "size")).reset_index()
    races = races.merge(aff[["race_id", "affected", "correctable", "timing_class", "staleness_min"]],
                        on="race_id", how="left")
    has_res = set(resdb.race_id)
    races["has_results"] = races.race_id.isin(has_res)
    races["n_winners"] = races.race_id.map(nwin).fillna(0).astype(int)
    races["dead_heat"] = races.n_winners != 1
    pop_log = {"candidate_flat_races": int(len(races))}
    ok = races[races.has_results & (races.timing_class.fillna("") != "NO_RESULTS") & races.affected.notna()]
    pop_log["with_results_and_R05_class"] = int(len(ok))

    # final odds per horse
    fin_parts = []
    excluded_final = {"affected_uncorrectable": 0, "archive_incomplete": 0}
    arc_idx = arc.set_index(["race_id", "horse_number"])
    for rid, grp in feats[feats.race_id.isin(ok.race_id)].groupby("race_id", sort=False):
        rr = ok[ok.race_id == rid].iloc[0]
        if int(rr.affected) == 0:
            fin_parts.append(pd.DataFrame({"race_id": rid, "horse_id": grp.horse_id.values,
                                           "O_F": grp.odds.values, "pop_F": grp.popularity.values,
                                           "final_src": "db_stored"}))
        elif bool(rr.correctable):
            try:
                a = arc_idx.loc[[(rid, int(h)) for h in grp.horse_number]]
            except KeyError:
                excluded_final["archive_incomplete"] += 1
                continue
            if a["result_page_odds"].isna().any() or (a["horse_id"].values != grp.horse_id.values).any():
                excluded_final["archive_incomplete"] += 1
                continue
            fin_parts.append(pd.DataFrame({"race_id": rid, "horse_id": grp.horse_id.values,
                                           "O_F": a.result_page_odds.astype(float).values,
                                           "pop_F": a.popularity_page.astype(float).values,
                                           "official_payout_yen": a.official_payout_yen.astype(float).values,
                                           "final_src": "archive"}))
        else:
            excluded_final["affected_uncorrectable"] += 1
    fin = pd.concat(fin_parts, ignore_index=True)
    pop_log["final_available_races"] = int(fin.race_id.nunique())
    pop_log["excluded_final"] = excluded_final

    # ---------------------------------------------------------------- judged pairs
    pairs = []  # dicts: pair_id, race_id, source, hours, J odds/pop arrays aligned to feats order
    chaos_log = {"active": 0, "not_in_final_set": 0, "field_mismatch": 0, "used": 0}
    fset = set(fin.race_id)
    fe_by_race = {rid: g for rid, g in feats.groupby("race_id", sort=False)}
    for row in chaos[chaos.status == "active"].itertuples():
        chaos_log["active"] += 1
        rid = row.race_id
        if rid not in fset:
            chaos_log["not_in_final_set"] += 1
            continue
        g = fe_by_race[rid]
        fld = {x["horse_id"]: x for x in json.loads(row.field_json)}
        if set(fld) != set(g.horse_id):
            chaos_log["field_mismatch"] += 1
            continue
        oj = np.array([float(fld[h]["odds"]) for h in g.horse_id])
        pj = np.array([float(fld[h]["popularity"]) if fld[h].get("popularity") is not None else np.nan
                       for h in g.horse_id])
        chaos_log["used"] += 1
        pairs.append({"race_id": rid, "source": "CHAOS", "hours": row.seconds_to_post / 3600.0,
                      "O_J": oj, "pop_J": pj, "snap": row.snap_id})
    stored_log = {"candidates": 0, "not_in_final_set": 0, "used": 0}
    sp = ok[(ok.affected == 1) & (ok.correctable == True) & (ok.timing_class == "PRE_RACE_SNAPSHOT")]  # noqa: E712
    for rr in sp.itertuples():
        stored_log["candidates"] += 1
        if rr.race_id not in fset:
            stored_log["not_in_final_set"] += 1
            continue
        g = fe_by_race[rr.race_id]
        stored_log["used"] += 1
        pairs.append({"race_id": rr.race_id, "source": "STORED_PRE", "hours": float(rr.staleness_min) / 60.0,
                      "O_J": g.odds.to_numpy(float), "pop_J": g.popularity.to_numpy(float), "snap": None})
    pdf = pd.DataFrame([{k: v for k, v in p.items() if k not in ("O_J", "pop_J")} for p in pairs])
    pdf["tb"] = band(pdf.hours.to_numpy(), TB_EDGES, TB)
    pdf["prio"] = np.where(pdf.source == "CHAOS", 0, 1)
    pdf["i"] = np.arange(len(pdf))
    keep = pdf.sort_values(["race_id", "tb", "prio", "hours"]).drop_duplicates(["race_id", "tb"], keep="first")
    dedup_dropped = int(len(pdf) - len(keep))
    keep = keep.sort_values("i")
    pairs = [pairs[i] for i in keep.i]
    pdf = keep.drop(columns=["prio", "i"]).reset_index(drop=True)
    pdf["pair_id"] = [f"{r}|{s}|{t}" for r, s, t in zip(pdf.race_id, pdf.source, pdf.tb)]
    # judged odds must be valid
    valid = [bool(np.all(np.isfinite(p["O_J"])) and np.all(p["O_J"] >= 1.0)) for p in pairs]
    invalid_j = int(len(valid) - sum(valid))
    pairs = [p for p, v in zip(pairs, valid) if v]
    pdf = pdf[np.array(valid)].reset_index(drop=True)
    out["population"] = pop_log | {"chaos": chaos_log, "stored_pre": stored_log,
                                   "dedup_dropped_same_race_same_band": dedup_dropped,
                                   "invalid_judged_odds_pairs": invalid_j,
                                   "pairs_by_source_band": pdf.groupby(["source", "tb"]).size().unstack(fill_value=0).to_dict(),
                                   "n_pairs": int(len(pdf)), "n_races": int(pdf.race_id.nunique()),
                                   "race_days": sorted(feats[feats.race_id.isin(pdf.race_id)].race_date.unique().tolist())}
    print("pairs", len(pdf), pdf.groupby("tb").size().to_dict(), flush=True)

    # ---------------------------------------------------------------- versions & scoring
    # F version for every final-available race
    fv = feats[feats.race_id.isin(fset)].copy()
    fv = fv.merge(fin, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    fv["odds"] = fv["O_F"].astype(float)
    fv["popularity"] = np.where(fv["pop_F"].notna(), fv["pop_F"], np.nan)
    fv = fv.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    mb = market_block(fv[["race_id", "horse_id", "horse_number", "odds"]], "race_id")
    for c in MARKET_COLS:
        fv[c] = mb[c].values
    pr = fv.groupby("race_id")["odds"].rank(method="min")
    fv["popularity"] = np.where(fv["popularity"].isna(), pr, fv["popularity"])
    pe, ps = scorer.score(fv)
    fv["pF_ens"], fv["pF_single"] = pe, ps
    fv["EVe_F"], fv["EVs_F"] = pe * fv.odds.values, ps * fv.odds.values
    # J versions
    jparts = []
    for p, row in zip(pairs, pdf.itertuples()):
        g = fe_by_race[p["race_id"]].copy()
        g["odds"] = p["O_J"]
        g["popularity"] = p["pop_J"]
        g["pair_id"] = row.pair_id
        jparts.append(g)
    jv = pd.concat(jparts, ignore_index=True)
    jv = jv.sort_values(["pair_id", "horse_number"]).reset_index(drop=True)
    mb = market_block(jv[["pair_id", "horse_id", "horse_number", "odds"]], "pair_id")
    for c in MARKET_COLS:
        jv[c] = mb[c].values
    prj = jv.groupby("pair_id")["odds"].rank(method="min")
    jv["popularity"] = np.where(jv["popularity"].isna(), prj, jv["popularity"])
    pe, ps = scorer.score(jv)
    jv["EVe_J"], jv["EVs_J"] = pe * jv.odds.values, ps * jv.odds.values
    jv["pJ_ens"], jv["pJ_single"] = pe, ps
    # stored version (M1)
    sv = feats.copy()
    pe, ps = scorer.score(sv)
    sv["EVe_S"], sv["EVs_S"] = pe * sv.odds.values, ps * sv.odds.values
    print("scored", round(time.time() - t0, 1), "s", flush=True)

    # ---------------------------------------------------------------- M1: research parity
    rr_ids = set(pdf.race_id) | set(fv.race_id)
    base = None
    evs = []
    for s, tag in ENS_RUNS.items():
        v = pd.read_parquet(RES / tag / "predictions.parquet", columns=["race_id", "horse_id", "pred"])
        v = v[v.race_id.isin(rr_ids)].sort_values(["race_id", "horse_id"]).reset_index(drop=True)
        if base is None:
            base = v[["race_id", "horse_id"]].copy()
        evs.append(1.0 + v.pred.to_numpy(float) / 100.0)
    base["EVe_R"] = np.mean(evs, axis=0)
    v = pd.read_parquet(RES / SINGLE_RUN / "predictions.parquet", columns=["race_id", "horse_id", "pred"])
    base = base.merge(v.assign(EVs_R=1.0 + v.pred / 100.0)[["race_id", "horse_id", "EVs_R"]],
                      on=["race_id", "horse_id"], how="left")
    m1 = base.merge(sv[["race_id", "horse_id", "EVe_S", "EVs_S"]], on=["race_id", "horse_id"], how="inner")
    de = np.abs(m1.EVe_R - m1.EVe_S)
    ds = np.abs(m1.EVs_R - m1.EVs_S)
    out["check_M1_research_parity"] = {
        "rows_compared": int(len(m1)), "research_rows_in_scope": int(len(base)),
        "ens_share_abs_diff_lt_1e-9": r6((de < 1e-9).mean()), "ens_max_abs_diff": r6(de.max()),
        "single_share_abs_diff_lt_1e-9": r6((ds < 1e-9).mean()), "single_max_abs_diff": r6(ds.max()),
        "pass": bool((de < 1e-9).mean() >= 0.999 and (ds < 1e-9).mean() >= 0.999)}
    print("M1", out["check_M1_research_parity"], flush=True)

    # ---------------------------------------------------------------- M2: production pending rows
    m2 = {}
    for ver, pcol in (("mev-ens15-v1", "ens"), ("mev-binary-v2", "single")):
        mm = mev[mev.model_version == ver]
        parts = []
        for rid, grp in mm.groupby("race_id"):
            g = fe_by_race.get(rid)
            if g is None or set(grp.horse_id) != set(g.horse_id):
                continue
            g = g.copy()
            omap = dict(zip(grp.horse_id, grp.odds_used.astype(float)))
            oj = np.array([omap[h] for h in g.horse_id])
            same = bool(np.allclose(oj, g.odds.to_numpy(float), rtol=0, atol=1e-12))
            g["odds"] = oj
            if not same:  # DB popularity belongs to other odds -> rank(min)
                g["popularity"] = g.groupby("race_id")["odds"].rank(method="min")
            parts.append(g)
        if not parts:
            m2[ver] = {"rows": 0}
            continue
        gv = pd.concat(parts, ignore_index=True).sort_values(["race_id", "horse_number"]).reset_index(drop=True)
        mb = market_block(gv[["race_id", "horse_id", "horse_number", "odds"]], "race_id")
        for c in MARKET_COLS:
            gv[c] = mb[c].values
        pe, ps = scorer.score(gv)
        gv["p"] = pe if pcol == "ens" else ps
        cmp_ = gv.merge(mm[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])
        dd = np.abs(cmp_.p - cmp_.win_prob.astype(float))
        cmp_["bad"] = dd >= 1e-9
        per_race = cmp_.groupby("race_id").agg(n=("bad", "size"), bad=("bad", "sum"))
        bad_races = per_race[per_race.bad > 0]
        # diagnostic: can a different same-day weather/going reproduce a fully mismatched race exactly?
        wg = {}
        spec = scorer.ens.spec if pcol == "ens" else scorer.single.spec
        for rid in bad_races.index:
            g = gv[gv.race_id == rid]
            hits = []
            for w in spec["cat_maps"]["weather"]:
                for go in spec["cat_maps"]["going"]:
                    gg = g.copy()
                    gg["weather"], gg["going"] = w, go
                    pe2, ps2 = scorer.score(gg)
                    p2 = pe2 if pcol == "ens" else ps2
                    c2 = gg.assign(p=p2).merge(mm[["race_id", "horse_id", "win_prob"]], on=["race_id", "horse_id"])
                    if (np.abs(c2.p - c2.win_prob.astype(float)) < 1e-9).all():
                        hits.append(f"{w}/{go}")
            wg[rid] = {"now": f"{g.weather.iloc[0]}/{g.going.iloc[0]}", "rows": int(bad_races.loc[rid, "n"]),
                       "mismatched_rows": int(bad_races.loc[rid, "bad"]), "exact_with_weather_going": hits}
        m2[ver] = {"rows": int(len(cmp_)), "races": int(cmp_.race_id.nunique()),
                   "share_lt_1e-9": r6((dd < 1e-9).mean()), "share_lt_1e-6": r6((dd < 1e-6).mean()),
                   "max_abs_diff": r6(dd.max()), "races_with_mismatch": wg,
                   "pass": bool((dd < 1e-9).mean() >= 0.99)}
    m2["note"] = ("popularity = DB popularity when odds_used == DB odds (all 18 races), else rank(min). Mismatches are "
                  "explained when a different same-day weather/going reproduces the stored win_prob exactly: the "
                  "judged-time model saw different race-day fields that were updated later (not replayed by R02).")
    out["check_M2_production_pending"] = m2
    print("M2", m2, flush=True)

    # ---------------------------------------------------------------- long pair-horse table
    ph = jv[["pair_id", "race_id", "horse_id", "horse_number", "odds", "EVe_J", "EVs_J", "days_since_last",
             "race_date"]].rename(columns={"odds": "O_J"})
    ph = ph.merge(pdf[["pair_id", "source", "tb", "hours"]], on="pair_id", how="left")
    ph = ph.merge(fv[["race_id", "horse_id", "odds", "EVe_F", "EVs_F", "q", "final_src", "official_payout_yen"]]
                  .rename(columns={"odds": "O_F", "q": "q_F"}), on=["race_id", "horse_id"], how="left",
                  validate="many_to_one")
    ph["d"] = np.log(ph.O_F / ph.O_J)
    ph["bJ"] = band(ph.O_J.to_numpy(), OB_EDGES, OB)
    ph["bF"] = band(ph.O_F.to_numpy(), OB_EDGES, OB)
    jsel = select(ph.EVe_J.to_numpy(), ph.EVs_J.to_numpy(), ph.O_J.to_numpy(), ph.days_since_last.to_numpy())
    fsel = select(ph.EVe_F.to_numpy(), ph.EVs_F.to_numpy(), ph.O_F.to_numpy(), ph.days_since_last.to_numpy())
    for r in RULES:
        ph[f"J_{r}"] = jsel[r]
        ph[f"F_{r}"] = fsel[r]
    wset = set(zip(win.race_id, win.horse_id))
    ph["won"] = [(a, b) in wset for a, b in zip(ph.race_id, ph.horse_id)]
    ph["dead_heat"] = ph.race_id.map(dict(zip(races.race_id, races.dead_heat)))
    o_settle = np.where((ph.final_src == "archive") & ph.won & ph.official_payout_yen.notna(),
                        ph.official_payout_yen / 100.0, ph.O_F)
    ph["O_settle"] = o_settle
    ph["ret"] = np.where(ph.won, ph.O_settle, 0.0)
    ph["gapf"] = (ph.days_since_last >= 14) & (ph.days_since_last <= 112)
    ph.to_parquet(OUT / "pairs.parquet", index=False)

    days = sorted(ph.race_date.unique().tolist())
    didx = day_index(ph.race_date.to_numpy(), days)
    nd = len(days)

    # ---------------------------------------------------------------- (a) drift
    def drift_summary(sub: pd.DataFrame) -> dict:
        d = sub.d.to_numpy()
        if len(d) == 0:
            return {"n": 0}
        return {"n": int(len(d)), "races": int(sub.race_id.nunique()), "mean": r6(d.mean()),
                "median": r6(np.median(d)), "p10": r6(np.quantile(d, .1)), "p90": r6(np.quantile(d, .9)),
                "sd": r6(d.std(ddof=1)) if len(d) > 1 else None, "share_abs_gt_0.1": r6((np.abs(d) > 0.1).mean())}

    a = {"by_bandJ_x_tb": {}, "by_tb": {}, "by_bandJ": {}, "by_bandF_x_tb": {}, "pooled": drift_summary(ph),
         "by_source_tb": {}}
    for t in TB:
        a["by_tb"][t] = drift_summary(ph[ph.tb == t])
        for b_ in OB:
            a["by_bandJ_x_tb"][f"{b_}|{t}"] = drift_summary(ph[(ph.tb == t) & (ph.bJ == b_)])
            a["by_bandF_x_tb"][f"{b_}|{t}"] = drift_summary(ph[(ph.tb == t) & (ph.bF == b_)])
        for s in ("CHAOS", "STORED_PRE"):
            a["by_source_tb"][f"{s}|{t}"] = drift_summary(ph[(ph.tb == t) & (ph.source == s)])
    for b_ in OB:
        a["by_bandJ"][b_] = drift_summary(ph[ph.bJ == b_])
    out["a_drift"] = a

    # ---------------------------------------------------------------- (b) selection overlap & EV shrink
    counts = block_bootstrap_counts(nd, B_SMALL, SEED).astype(float)  # (B, nd)
    w_rows = counts[:, didx]  # (B, rows) weights

    def implied_sigma(rule, ret):
        pts = [(0.0, 1.0)] + sorted(SIGMA_CURVE[rule].items())
        if ret is None:
            return None
        for (s0, o0), (s1, o1) in zip(pts[:-1], pts[1:]):
            if o1 <= ret <= o0:
                return r6(s0 + (o0 - ret) / (o0 - o1) * (s1 - s0))
        return ">0.3" if ret < pts[-1][1] else "<0"

    bres = {}
    for r in RULES:
        evcol = "EVe" if ar.definition(r).uses_ensemble else "EVs"
        thr = ar.definition(r).ev_gt
        bres[r] = {}
        for t in TB + ["ALL"]:
            m = np.ones(len(ph), bool) if t == "ALL" else (ph.tb == t).to_numpy()
            J = ph[f"J_{r}"].to_numpy() & m
            F = ph[f"F_{r}"].to_numpy() & m
            JF = J & F
            shrink = (ph[f"{evcol}_F"] / ph[f"{evcol}_J"]).to_numpy()
            ent = {"n_J": int(J.sum()), "n_F": int(F.sum()), "n_JF": int(JF.sum()),
                   "n_pairs": int(ph[m].pair_id.nunique())}
            ent["retention"] = ratio_ci(JF, J, didx, days)
            ent["recall"] = ratio_ci(JF, F, didx, days)
            ent["count_ratio_J_over_F"] = ratio_ci(J, F, didx, days)
            if J.sum() > 0:
                sj = shrink[J]
                ent["ev_shrink_median"] = r6(np.median(sj))
                ent["ev_shrink_mean"] = r6(sj.mean())
                reps = np.array([weighted_median(sj, w_rows[bb][J]) for bb in range(B_SMALL)])
                ent["ev_shrink_median_ci95"] = [r6(np.nanquantile(reps, .025)), r6(np.nanquantile(reps, .975))]
                ent["share_J_still_above_thr_at_final"] = r6((ph[f"{evcol}_F"].to_numpy()[J] > thr).mean())
                ent["mean_EV_J"] = r6(ph[f"{evcol}_J"].to_numpy()[J].mean())
                ent["mean_EV_F_of_J"] = r6(ph[f"{evcol}_F"].to_numpy()[J].mean())
                ent["median_drift_d_of_J"] = r6(np.median(ph.d.to_numpy()[J]))
            if F.sum() > 0:
                ent["mean_EV_F_of_F"] = r6(ph[f"{evcol}_F"].to_numpy()[F].mean())
            ent["implied_sigma_138_curve"] = implied_sigma(r, ent["retention"]["point"])
            bres[r][t] = ent
    out["b_selection_and_ev"] = bres
    out["b_138_sigma_curve"] = SIGMA_CURVE

    # ---------------------------------------------------------------- (c) selected vs unselected drift
    def strat_D(d, sel, strata):
        Dn = 0.0
        Ns = 0
        for s in np.unique(strata):
            m = strata == s
            ns = sel[m].sum()
            nu = m.sum() - ns
            if ns == 0 or nu == 0:
                continue
            Dn += ns * (d[m][sel[m]].mean() - d[m][~sel[m]].mean())
            Ns += ns
        return Dn / Ns if Ns else np.nan

    def perm_test(d, sel, strata, n_perm=N_PERM, seed=SEED):
        rng = np.random.default_rng(seed)
        obs = strat_D(d, sel, strata)
        Dn = np.zeros(n_perm)
        Ns = 0
        for s in np.unique(strata):
            m = np.flatnonzero(strata == s)
            ns = int(sel[m].sum())
            n = len(m)
            if ns == 0 or ns == n:
                continue
            ds = d[m]
            T = ds.sum()
            # sum of d over a random subset of size ns, n_perm times (chunked)
            sums = np.empty(n_perm)
            ch = max(1, int(2e7 // max(n, 1)))
            for a0 in range(0, n_perm, ch):
                k = min(ch, n_perm - a0)
                keys = rng.random((k, n))
                idx = np.argpartition(keys, ns - 1, axis=1)[:, :ns]
                sums[a0:a0 + k] = ds[idx].sum(axis=1)
            Dn += sums * n / (n - ns) - ns * T / (n - ns)
            Ns += ns
        Dp = Dn / Ns
        p = (1 + np.sum(np.abs(Dp) >= abs(obs) - 1e-15)) / (n_perm + 1)
        return obs, p, Dp

    def boot_D(d, sel, strata):
        reps = np.full(B_SMALL, np.nan)
        su = np.unique(strata)
        num = np.zeros(B_SMALL)
        den = np.zeros(B_SMALL)
        for s in su:
            m = strata == s
            if sel[m].sum() == 0 or (~sel[m]).sum() == 0:
                continue
            ms = m & sel
            mu = m & ~sel
            S_s = counts @ per_day(d * ms, didx, nd)
            N_s = counts @ per_day(ms.astype(float), didx, nd)
            S_u = counts @ per_day(d * mu, didx, nd)
            N_u = counts @ per_day(mu.astype(float), didx, nd)
            with np.errstate(invalid="ignore", divide="ignore"):
                term = np.where((N_s > 0) & (N_u > 0), N_s * (S_s / N_s - S_u / N_u), 0.0)
            num += term
            den += np.where((N_s > 0) & (N_u > 0), N_s, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            reps = num / den
        return [r6(np.nanquantile(reps, .025)), r6(np.nanquantile(reps, .975))]

    d = ph.d.to_numpy()
    strata_main = (ph.bJ.astype(str) + "|" + ph.tb.astype(str)).to_numpy()
    lj = np.log(ph.O_J.to_numpy())
    qbins = np.quantile(lj, np.linspace(0, 1, 13)[1:-1])
    strata_fine = (pd.Series(np.searchsorted(qbins, lj, side="right")).astype(str) + "|" + ph.tb.astype(str)).to_numpy()
    cres = {}
    for r in ["S3", "S1", "S2", "S4", "S5"]:
        sel = ph[f"J_{r}"].to_numpy()
        obs, p, _ = perm_test(d, sel, strata_main)
        obs_f, p_f, _ = perm_test(d, sel, strata_fine, seed=SEED + 1)
        cres[r] = {"D": r6(obs), "p_two_sided": r6(p), "D_boot_ci95": boot_D(d, sel, strata_main),
                   "n_sel": int(sel.sum()), "robust_fine_strata": {"D": r6(obs_f), "p_two_sided": r6(p_f)},
                   "mean_d_sel": r6(d[sel].mean()) if sel.any() else None,
                   "mean_d_unsel": r6(d[~sel].mean())}
    p3, p1 = cres["S3"]["p_two_sided"], cres["S1"]["p_two_sided"]
    order = sorted([("S3", p3), ("S1", p1)], key=lambda x: x[1])
    holm = {}
    prev = 0.0
    for i, (k, pv) in enumerate(order):
        adj = min(1.0, max(prev, (2 - i) * pv))
        holm[k] = r6(adj)
        prev = adj
    for k in holm:
        cres[k]["p_holm_S3_S1"] = holm[k]
    cres["note"] = "S2/S4/S5 reported descriptively (not in Holm)"
    out["c_selected_vs_unselected_drift"] = cres
    print("c", {k: (v["D"], v["p_two_sided"]) for k, v in cres.items() if k != "note"}, flush=True)

    # ---------------------------------------------------------------- (d) affected vs unaffected selection rate (research rows, P)
    hist = load_history()
    P = hist[(hist.race_date >= "2026-06-27") & (hist.race_date <= "2026-09-22")].copy()
    amap = dict(zip(aff.race_id, aff.affected))
    P["aff"] = P.race_id.map(amap).fillna(0).astype(int)
    dres = {}
    selP = select(P.ens_ev.to_numpy(), P.single_ev.to_numpy(), P.odds.to_numpy(), P.days_since_last.to_numpy())
    for r in ["S1", "S3", "S2", "S4", "S5"]:
        P["sel"] = selP[r]
        k_aff = int(P.sel[P.aff == 1].sum())
        k_tot = int(P.sel.sum())
        p0 = float((P.aff == 1).mean())
        bt = stats.binomtest(k_aff, k_tot, p0) if k_tot > 0 else None
        num = den = 0.0
        cmh_num = cmh_var = 0.0
        rr_num = rr_den = 0.0
        mixed = 0
        for _, g in P.groupby("race_date"):
            if g.aff.nunique() < 2:
                continue
            mixed += 1
            a_ = float(((g.aff == 1) & g.sel).sum()); b2 = float(((g.aff == 1) & ~g.sel).sum())
            c_ = float(((g.aff == 0) & g.sel).sum()); d2 = float(((g.aff == 0) & ~g.sel).sum())
            n = a_ + b2 + c_ + d2
            num += a_ * d2 / n
            den += b2 * c_ / n
            n1 = a_ + b2; n0 = c_ + d2; m1 = a_ + c_; m0 = b2 + d2
            cmh_num += a_ - n1 * m1 / n
            if n > 1:
                cmh_var += n1 * n0 * m1 * m0 / (n * n * (n - 1))
            rr_num += a_ * n0 / n
            rr_den += c_ * n1 / n
        chi = (abs(cmh_num) - 0.5) ** 2 / cmh_var if cmh_var > 0 else np.nan
        dres[r] = {"rows_aff": int((P.aff == 1).sum()), "rows_unaff": int((P.aff == 0).sum()),
                   "sel_aff": k_aff, "sel_total": k_tot, "p0_affected_row_share": r6(p0),
                   "rate_per_1000_aff": r6(1000 * k_aff / max(1, (P.aff == 1).sum())),
                   "rate_per_1000_unaff": r6(1000 * (k_tot - k_aff) / max(1, (P.aff == 0).sum())),
                   "binom_p_two_sided": r6(bt.pvalue) if bt else None,
                   "mixed_days": mixed, "MH_odds_ratio": r6(num / den) if den > 0 else None,
                   "MH_rate_ratio": r6(rr_num / rr_den) if rr_den > 0 else None,
                   "CMH_chi2_cc": r6(chi), "CMH_p": r6(stats.chi2.sf(chi, 1)) if np.isfinite(chi) else None}
    dres["note"] = "replication of R05 supplement_rates (already seen before prereg)"
    out["d_affected_rate"] = dres

    # ---------------------------------------------------------------- (e) realized on paired sample (diagnostic)
    nd_mask = ~ph.dead_heat.to_numpy().astype(bool)
    ret = ph.ret.to_numpy()
    eres = {}
    rng_q = np.random.default_rng(SEED)
    # q-resampled winners per race (same race -> same draw for all its pairs)
    race_list = sorted(ph.race_id.unique())
    qdraw = {}
    for rid in race_list:
        g = fv[fv.race_id == rid]
        q = (1.0 / g.odds.to_numpy()) / (1.0 / g.odds.to_numpy()).sum()
        qdraw[rid] = (g.horse_id.to_numpy(), rng_q.choice(len(g), size=B_SMALL, p=q))
    win_idx = {}
    for rid, (hids, dr) in qdraw.items():
        win_idx[rid] = (dict(zip(hids, range(len(hids)))), dr)
    pos_in_race = np.array([win_idx[r][0][h] for r, h in zip(ph.race_id, ph.horse_id)])
    qwin = np.vstack([win_idx[r][1] for r in ph.race_id]) == pos_in_race[:, None]  # (rows, B)
    of = ph.O_F.to_numpy()
    for r in RULES:
        eres[r] = {}
        for t in TB + ["ALL"]:
            m = (np.ones(len(ph), bool) if t == "ALL" else (ph.tb == t).to_numpy()) & nd_mask
            J = ph[f"J_{r}"].to_numpy() & m
            F = ph[f"F_{r}"].to_numpy() & m
            sets = {"J": J, "F": F, "J_not_F": J & ~F, "F_not_J": F & ~J, "parent": m}
            ent = {}
            num_rows = np.vstack([np.where(s, ret, 0.0) for s in sets.values()])
            den_rows = np.vstack([s.astype(float) for s in sets.values()])
            num = np.vstack([per_day(x, didx, nd) for x in num_rows])
            den = np.vstack([per_day(x, didx, nd) for x in den_rows])
            bb = race_block_ratio_bootstrap_ci_v1(num, den, days, b=B_ROI, seed=SEED_ROI)
            for i, (k, s) in enumerate(sets.items()):
                ent[k] = {"n": int(s.sum()), "hits": int((s & ph.won.to_numpy()).sum()),
                          "roi": r6(bb.point[i]), "ci95": [r6(bb.ci_low[i]), r6(bb.ci_high[i])],
                          "p_centered_one_sided": r6(centered_one_sided_p_from_replicates(bb.replicates[i], bb.point[i]))
                          if np.isfinite(bb.point[i]) else None}
            if J.sum() > 0:
                qroi = (qwin[J] * of[J][:, None]).sum(axis=0) / J.sum()
                ent["J_q_null"] = {"mean": r6(qroi.mean()), "q05": r6(np.quantile(qroi, .05)),
                                   "q95": r6(np.quantile(qroi, .95)),
                                   "p_ge_realized": r6((1 + np.sum(qroi >= ent["J"]["roi"] - 1e-12)) / (B_SMALL + 1))}
                if t == "ALL":
                    obs, p, _ = perm_test(ret[m], J[m], strata_main[m], seed=SEED + 2)
                    ent["J_vs_unselected_stratified_perm"] = {"diff": r6(obs), "p_two_sided": r6(p)}
            eres[r][t] = ent
    eres["year_weighted"] = "not applicable: all pairs are in 2026"
    out["e_realized_paired"] = eres

    # ---------------------------------------------------------------- (f) payout slippage
    fres = {"by_bandJ_x_tb": {}, "by_tb": {}, "by_bandJ": {}}
    w_ = ph.won.to_numpy() & nd_mask

    def slip(m):
        mm = m & nd_mask
        Hr_num = ph.O_settle.to_numpy()[w_ & mm].sum()
        Hr_den = ph.O_J.to_numpy()[w_ & mm].sum()
        Hm_num = (ph.q_F.to_numpy() * ph.O_F.to_numpy())[mm].sum()
        Hm_den = (ph.q_F.to_numpy() * ph.O_J.to_numpy())[mm].sum()
        return {"n_horses": int(mm.sum()), "n_winners": int((w_ & mm).sum()),
                "H_real": r6(Hr_num / Hr_den) if Hr_den > 0 else None,
                "H_mkt": r6(Hm_num / Hm_den) if Hm_den > 0 else None}
    for t in TB:
        fres["by_tb"][t] = slip((ph.tb == t).to_numpy())
        for b_ in OB:
            fres["by_bandJ_x_tb"][f"{b_}|{t}"] = slip(((ph.tb == t) & (ph.bJ == b_)).to_numpy())
    for b_ in OB:
        fres["by_bandJ"][b_] = slip((ph.bJ == b_).to_numpy())
    fres["pooled"] = slip(np.ones(len(ph), bool))
    for r in ["S1", "S3"]:
        fres[f"J_{r}_by_tb"] = {t: slip(((ph.tb == t) & ph[f"J_{r}"]).to_numpy()) for t in TB}
        fres[f"J_{r}_by_tb"]["ALL"] = slip(ph[f"J_{r}"].to_numpy())
    out["f_payout_slippage"] = fres

    # ---------------------------------------------------------------- (g) implied buyable ROI
    out["g_implied_buyable_roi"] = implied_buyable(hist, ph, didx, days, counts, freeze, aff)

    # ---------------------------------------------------------------- production picks (descriptive)
    out["production_prospective_picks"] = production_picks(picks, mev, fv, scorer)
    out["elapsed_s"] = round(time.time() - t0, 1)
    (OUT / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print("done", out["elapsed_s"], "s")


# ---------------------------------------------------------------------------------------------- history

def load_history() -> pd.DataFrame:
    d = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "race_date", "year", "odds", "won",
                                       "days_since_last", "race_ok", "dead_heat"])
    evs = []
    for s, tag in ENS_RUNS.items():
        v = pd.read_parquet(RES / tag / "predictions.parquet", columns=["race_id", "horse_id", "pred"])
        v = v.rename(columns={"pred": f"pred{s}"})
        d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    v = pd.read_parquet(RES / SINGLE_RUN / "predictions.parquet", columns=["race_id", "horse_id", "pred"])
    d = d.merge(v.rename(columns={"pred": "pred_single"}), on=["race_id", "horse_id"], how="left",
                validate="one_to_one")
    d = d[d.race_ok & ~d.dead_heat & (d.year >= 2010)].reset_index(drop=True)
    d["ens_ev"] = np.mean([1.0 + d[f"pred{s}"].to_numpy(float) / 100.0 for s in ENS_RUNS], axis=0)
    d["single_ev"] = 1.0 + d["pred_single"].to_numpy(float) / 100.0
    d = d.drop(columns=[f"pred{s}" for s in ENS_RUNS] + ["pred_single"])
    d["ret"] = np.where(d.won, d.odds, 0.0)
    return d


def cell_ids(ev, odds, gap):
    evb = np.searchsorted(np.asarray(EV_EDGES[1:-1]), ev, side="right")
    ob = np.searchsorted(np.asarray(OB_EDGES[1:-1]), odds, side="right")
    gf = ((gap >= 14) & (gap <= 112)).astype(int)
    return evb * 12 + ob * 2 + gf, evb * 6 + ob, evb


def implied_buyable(hist, ph, didx, days, counts, freeze, aff) -> dict:
    res: dict = {"cells": "EV bin x final odds band x gap flag; fallback (EV bin, band) then (EV bin) when n<300"}
    affected = set(aff.race_id[aff.affected == 1])
    # year-weighted and pooled S-rule ROIs on research rows (affected races excluded) for translation
    yw = {}
    hs = select(hist.ens_ev.to_numpy(), hist.single_ev.to_numpy(), hist.odds.to_numpy(),
                hist.days_since_last.to_numpy())
    keep = ~hist.race_id.isin(affected).to_numpy()
    for r in RULES:
        yw[r] = {}
        for wname, (y0, y1) in {"ALL": (2010, 2026), "C": (2019, 2026)}.items():
            m = hs[r] & keep & (hist.year >= y0).to_numpy() & (hist.year <= y1).to_numpy()
            sub = hist[m]
            by = sub.groupby("year").agg(n=("ret", "size"), s=("ret", "sum"))
            yw[r][wname] = {"pooled_excl_affected": r6(sub.ret.sum() / len(sub)) if len(sub) else None,
                            "year_weighted_excl_affected": r6((by.s / by.n).mean()) if len(by) else None,
                            "frozen_pooled": freeze["rules"][r][wname]["roi"], "n": int(len(sub))}
    res["closing_backtest_reference"] = yw

    out_models = {}
    for model in ("ens", "single"):
        evcol = "ens_ev" if model == "ens" else "single_ev"
        variants = {}
        for vname, (y0, y1, yweighted) in {"primary_2010_2025": (2010, 2025, False),
                                          "sens_2019_2025": (2019, 2025, False),
                                          "sens_2010_2025_year_weighted": (2010, 2025, True)}.items():
            h = hist[(hist.year >= y0) & (hist.year <= y1)]
            c1, c2, c3 = cell_ids(h[evcol].to_numpy(), h.odds.to_numpy(), h.days_since_last.to_numpy())
            retv = h.ret.to_numpy()
            n1 = np.bincount(c1, minlength=96); n2 = np.bincount(c2, minlength=48); n3 = np.bincount(c3, minlength=8)
            if not yweighted:
                g1 = np.bincount(c1, weights=retv, minlength=96) / np.maximum(n1, 1)
                g2 = np.bincount(c2, weights=retv, minlength=48) / np.maximum(n2, 1)
                g3 = np.bincount(c3, weights=retv, minlength=8) / np.maximum(n3, 1)
            else:
                yrs = h.year.to_numpy()

                def yw_cells(c, k):
                    acc = np.zeros(k); cnt = np.zeros(k)
                    for y in np.unique(yrs):
                        m = yrs == y
                        ny = np.bincount(c[m], minlength=k)
                        sy = np.bincount(c[m], weights=retv[m], minlength=k)
                        okk = ny >= 30
                        acc[okk] += sy[okk] / ny[okk]; cnt[okk] += 1
                    return np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan), cnt
                g1, cn1 = yw_cells(c1, 96); g2, cn2 = yw_cells(c2, 48); g3, cn3 = yw_cells(c3, 8)
                n1 = np.where(np.isfinite(g1), n1, 0); n2 = np.where(np.isfinite(g2), n2, 0)
            lvl = np.where(n1 >= 300, 1, np.where(n2[np.arange(96) // 2] >= 300, 2, 3))
            g = np.where(lvl == 1, g1, np.where(lvl == 2, g2[np.arange(96) // 2], g3[np.arange(96) // 12]))
            variants[vname] = {"g": g, "lvl": lvl, "n1": n1}
            if vname == "primary_2010_2025":
                # per-day sums for the historical bootstrap
                hdays = sorted(h.race_date.unique().tolist())
                hdi = day_index(h.race_date.to_numpy(), hdays)
                variants[vname]["boot"] = (hdays, hdi, c1, c2, c3, retv)
        out_models[model] = variants

    # paired-sample cells (final state)
    cells = {}
    for model in ("ens", "single"):
        evc = "EVe_F" if model == "ens" else "EVs_F"
        cells[model] = cell_ids(ph[evc].to_numpy(), ph.O_F.to_numpy(), ph.days_since_last.to_numpy())[0]

    # historical bootstrap replicates of g (primary)
    rng = np.random.default_rng(SEED + 7)
    g_reps = {}
    for model in ("ens", "single"):
        hdays, hdi, c1, c2, c3, retv = out_models[model]["primary_2010_2025"]["boot"]
        nh = len(hdays)
        W = rng.poisson(1.0, size=(B_SMALL, nh)).astype(float)

        def cell_day(c, k):
            M_n = np.zeros((nh, k)); M_s = np.zeros((nh, k))
            np.add.at(M_n, (hdi, c), 1.0)
            np.add.at(M_s, (hdi, c), retv)
            return W @ M_n, W @ M_s
        N1, S1 = cell_day(c1, 96); N2, S2 = cell_day(c2, 48); N3, S3 = cell_day(c3, 8)
        lvl = out_models[model]["primary_2010_2025"]["lvl"]
        with np.errstate(invalid="ignore", divide="ignore"):
            G1 = S1 / N1; G2 = S2 / N2; G3 = S3 / N3
        idx = np.arange(96)
        g_reps[model] = np.where(lvl == 1, G1, np.where(lvl == 2, G2[:, idx // 2], G3[:, idx // 12]))

    nd = len(days)
    tbs = TB + ["ALL"]
    gres = {}
    for r in RULES:
        model = "ens" if ar.definition(r).uses_ensemble else "single"
        cc = cells[model]
        gres[r] = {}
        rho_reps = {}
        for t in tbs:
            m = np.ones(len(ph), bool) if t == "ALL" else (ph.tb == t).to_numpy()
            J = ph[f"J_{r}"].to_numpy() & m
            F = ph[f"F_{r}"].to_numpy() & m
            ent = {"n_J": int(J.sum()), "n_F": int(F.sum())}
            for vname, var in out_models[model].items():
                g = var["g"]
                GJ = g[cc[J]].mean() if J.any() else np.nan
                GF = g[cc[F]].mean() if F.any() else np.nan
                ent[vname] = {"G_J": r6(GJ), "G_F": r6(GF), "rho": r6(GJ / GF) if F.any() and J.any() else None}
            # model-EV ratio (descriptive)
            evc = "EVe_F" if model == "ens" else "EVs_F"
            if J.any() and F.any():
                ent["model_EV_F_ratio_J_over_F"] = r6(ph[evc].to_numpy()[J].mean() / ph[evc].to_numpy()[F].mean())
            # joint bootstrap (primary)
            if J.any() and F.any():
                MJ = np.zeros((nd, 96)); MF = np.zeros((nd, 96))
                np.add.at(MJ, (didx[J], cc[J]), 1.0)
                np.add.at(MF, (didx[F], cc[F]), 1.0)
                cJ = counts @ MJ; cF = counts @ MF  # (B, 96)
                with np.errstate(invalid="ignore", divide="ignore"):
                    GJr = (cJ * g_reps[model]).sum(axis=1) / cJ.sum(axis=1)
                    GFr = (cF * g_reps[model]).sum(axis=1) / cF.sum(axis=1)
                    rr = GJr / GFr
                rho_reps[t] = rr
                ent["primary_2010_2025"]["G_J_ci95"] = [r6(np.nanquantile(GJr, .025)), r6(np.nanquantile(GJr, .975))]
                ent["primary_2010_2025"]["rho_ci95"] = [r6(np.nanquantile(rr, .025)), r6(np.nanquantile(rr, .975))]
                rho = ent["primary_2010_2025"]["rho"]
                ref = res["closing_backtest_reference"][r]
                ent["translated"] = {
                    w: {"frozen_pooled_x_rho": r6(ref[w]["frozen_pooled"] * rho),
                        "frozen_pooled_x_rho_ci95": [r6(ref[w]["frozen_pooled"] * np.nanquantile(rr, .025)),
                                                     r6(ref[w]["frozen_pooled"] * np.nanquantile(rr, .975))],
                        "year_weighted_excl_affected_x_rho": r6(ref[w]["year_weighted_excl_affected"] * rho)}
                    for w in ("ALL", "C")}
            gres[r][t] = ent
        if "T1" in rho_reps and "T4" in rho_reps:
            diff = rho_reps["T1"] - rho_reps["T4"]
            gres[r]["contrast_rho_T1_minus_T4"] = {
                "point": r6(gres[r]["T1"]["primary_2010_2025"]["rho"] - gres[r]["T4"]["primary_2010_2025"]["rho"]),
                "ci95": [r6(np.nanquantile(diff, .025)), r6(np.nanquantile(diff, .975))]}
    res["by_rule"] = gres
    res["g_tables"] = {m: {v: {"g": [r6(x) for x in var["g"]], "level": var["lvl"].tolist(),
                               "n_cell": [int(x) for x in var["n1"]]} for v, var in out_models[m].items()}
                       for m in out_models}
    res["cell_index"] = "cell = evb*12 + ob*2 + gapflag; evb over EV edges [0.8,0.9,1.0,1.1,1.2,1.3,1.5]; ob over [5,10,20,40,100]"
    return res


def production_picks(picks, mev, fv, scorer) -> dict:
    pp = picks[picks.result_pending_at_compute.astype(bool) & (picks.kind == "pick")].copy()
    fvi = fv.set_index(["race_id", "horse_id"])
    rows = []
    for r in pp.itertuples():
        key = (r.race_id, r.horse_id)
        has = key in fvi.index
        rows.append({"rule": r.rule_id, "race_id": r.race_id, "horse_number": int(r.horse_number),
                     "hours_to_post": r6(r.seconds_to_post / 3600.0), "odds_used": r6(float(r.odds_used)),
                     "ens_ev_judged": r6(float(r.ens_expected_return)) if r.ens_expected_return is not None else None,
                     "single_ev_judged": r6(float(r.single_expected_return)) if r.single_expected_return is not None else None,
                     "O_F": r6(fvi.loc[key, "odds"]) if has else None,
                     "ens_ev_final": r6(fvi.loc[key, "EVe_F"]) if has else None,
                     "single_ev_final": r6(fvi.loc[key, "EVs_F"]) if has else None,
                     "still_selected_at_final": bool(
                         ar.matches(ar.definition(r.rule_id), ens_ev=fvi.loc[key, "EVe_F"],
                                    single_ev=fvi.loc[key, "EVs_F"], odds=fvi.loc[key, "odds"],
                                    days_since_last=None if r.days_since_last is None else float(r.days_since_last)))
                     if has else None})
    first = mev.groupby("race_id").agg(computed_at=("computed_at", "min")).reset_index()
    return {"n_prospective_picks": len(rows), "picks": rows,
            "note": "attention_picks with result_pending_at_compute (2026-10-03/04 go-live window); O_F from final-available races only"}


if __name__ == "__main__":
    main()
