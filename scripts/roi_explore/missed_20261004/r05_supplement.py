"""R05_settlement supplement (descriptive breakdowns added after the main run; see report 'deviations').

1. Decomposition of B_corrected vs A_stored into (a) population change (exclusion of affected races
   without archive) and (b) pure payout correction on the same picks: A_onB = stored payout on B's
   population.
2. Affected / correctable counts per race date.
3. Every S1..S5 hit inside P with stored odds vs official payout.
4. Sensitivity: the single 2025-10-11 'uncertain' race (odds fetched 8.5 months after the race)
   treated as unaffected.
5. Why E (p_hat held fixed, final odds) is not interpretable: stored vs final odds and EV' for S3 picks.

Inputs: outputs of r05_settlement.py (affected_races.csv, archived_final_odds.csv) + rows/preds.
Run:  cd training && uv run python ../scripts/roi_explore/missed_20261004/r05_supplement.py
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd
from horseracing_eval import attention_rules as ar

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/roi_explore/missed_20261004/R05_settlement"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
RES = ROOT / "artifacts/roi_explore/results"
ENS_RUNS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
SINGLE_RUN = "armC_binary_drop-sameday+weightlive_serving_v2_2007"
P_LO, P_HI = "2026-06-27", "2026-09-22"


def r6(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 6)


def main() -> None:
    races = pd.read_csv(OUT / "affected_races.csv", dtype={"race_id": str})
    fo = pd.read_csv(OUT / "archived_final_odds.csv", dtype={"race_id": str})
    raw = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won",
                                         "days_since_last", "race_ok", "dead_heat"])
    d = raw
    for s, tag in ENS_RUNS.items():
        v = pd.read_parquet(RES / tag / "predictions.parquet")[["race_id", "horse_id", "pred"]].rename(columns={"pred": f"pred{s}"})
        d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    v = pd.read_parquet(RES / SINGLE_RUN / "predictions.parquet")[["race_id", "horse_id", "pred"]].rename(columns={"pred": "pred_single"})
    d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    d = d[d.race_ok & ~d.dead_heat & (d.year >= 2010)].reset_index(drop=True)
    d = d.merge(races[["race_id", "affected", "archive_tansho_ok", "timing_class"]], on="race_id", how="left")
    d["affected"] = d.affected.fillna(0).astype(int)
    d["archive_tansho_ok"] = d.archive_tansho_ok.fillna(False).astype(bool)
    d = d.merge(fo[["race_id", "horse_number", "result_page_odds", "official_payout_yen"]],
                on=["race_id", "horse_number"], how="left")
    years = d.year.to_numpy()
    dates = d.race_date.astype(str).str.slice(0, 10).to_numpy()
    odds = d.odds.to_numpy(float)
    won = d.won.to_numpy(bool)
    dsl = d.days_since_last.to_numpy(float)
    ens_ev = np.mean([1.0 + d[f"pred{s}"].to_numpy(float) / 100.0 for s in ENS_RUNS], axis=0)
    single_ev = 1.0 + d["pred_single"].to_numpy(float) / 100.0
    aff = d.affected.to_numpy(int) == 1
    arch = d.archive_tansho_ok.to_numpy(bool)
    off = d.official_payout_yen.to_numpy(float)
    pay_A = won * odds * 100.0
    pay_B = np.where(aff & arch, np.nan_to_num(off), pay_A)
    keep_B = ~(aff & ~arch)
    windows = {"P": (dates >= P_LO) & (dates <= P_HI), "Y2026": (dates >= "2026-01-01") & (dates <= P_HI),
               "C": (years >= 2019) & (years <= 2026), "ALL": (years >= 2010) & (years <= 2026)}
    sel = {rid: ar.match_mask(ar.definition(rid), ens_ev=ens_ev, single_ev=single_ev, odds=odds, days_since_last=dsl)
           for rid in ar.RULE_IDS}
    sel["ALL_HORSES"] = np.ones(len(d), bool)

    def roi(pay, m):
        n = int(m.sum())
        return {"n": n, "hits": int((m & won).sum()), "roi": r6(pay[m].sum() / (100 * n)) if n else None,
                "payout_yen": r6(pay[m].sum())}

    decomp = {}
    for rid, s in sel.items():
        decomp[rid] = {}
        for wn, w in windows.items():
            a = roi(pay_A, s & w)
            a_onb = roi(pay_A, s & w & keep_B)
            b = roi(pay_B, s & w & keep_B)
            # same picks: correctable affected picks only
            cm = s & w & aff & arch
            decomp[rid][wn] = {"A_stored": a, "A_stored_on_B_population": a_onb, "B_corrected": b,
                               "population_effect": r6((a_onb["roi"] or 0) - (a["roi"] or 0)) if a["roi"] is not None and a_onb["roi"] is not None else None,
                               "payout_correction_effect": r6((b["roi"] or 0) - (a_onb["roi"] or 0)) if b["roi"] is not None and a_onb["roi"] is not None else None,
                               "correctable_affected_picks": int(cm.sum()),
                               "correctable_affected_stored_payout": r6(pay_A[cm].sum()),
                               "correctable_affected_official_payout": r6(pay_B[cm].sum())}

    # hits inside P
    hits = []
    inP = windows["P"]
    for rid in ar.RULE_IDS:
        m = sel[rid] & inP & won
        for i in np.flatnonzero(m):
            hits.append({"rule": rid, "race_id": d.race_id[i], "race_date": str(d.race_date[i]),
                         "horse_number": int(d.horse_number[i]), "affected": int(aff[i]), "archive": bool(arch[i]),
                         "stored_odds": float(odds[i]), "official_payout_yen": None if not np.isfinite(off[i]) else float(off[i]),
                         "timing_class": d.timing_class[i]})
    # per date
    rd = races[races.race_date.between("2026-06-20", "2026-10-04")]
    per_date = rd.groupby("race_date").agg(races=("race_id", "size"), affected=("affected", "sum"),
                                           correctable=("correctable", "sum"),
                                           pre_race=("timing_class", lambda s: int((s == "PRE_RACE_SNAPSHOT").sum())),
                                           filled_after=("timing_class", lambda s: int((s == "FILLED_AFTER_RESULTS").sum())),
                                           archive=("archive_available", "sum"),
                                           median_staleness_min=("staleness_min", "median")).reset_index()
    # sensitivity 4: 2025-10-11 uncertain race treated unaffected
    unc = races[(races.timing_class == "POST_RACE_BEFORE_RESULTS") & (races.race_date < "2026-01-01")].race_id.tolist()
    m_unc = d.race_id.isin(unc).to_numpy()
    sens4 = {"race_ids": unc, "eval_rows": int(m_unc.sum()),
             "picks_by_rule": {rid: int((sel[rid] & m_unc).sum()) for rid in ar.RULE_IDS}}
    # 5: E artifact
    fin = d.result_page_odds.to_numpy(float)
    cm = aff & arch & np.isfinite(fin) & inP
    s3 = sel["S3"]
    ensE = ens_ev / odds * np.where(cm, fin, odds)
    s3E = (ensE > 1.2) & cm
    e_art = {"rows_correctable_P": int(cm.sum()),
             "S3_picks_stored": int((s3 & cm).sum()), "S3_picks_E": int(s3E.sum()),
             "median_final_over_stored_S3E_picks": r6(np.median(fin[s3E] / odds[s3E])) if s3E.any() else None,
             "median_stored_odds_S3E_picks": r6(np.median(odds[s3E])) if s3E.any() else None,
             "median_final_odds_S3E_picks": r6(np.median(fin[s3E])) if s3E.any() else None}
    out = {"decomposition": decomp, "hits_in_P": hits, "per_date": per_date.to_dict(orient="records"),
           "sensitivity_uncertain_2025": sens4, "E_artifact": e_art}
    (OUT / "supplement.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    for rid in sel:
        for wn in windows:
            x = decomp[rid][wn]
            print(rid, wn, "A", x["A_stored"]["roi"], "A_onB", x["A_stored_on_B_population"]["roi"], "B", x["B_corrected"]["roi"],
                  "pop", x["population_effect"], "pay", x["payout_correction_effect"],
                  "corr_picks", x["correctable_affected_picks"], x["correctable_affected_stored_payout"], "->",
                  x["correctable_affected_official_payout"])
    print(pd.DataFrame(hits).to_string())
    print(per_date.to_string())
    print(sens4)
    print(e_art)


if __name__ == "__main__":
    main()
