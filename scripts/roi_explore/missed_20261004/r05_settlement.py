"""R05_settlement: which races carry PRE-RACE win odds as their "settled" odds, how big the bias is,
and how S1..S5 (+137 EV>1.2 = S5) and the 138 frozen table move under three settlements.

Pre-registration: artifacts/roi_explore/missed_20261004/R05_settlement/prereg.json (sha256 in
prereg.sha256). Read-only: DB SELECT in a READ ONLY transaction, no product code edited.
Requires r05_parse_archive.py to have produced archive_payouts.jsonl / archive_horses.jsonl.

Run:  cd training && uv run python ../scripts/roi_explore/missed_20261004/r05_settlement.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import numpy as np
import pandas as pd
from horseracing_eval import attention_rules as ar
from horseracing_eval.bootstrap import centered_one_sided_p_from_replicates, race_block_ratio_bootstrap_ci_v1
from sqlalchemy import create_engine, text

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/roi_explore/missed_20261004/R05_settlement"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
RES = ROOT / "artifacts/roi_explore/results"
ENS_RUNS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
SINGLE_RUN = "armC_binary_drop-sameday+weightlive_serving_v2_2007"
FREEZE = ROOT / "specs/138-attention-conditions/evidence/rules_S1_S5_freeze.json"
DB_URL = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
P_LO, P_HI = "2026-06-27", "2026-09-22"
WINDOWS = {"P": ("date", P_LO, P_HI), "Y2026": ("date", "2026-01-01", P_HI),
           "C": ("year", 2019, 2026), "ALL": ("year", 2010, 2026)}
BOOT_B, BOOT_SEED = 20000, 20260905
JOB_LOG_START = pd.Timestamp("2026-06-23", tz="UTC")


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def r6(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), 6)


# ---------------------------------------------------------------------------------------------
# DB (read-only)
# ---------------------------------------------------------------------------------------------
def load_db() -> dict[str, pd.DataFrame]:
    e = create_engine(DB_URL, hide_parameters=True)
    q = {
        "races": "select race_id, race_date, post_time, track_type from races where race_date >= '2025-10-01'",
        "horses": ("select rh.race_id, rh.horse_id, rh.horse_number, rh.odds, rh.popularity, rh.entry_status "
                   "from race_horses rh join races r using(race_id) where r.race_date >= '2025-10-01'"),
        "results": ("select rr.race_id, rr.horse_id, rr.finish_order, rr.result_status, rr.created_at "
                    "from race_results rr join races r using(race_id) where r.race_date >= '2025-10-01'"),
        "jobs": ("select job_type, scope_value, status, started_at, completed_at, summary, error_message "
                 "from ingestion_jobs where source='netkeiba' and job_type in ('odds','results')"),
    }
    out = {}
    with e.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        for k, s in q.items():
            out[k] = pd.read_sql(text(s), c)
    e.dispose()
    return out


# ---------------------------------------------------------------------------------------------
# Step 1: classification (outcome-free) + archive ground truth
# ---------------------------------------------------------------------------------------------
def classify(db) -> pd.DataFrame:
    races = db["races"].copy()
    hs = db["horses"]
    started = hs[hs.entry_status == "started"]
    res = db["results"]
    t_res = res.groupby("race_id").created_at.min().rename("t_res")
    races = races.merge(t_res, on="race_id", how="left")
    races = races.merge(started.groupby("race_id").size().rename("n_started"), on="race_id", how="left")
    jobs = db["jobs"].copy()
    jobs["race_id"] = jobs.scope_value.str.extract(r"(\d{12})", expand=False)
    jobs["written"] = jobs.summary.apply(lambda s: s.get("written") if isinstance(s, dict) else None)
    o = jobs[(jobs.job_type == "odds") & jobs.status.isin(["succeeded", "partial"])]
    o = o.merge(races[["race_id", "t_res", "post_time"]], on="race_id", how="inner")
    before = o[o.t_res.isna() | (o.started_at < o.t_res)]
    after = o[o.t_res.notna() & (o.started_at >= o.t_res)]
    last_before = before.groupby("race_id").agg(last_pre_res_odds_job_at=("started_at", "max"),
                                                n_odds_jobs_before_results=("started_at", "size"),
                                                last_pre_res_written=("written", "last"))
    # written>0 refinement (reported, the prereg rule uses status only)
    bw = before[before.written.fillna(0) > 0].groupby("race_id").started_at.max().rename("last_pre_res_write_at")
    n_after = after.groupby("race_id").size().rename("n_odds_jobs_after_results")
    races = races.merge(last_before, on="race_id", how="left").merge(bw, on="race_id", how="left")
    races = races.merge(n_after, on="race_id", how="left")
    races["n_odds_jobs_before_results"] = races.n_odds_jobs_before_results.fillna(0).astype(int)
    races["n_odds_jobs_after_results"] = races.n_odds_jobs_after_results.fillna(0).astype(int)

    def cls(r):
        if pd.isna(r.t_res):
            return "NO_RESULTS"
        if r.n_odds_jobs_before_results > 0:
            if pd.isna(r.post_time):
                return "POST_RACE_BEFORE_RESULTS"  # cannot order -> uncertain
            return "PRE_RACE_SNAPSHOT" if r.last_pre_res_odds_job_at < r.post_time else "POST_RACE_BEFORE_RESULTS"
        if r.n_odds_jobs_after_results > 0:
            return "FILLED_AFTER_RESULTS"
        return "NO_JOB_LOG"

    races["timing_class"] = races.apply(cls, axis=1)
    races["staleness_min"] = np.where(
        races.timing_class == "PRE_RACE_SNAPSHOT",
        (races.post_time - races.last_pre_res_odds_job_at).dt.total_seconds() / 60.0, np.nan)
    races["timing_affected"] = races.timing_class.map({
        "PRE_RACE_SNAPSHOT": 1, "POST_RACE_BEFORE_RESULTS": 1, "FILLED_AFTER_RESULTS": 0,
        "NO_JOB_LOG": 0, "NO_RESULTS": 0})
    races["timing_uncertain"] = races.timing_class.eq("POST_RACE_BEFORE_RESULTS")
    return races


def archive_truth(db, races) -> tuple[pd.DataFrame, pd.DataFrame]:
    pay = pd.read_json(OUT / "archive_payouts.jsonl", lines=True, dtype={"race_id": str})
    ah = pd.read_json(OUT / "archive_horses.jsonl", lines=True, dtype={"race_id": str})
    ok = pay[pay.status == "ok"].sort_values(["race_id", "version"]).groupby("race_id").tail(1)
    tansho = ok.set_index("race_id").tansho.to_dict()
    hs = db["horses"]
    started = hs[hs.entry_status == "started"][["race_id", "horse_id", "horse_number", "odds"]].copy()
    res = db["results"][["race_id", "horse_id", "finish_order", "result_status"]]
    started = started.merge(res, on=["race_id", "horse_id"], how="left")
    started["db_won"] = (started.finish_order == 1) & (started.result_status == "finished")
    arch_races = set(pay.race_id)
    sa = started[started.race_id.isin(arch_races)].merge(
        ah[["race_id", "horse_number", "result_page_odds", "popularity_page", "finish_text"]],
        on=["race_id", "horse_number"], how="left")
    sa["odds"] = sa.odds.astype(float)
    sa["page_missing"] = sa.result_page_odds.isna()
    sa["differs"] = (~sa.page_missing) & sa.odds.notna() & ((sa.odds - sa.result_page_odds).abs() > 1e-9)

    def pay_for(row):
        t = tansho.get(row.race_id)
        if t is None:
            return np.nan
        for num, yen in t:
            if num == row.horse_number:
                return float(yen)
        return 0.0

    sa["official_payout_yen"] = sa.apply(pay_for, axis=1)
    g = sa.groupby("race_id").agg(n_started_arch=("horse_number", "size"), n_page_missing=("page_missing", "sum"),
                                  n_horses_odds_differ=("differs", "sum"))
    g["archive_tansho_ok"] = g.index.isin(set(tansho))
    # identity: DB winners == tansho horse numbers
    win_db = sa[sa.db_won].groupby("race_id").horse_number.apply(lambda s: sorted(s.tolist()))
    win_ar = {k: sorted(x[0] for x in v) for k, v in tansho.items()}
    g["winner_identity_ok"] = [
        (r in win_ar and r in win_db.index and win_db[r] == win_ar[r]) for r in g.index]
    g["gt_all_horse_match"] = np.where(g.n_page_missing == 0, g.n_horses_odds_differ == 0, np.nan)
    g = g.reset_index()
    g["archive_available"] = True
    return g, sa


# ---------------------------------------------------------------------------------------------
# Step 3: ROI under settlements
# ---------------------------------------------------------------------------------------------
def ci_and_p(pay: np.ndarray, days: np.ndarray, sel: np.ndarray, stake: np.ndarray | None = None) -> dict:
    idx = np.flatnonzero(sel)
    if idx.size == 0:
        return {"roi": None, "ci95": [None, None], "p_one_sided": None, "n_days": 0}
    frame = pd.DataFrame({"d": days[idx], "pay": pay[idx]})
    g = frame.groupby("d", sort=True)["pay"].agg(["sum", "size"])
    if len(g) < 2:
        return {"roi": r6(g["sum"].sum() / (100 * g["size"].sum())), "ci95": [None, None], "p_one_sided": None,
                "n_days": int(len(g))}
    res = race_block_ratio_bootstrap_ci_v1(g["sum"].to_numpy(float), 100.0 * g["size"].to_numpy(float),
                                           list(g.index), block="race_day", b=BOOT_B, seed=BOOT_SEED)
    point = float(res.point[0])
    p = centered_one_sided_p_from_replicates(res.replicates[0], point)
    return {"roi": r6(point), "ci95": [r6(res.ci_low[0]), r6(res.ci_high[0])], "p_one_sided": r6(p),
            "n_days": int(len(g))}


def main() -> None:
    prereg = OUT / "prereg.json"
    prereg_sha = sha(prereg)
    db = load_db()
    races = classify(db)
    gt, sa = archive_truth(db, races)
    races = races.merge(gt, on="race_id", how="left")
    races["archive_available"] = races.archive_available.fillna(False).astype(bool)
    races["archive_tansho_ok"] = races.archive_tansho_ok.fillna(False).astype(bool)

    def final_flag(r):
        if r.archive_available and pd.notna(r.gt_all_horse_match):
            return (0 if bool(r.gt_all_horse_match) else 1), "archive"
        return int(r.timing_affected), "timing"

    ff = races.apply(final_flag, axis=1, result_type="expand")
    races["affected"], races["affected_source"] = ff[0].astype(int), ff[1]
    races["correctable"] = (races.affected == 1) & races.archive_tansho_ok

    # winner stored odds / official payout per race (archived races)
    w = sa[sa.db_won].groupby("race_id").agg(winner_horse_numbers=("horse_number", lambda s: ",".join(map(str, sorted(s)))),
                                             stored_winner_odds=("odds", "first"),
                                             official_win_payout_yen=("official_payout_yen", "first"),
                                             n_winners=("horse_number", "size"))
    races = races.merge(w, on="race_id", how="left")
    races["payout_ratio"] = races.official_win_payout_yen / (100 * races.stored_winner_odds)

    # ---------------- confusion & bias ----------------
    arch = races[races.archive_available & races.gt_all_horse_match.notna()]
    confusion = pd.crosstab(arch.timing_class, arch.gt_all_horse_match.map({1.0: "gt_final", 0.0: "gt_differs", True: "gt_final", False: "gt_differs"}))
    cls_by_date = races.assign(d=races.race_date.astype(str)).groupby(["d", "timing_class"]).size().unstack(fill_value=0)

    single = arch[(arch.n_winners == 1) & arch.winner_identity_ok]
    aff_w = single[single.affected == 1]
    un_w = single[single.affected == 0]

    def ratio_stats(df):
        r = df.payout_ratio.dropna()
        if r.empty:
            return {"n": 0}
        return {"n": int(len(r)), "mean_ratio": r6(r.mean()), "median_ratio": r6(r.median()),
                "share_lt1": r6((r < 1 - 1e-9).mean()), "share_eq1": r6((abs(r - 1) <= 1e-9).mean()),
                "share_gt1": r6((r > 1 + 1e-9).mean()),
                "aggregate_official_over_stored": r6(df.official_win_payout_yen.sum() / (100 * df.stored_winner_odds.sum())),
                "mean_stored_over_official_minus1": r6((1 / r).mean() - 1),
                "p10": r6(r.quantile(.1)), "p90": r6(r.quantile(.9))}

    bins = [0, 30, 120, 360, np.inf]
    labels = ["<30min", "30-120min", "2-6h", ">=6h"]
    aff_w = aff_w.assign(stale_bin=pd.cut(aff_w.staleness_min, bins, labels=labels, right=False))
    b4 = {str(k): ratio_stats(v) for k, v in aff_w.groupby("stale_bin", observed=False)}
    # B2: all horses log(final/stored) by stored odds band
    sa2 = sa.merge(races[["race_id", "affected", "gt_all_horse_match"]], on="race_id", how="left")
    sa2 = sa2[sa2.result_page_odds.notna() & sa2.odds.notna() & sa2.gt_all_horse_match.notna()]
    sa2["lr"] = np.log(sa2.result_page_odds / sa2.odds)
    obands = [1, 5, 10, 20, 40, 100, np.inf]
    sa2["band"] = pd.cut(sa2.odds, obands, right=False)
    b2 = {}
    for flag, gdf in sa2.groupby("affected"):
        b2[f"affected={flag}"] = {str(k): {"n": int(len(v)), "mean_log_final_over_stored": r6(v.lr.mean()),
                                           "share_differ": r6((v.lr.abs() > 1e-12).mean()),
                                           "share_final_lower": r6((v.lr < -1e-12).mean())}
                                  for k, v in gdf.groupby("band", observed=False)}
    # winners vs non-winners within affected (late money direction)
    b2w = {}
    for won_, v in sa2[sa2.affected == 1].groupby("db_won"):
        b2w[f"db_won={bool(won_)}"] = {"n": int(len(v)), "mean_log_final_over_stored": r6(v.lr.mean()),
                                       "share_final_lower": r6((v.lr < -1e-12).mean())}

    staleness = races[races.timing_class == "PRE_RACE_SNAPSHOT"].staleness_min.describe(percentiles=[.1, .25, .5, .75, .9]).to_dict()

    # ---------------- rows + predictions ----------------
    raw = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won",
                                         "days_since_last", "race_ok", "dead_heat"])
    d = raw
    for s, tag in ENS_RUNS.items():
        v = pd.read_parquet(RES / tag / "predictions.parquet")[["race_id", "horse_id", "pred"]].rename(columns={"pred": f"pred{s}"})
        d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    v = pd.read_parquet(RES / SINGLE_RUN / "predictions.parquet")[["race_id", "horse_id", "pred"]].rename(columns={"pred": "pred_single"})
    d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    d = d[d.race_ok & ~d.dead_heat & (d.year >= 2010)].reset_index(drop=True)
    assert not d[[f"pred{s}" for s in ENS_RUNS] + ["pred_single"]].isna().any(axis=1).any()

    # consistency of stored odds in rows vs DB (P-window and later, non-outcome)
    dbodds = db["horses"][["race_id", "horse_id", "odds"]].rename(columns={"odds": "db_odds"})
    chk = d[d.race_date >= "2025-10-01"][["race_id", "horse_id", "odds"]].merge(dbodds, on=["race_id", "horse_id"], how="left")
    rows_vs_db_mismatch = int(((chk.odds - chk.db_odds.astype(float)).abs() > 1e-9).sum())
    rows_vs_db_missing = int(chk.db_odds.isna().sum())

    d = d.merge(races[["race_id", "affected", "timing_class", "archive_tansho_ok", "correctable"]], on="race_id", how="left")
    d["affected"] = d.affected.fillna(0).astype(int)   # pre-2025-10 races: JRA-VAN final odds
    d["archive_tansho_ok"] = d.archive_tansho_ok.fillna(False).astype(bool)
    d = d.merge(sa[["race_id", "horse_number", "result_page_odds", "official_payout_yen"]],
                on=["race_id", "horse_number"], how="left")

    years = d.year.to_numpy()
    dates = d.race_date.astype(str).str.slice(0, 10).to_numpy()
    odds = d.odds.to_numpy(float)
    won = d.won.to_numpy(bool)
    dsl = d.days_since_last.to_numpy(float)
    ens_ev = np.mean([1.0 + d[f"pred{s}"].to_numpy(float) / 100.0 for s in ENS_RUNS], axis=0)
    single_ev = 1.0 + d["pred_single"].to_numpy(float) / 100.0
    aff = d.affected.to_numpy(int) == 1
    arch_ok = d.archive_tansho_ok.to_numpy(bool)
    off = d.official_payout_yen.to_numpy(float)
    fin = d.result_page_odds.to_numpy(float)

    # winner identity check for corrected settlement: DB won <=> official payout > 0 on correctable rows
    corr_rows = aff & arch_ok
    ident_bad = int(((off[corr_rows] > 0) != won[corr_rows]).sum())

    pay_A = won * odds * 100.0
    keep_A = np.ones(len(d), bool)
    pay_B = np.where(aff & arch_ok, np.nan_to_num(off), pay_A)
    keep_B = ~(aff & ~arch_ok)
    keep_C = ~aff
    # D: aggregate ratio from B1 (affected archived single-winner winners)
    R = float(aff_w.official_win_payout_yen.sum() / (100 * aff_w.stored_winner_odds.sum()))
    pay_D = np.where(aff & arch_ok, np.nan_to_num(off), np.where(aff, pay_A * R, pay_A))
    keep_D = keep_A
    # E: closing-consistent selection on archived affected races (p_hat fixed), others in affected set dropped
    odds_E = np.where(aff & arch_ok & np.isfinite(fin), fin, odds)
    ens_E = ens_ev / odds * odds_E
    single_E = single_ev / odds * odds_E
    keep_E = ~(aff & ~(arch_ok & np.isfinite(fin)))
    pay_E = pay_B

    settlements = {"A_stored": (pay_A, keep_A, False), "B_corrected": (pay_B, keep_B, False),
                   "C_excluded": (pay_A, keep_C, False), "D_imputed": (pay_D, keep_D, False),
                   "E_closing_consistent": (pay_E, keep_E, True)}

    def wmask(spec):
        kind, lo, hi = spec
        if kind == "year":
            return (years >= lo) & (years <= hi)
        return (dates >= lo) & (dates <= hi)

    rule_ids = list(ar.RULE_IDS) + ["ALL_HORSES"]
    sel_base = {rid: ar.match_mask(ar.definition(rid), ens_ev=ens_ev, single_ev=single_ev, odds=odds, days_since_last=dsl)
                for rid in ar.RULE_IDS}
    sel_base["ALL_HORSES"] = np.ones(len(d), bool)
    sel_E = {rid: ar.match_mask(ar.definition(rid), ens_ev=ens_E, single_ev=single_E, odds=odds_E, days_since_last=dsl)
             for rid in ar.RULE_IDS}
    sel_E["ALL_HORSES"] = np.ones(len(d), bool)

    def stats(sel, pay, wn):
        b = ci_and_p(pay, dates, sel)
        n = int(sel.sum())
        out = {"n": n, "hits": int((sel & (pay > 0)).sum()), "roi": b["roi"], "ci95": b["ci95"],
               "p_one_sided": b["p_one_sided"], "n_days": b["n_days"],
               "payout_yen": r6(pay[sel].sum())}
        if WINDOWS[wn][0] == "year":
            yr = []
            for y in range(WINDOWS[wn][1], WINDOWS[wn][2] + 1):
                m = sel & (years == y)
                if m.sum():
                    yr.append(pay[m].sum() / (100 * m.sum()))
            out["year_weighted_roi"] = r6(np.mean(yr)) if yr else None
            out["n_years"] = len(yr)
        return out

    roi = {}
    for rid in rule_ids:
        roi[rid] = {}
        for sname, (pay, keep, use_e) in settlements.items():
            sel0 = sel_E[rid] if use_e else sel_base[rid]
            roi[rid][sname] = {}
            for wn, spec in WINDOWS.items():
                if rid == "ALL_HORSES" and wn in ("C", "ALL"):
                    # pooled only (bootstrap over ~0.8M rows is unnecessary for the control)
                    sel = sel0 & keep & wmask(spec)
                    n = int(sel.sum())
                    roi[rid][sname][wn] = {"n": n, "hits": int((sel & (pay > 0)).sum()),
                                           "roi": r6(pay[sel].sum() / (100 * n)) if n else None}
                    continue
                roi[rid][sname][wn] = stats(sel0 & keep & wmask(spec), pay, wn)

    # picks inside P falling on affected races
    inP = wmask(WINDOWS["P"])
    concentration = {}
    for rid in rule_ids:
        s = sel_base[rid] & inP
        concentration[rid] = {"picks_P": int(s.sum()), "picks_P_affected": int((s & aff).sum()),
                              "picks_P_affected_correctable": int((s & aff & arch_ok).sum()),
                              "races_P": int(pd.Series(d.race_id[s]).nunique()),
                              "share_affected": r6((s & aff).sum() / max(1, s.sum()))}
    racesP = d[inP].groupby("race_id").agg(aff=("affected", "first"), ok=("archive_tansho_ok", "first"))
    p_population = {"races": int(len(racesP)), "affected": int(racesP.aff.sum()),
                    "affected_correctable": int((racesP.aff.astype(bool) & racesP.ok).sum()),
                    "affected_not_correctable": int((racesP.aff.astype(bool) & ~racesP.ok).sum()),
                    "unaffected": int((racesP.aff == 0).sum())}

    # freeze reproduction
    fz = json.loads(FREEZE.read_text())
    repro = {}
    for rid in ar.RULE_IDS:
        for wn in ("ALL", "C"):
            a = roi[rid]["A_stored"][wn]
            f = fz["rules"][rid][wn]
            repro[f"{rid}_{wn}"] = {"n": [a["n"], f["n"]], "hits": [a["hits"], f["hits"]], "roi": [a["roi"], f["roi"]],
                                    "ci95": [a["ci95"], f["ci95"]], "p": [a["p_one_sided"], f["p_one_sided"]],
                                    "match": a["n"] == f["n"] and a["hits"] == f["hits"] and a["roi"] == f["roi"]
                                    and a["ci95"] == f["ci95"] and a["p_one_sided"] == f["p_one_sided"]}

    # materiality (prereg decision rule)
    material = {}
    for rid in ar.RULE_IDS:
        A = roi[rid]["A_stored"]
        out = {}
        for alt in ("B_corrected", "C_excluded"):
            X = roi[rid][alt]
            dA = X["ALL"]["roi"] - A["ALL"]["roi"]
            dC = X["C"]["roi"] - A["C"]["roi"]
            cross = any(((A[w]["roi"] - 1) * (X[w]["roi"] - 1) < 0) or
                        ((A[w]["ci95"][0] - 1) * (X[w]["ci95"][0] - 1) < 0) for w in ("ALL", "C"))
            out[alt] = {"delta_ALL": r6(dA), "delta_C": r6(dC),
                        "delta_Y2026": r6((X["Y2026"]["roi"] or 0) - (A["Y2026"]["roi"] or 0)),
                        "delta_P": r6((X["P"]["roi"] or 0) - (A["P"]["roi"] or 0)) if X["P"]["roi"] is not None else None,
                        "crosses_1": bool(cross),
                        "material": bool(abs(dA) >= 0.01 or abs(dC) >= 0.02 or cross)}
        material[rid] = out

    # ---------------- outputs ----------------
    rows_races = set(d.race_id)
    races["in_rows_2007_eval_population"] = races.race_id.isin(rows_races)
    races["in_P"] = races.race_date.astype(str).between(P_LO, P_HI)
    races["post_time_jst"] = races.post_time.dt.tz_convert("Asia/Tokyo").dt.strftime("%Y-%m-%d %H:%M")
    races["last_pre_res_odds_job_at_jst"] = races.last_pre_res_odds_job_at.dt.tz_convert("Asia/Tokyo").dt.strftime("%Y-%m-%d %H:%M:%S")
    races["t_res_jst"] = races.t_res.dt.tz_convert("Asia/Tokyo").dt.strftime("%Y-%m-%d %H:%M:%S")
    cols = ["race_id", "race_date", "post_time_jst", "track_type", "n_started", "timing_class", "timing_affected",
            "timing_uncertain", "last_pre_res_odds_job_at_jst", "staleness_min", "n_odds_jobs_before_results",
            "n_odds_jobs_after_results", "t_res_jst", "archive_available", "archive_tansho_ok", "gt_all_horse_match",
            "n_horses_odds_differ", "winner_identity_ok", "affected", "affected_source", "correctable",
            "winner_horse_numbers", "n_winners", "stored_winner_odds", "official_win_payout_yen", "payout_ratio",
            "in_rows_2007_eval_population", "in_P"]
    races.sort_values("race_id")[cols].to_csv(OUT / "affected_races.csv", index=False)
    sa_out = sa.merge(races[["race_id", "race_date", "affected"]], on="race_id", how="left")
    sa_out[["race_id", "race_date", "horse_number", "horse_id", "odds", "result_page_odds", "popularity_page",
            "differs", "db_won", "official_payout_yen", "affected"]].rename(columns={"odds": "stored_odds"}).sort_values(
        ["race_id", "horse_number"]).to_csv(OUT / "archived_final_odds.csv", index=False)

    summary = {
        "test_id": "R05_settlement", "prereg_sha256": prereg_sha,
        "inputs": {"rows_2007_sha256": sha(ROWS), "freeze_sha256": sha(FREEZE),
                   "archive_payouts_sha256": sha(OUT / "archive_payouts.jsonl"),
                   "archive_horses_sha256": sha(OUT / "archive_horses.jsonl"),
                   "script_sha256": sha(pathlib.Path(__file__))},
        "timing_class_counts_all": races.timing_class.value_counts().to_dict(),
        "timing_class_counts_P": races[races.in_P].timing_class.value_counts().to_dict(),
        "timing_class_by_date": {k: {c: int(v) for c, v in row.items() if v} for k, row in cls_by_date.iterrows()},
        "affected_counts": {"all_dates": int(races.affected.sum()),
                            "P": int(races[races.in_P].affected.sum()),
                            "outside_P": int(races[~races.in_P].affected.sum()),
                            "outside_P_dates": sorted(races[(~races.in_P) & (races.affected == 1)].race_date.astype(str).unique().tolist()),
                            "P_correctable": int(races[races.in_P & races.correctable].shape[0]),
                            "all_correctable": int(races.correctable.sum()),
                            "first_affected_date": str(races[races.affected == 1].race_date.min()),
                            "last_affected_date": str(races[races.affected == 1].race_date.max())},
        "written_refinement_disagreements": int(((races.timing_class == "PRE_RACE_SNAPSHOT") &
                                                 (races.last_pre_res_write_at.isna() |
                                                  (races.last_pre_res_write_at >= races.post_time))).sum()),
        "archive": {"races_with_archive": int(races.archive_available.sum()),
                    "races_with_tansho": int(races.archive_tansho_ok.sum()),
                    "winner_identity_failures": int((races.archive_tansho_ok & ~races.winner_identity_ok.fillna(False).astype(bool)).sum()),
                    "confusion_timing_vs_gt": {str(i): {str(c): int(v) for c, v in row.items()} for i, row in confusion.iterrows()}},
        "staleness_min_pre_race": {k: r6(v) for k, v in staleness.items()},
        "bias": {"B1_affected_winners": ratio_stats(aff_w), "B3_unaffected_winners": ratio_stats(un_w),
                 "B4_by_staleness": b4, "B2_all_horses_by_band": b2, "B2_winners_vs_losers_affected": b2w,
                 "imputation_ratio_R": r6(R)},
        "rows_vs_db_odds": {"mismatch": rows_vs_db_mismatch, "missing_in_db": rows_vs_db_missing},
        "corrected_identity_failures": ident_bad,
        "P_population_eval": p_population,
        "pick_concentration_P": concentration,
        "roi": roi, "freeze_reproduction": repro, "materiality": material,
    }
    (OUT / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    # console
    print(json.dumps({k: summary[k] for k in ("timing_class_counts_P", "affected_counts", "archive",
                                              "written_refinement_disagreements", "rows_vs_db_odds",
                                              "corrected_identity_failures", "P_population_eval")},
                     ensure_ascii=False, indent=1, default=str))
    print(json.dumps(summary["bias"]["B1_affected_winners"], ensure_ascii=False))
    print(json.dumps(summary["bias"]["B3_unaffected_winners"], ensure_ascii=False))
    for rid in rule_ids:
        for wn in WINDOWS:
            print(rid, wn, " | ".join(
                f"{s[:1]}: n={roi[rid][s][wn]['n']} h={roi[rid][s][wn]['hits']} roi={roi[rid][s][wn]['roi']}"
                for s in settlements))
    print("repro all match:", all(v["match"] for v in repro.values()))
    print(json.dumps(material, ensure_ascii=False))


if __name__ == "__main__":
    main()
