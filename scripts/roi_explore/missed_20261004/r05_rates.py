"""R05 supplement 2 (descriptive): pick rates per eval row in affected vs unaffected races inside P,
and winner payout ratio (official / stored) by stored-odds band on archived affected races.
Run: cd training && uv run python ../scripts/roi_explore/missed_20261004/r05_rates.py"""
import json, pathlib
import numpy as np, pandas as pd
from horseracing_eval import attention_rules as ar
ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/roi_explore/missed_20261004/R05_settlement"
RES = ROOT / "artifacts/roi_explore/results"
ENS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
SINGLE = "armC_binary_drop-sameday+weightlive_serving_v2_2007"
races = pd.read_csv(OUT / "affected_races.csv", dtype={"race_id": str})
fo = pd.read_csv(OUT / "archived_final_odds.csv", dtype={"race_id": str})
d = pd.read_parquet(ROOT / "artifacts/market_ev/rows_2007.parquet", columns=["race_id", "horse_id", "race_date", "year", "odds", "won", "days_since_last", "race_ok", "dead_heat"])
d = d[(d.race_date >= "2026-06-27") & (d.race_date <= "2026-09-22") & d.race_ok & ~d.dead_heat]
for s, t in ENS.items():
    d = d.merge(pd.read_parquet(RES / t / "predictions.parquet")[["race_id", "horse_id", "pred"]].rename(columns={"pred": f"p{s}"}), on=["race_id", "horse_id"], how="left")
d = d.merge(pd.read_parquet(RES / SINGLE / "predictions.parquet")[["race_id", "horse_id", "pred"]].rename(columns={"pred": "ps"}), on=["race_id", "horse_id"], how="left")
d = d.merge(races[["race_id", "affected"]], on="race_id", how="left")
ens = np.mean([1 + d[f"p{s}"].to_numpy(float) / 100 for s in ENS], axis=0)
sg = 1 + d.ps.to_numpy(float) / 100
o = d.odds.to_numpy(float); g = d.days_since_last.to_numpy(float)
aff = d.affected.to_numpy(int) == 1
rates = {"rows_affected": int(aff.sum()), "rows_unaffected": int((~aff).sum())}
for rid in ar.RULE_IDS:
    m = ar.match_mask(ar.definition(rid), ens_ev=ens, single_ev=sg, odds=o, days_since_last=g)
    rates[rid] = {"picks_aff": int((m & aff).sum()), "picks_unaff": int((m & ~aff).sum()),
                  "per1000_aff": round(1000 * (m & aff).sum() / aff.sum(), 3), "per1000_unaff": round(1000 * (m & ~aff).sum() / (~aff).sum(), 3)}
# also: share of horses with ens EV>1.2 by stored odds band in affected vs not
band = pd.cut(o, [1, 5, 10, 20, 40, 100, np.inf], right=False)
ev_tab = pd.DataFrame({"band": band, "aff": aff, "ev12": ens > 1.2}).groupby(["band", "aff"], observed=False).ev12.mean().unstack().round(4)
w = fo[(fo.affected == 1) & fo.db_won & fo.official_payout_yen.gt(0)].copy()
w["ratio"] = w.official_payout_yen / (100 * w.stored_odds)
w["band"] = pd.cut(w.stored_odds, [1, 5, 10, 20, 40, 100, np.inf], right=False)
wb = w.groupby("band", observed=False).agg(n=("ratio", "size"), median=("ratio", "median"), mean=("ratio", "mean"),
                                           agg=("official_payout_yen", "sum"), st=("stored_odds", "sum"))
wb["aggr"] = wb["agg"] / (100 * wb["st"])
out = {"pick_rates_P": rates, "share_ens_ev_gt_1.2_by_band_affected": {str(k): v for k, v in ev_tab.to_dict(orient="index").items()},
       "winner_ratio_by_stored_band": {str(k): {"n": int(r.n), "median": round(float(r["median"]), 4) if r.n else None,
                                                 "mean": round(float(r["mean"]), 4) if r.n else None,
                                                 "aggregate": round(float(r["aggr"]), 4) if r.n else None} for k, r in wb.iterrows()}}
(OUT / "supplement_rates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print(json.dumps(out, ensure_ascii=False, indent=1))

# within-date stratified pick rates (dates having both affected and unaffected races)
d["aff"] = aff
d["d"] = d.race_date.astype(str)
mixed = d.groupby("d").aff.agg(lambda s: s.any() and (~s).any())
mixed_days = mixed[mixed].index
strat = {}
for rid in ar.RULE_IDS:
    m = ar.match_mask(ar.definition(rid), ens_ev=ens, single_ev=sg, odds=o, days_since_last=g)
    t = pd.DataFrame({"d": d.d, "aff": aff, "m": m})
    t = t[t.d.isin(mixed_days)]
    strat[rid] = {"days": int(len(mixed_days)), "rows_aff": int(t.aff.sum()), "rows_unaff": int((~t.aff).sum()),
                  "per1000_aff": round(1000 * t[t.aff].m.mean(), 3), "per1000_unaff": round(1000 * t[~t.aff].m.mean(), 3),
                  # Mantel-Haenszel style pooled rate ratio over days
                  "mh_rate_ratio": round(float(
                      sum(gg[gg.aff].m.sum() * (~gg.aff).sum() / len(gg) for _, gg in t.groupby("d")) /
                      max(1e-12, sum(gg[~gg.aff].m.sum() * gg.aff.sum() / len(gg) for _, gg in t.groupby("d")))), 3)}
out["pick_rates_P_within_mixed_days"] = strat
(OUT / "supplement_rates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print(json.dumps(strat, indent=1))
