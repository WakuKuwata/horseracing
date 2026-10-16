"""Independent re-derivation of S3 / N1 / N2 (race-level normalisation) from the 15 seed files.

Written without copying Codex's select/evidence code paths: pandas groupby instead of
factorize/bincount, explicit per-row EV, different bootstrap draw (integers → bincount).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kuwatawaku/workspace/horseracing")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
SEED_DIRS = ["armC_binary_drop-sameday+weightlive_from2007_ens15"] + [
    f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)
]
CUTOFF = "2026-06-26"

cols = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won",
        "race_ok", "dead_heat", "field_size", "popularity", "n_winners", "finished", "q"]
rows = pd.read_parquet(ROWS, columns=cols)
rows = rows[(rows.race_date >= "2010-01-01") & (rows.race_date <= CUTOFF)]
rows = rows[rows.race_ok & ~rows.dead_heat].copy()
print("population rows", len(rows), "races", rows.race_id.nunique())

# --- seeds: my own join (inner, then assert size unchanged) ---
preds = []
for i, tag in enumerate(SEED_DIRS, 1):
    p = pd.read_parquet(ROOT / "artifacts/roi_explore/results" / tag / "predictions.parquet",
                        columns=["race_id", "horse_id", "pred", "odds"])
    p = p.rename(columns={"pred": f"pred_{i}", "odds": f"odds_{i}"})
    preds.append(p)
df = rows
for p in preds:
    df = df.merge(p, on=["race_id", "horse_id"], how="inner")
assert len(df) == len(rows), (len(df), len(rows))
for i in range(1, 16):
    assert np.allclose(df[f"odds_{i}"], df.odds)
pred_cols = [f"pred_{i}" for i in range(1, 16)]
ev_s = 1.0 + df[pred_cols].to_numpy(float) / 100.0        # (n, 15)
df["EV_ens"] = ev_s.mean(axis=1)
p_s = ev_s / df.odds.to_numpy(float)[:, None]
df["p_bar"] = p_s.mean(axis=1)
df["sum_p"] = df.groupby("race_id")["p_bar"].transform("sum")
df["EV_norm"] = df.p_bar / df.sum_p * df.odds
df["payout"] = np.where(df.won, df.odds * 100.0, 0.0)
df["stake"] = 100.0
# sanity: one winner per race in population
assert (df.groupby("race_id")["won"].sum() == 1).all()

apply = df.year.between(2013, 2026)
df["S3"] = (df.EV_ens > 1.2) & apply
df["N1"] = (df.EV_norm > 1.2) & apply
df["N2"] = (df.EV_ens > 1.0) & (df.EV_norm > 1.2) & apply


def summ(m: pd.Series | np.ndarray, frame=None) -> dict:
    frame = df if frame is None else frame
    x = frame.loc[np.asarray(m)]
    n = len(x)
    pay = float(x.payout.sum())
    return {"n": n, "hits": int(x.won.sum()), "races": int(x.race_id.nunique()),
            "days": int(x.race_date.nunique()), "payout": pay,
            "roi": pay / (100.0 * n) if n else None}


windows = {"ALL": (2013, 2026), "R": (2023, 2026), "POST": (2019, 2026), "D": (2013, 2018), "Q": (2019, 2022)}
report: dict = {"windows": {}, "per_year": {}}
for w, (lo, hi) in windows.items():
    wm = df.year.between(lo, hi)
    report["windows"][w] = {a: summ(df[a] & wm) for a in ["S3", "N1", "N2"]}
for y in range(2013, 2027):
    ym = df.year == y
    report["per_year"][y] = {a: summ(df[a] & ym) for a in ["S3", "N1"]}
print(json.dumps(report["windows"], indent=1))

# ---------------- mechanism decomposition -----------------
# Only applied years from here on.
d = df[apply].copy()
mech: dict = {}
mech["sum_p_quantiles_all_races"] = {
    str(q): float(np.quantile(d.drop_duplicates("race_id").sum_p, q)) for q in [.01, .05, .25, .5, .75, .95, .99]}
# bins of sum_p
bins = [0, 0.9, 0.95, 1.0, 1.05, 1.1, 9]
labels = ["<0.90", "0.90-0.95", "0.95-1.00", "1.00-1.05", "1.05-1.10", ">1.10"]
d["sp_bin"] = pd.cut(d.sum_p, bins=bins, labels=labels, right=False)
tab = {}
for b, g in d.groupby("sp_bin", observed=True):
    tab[b] = {"races": int(g.race_id.nunique())}
    for a in ["S3", "N1"]:
        s = summ(d[a] & (d.sp_bin == b), d)
        tab[b][a] = s
mech["by_sum_p_bin"] = tab

# overlap / added / removed (ALL and R)
for w, (lo, hi) in {"ALL": (2013, 2026), "R": (2023, 2026)}.items():
    wm = d.year.between(lo, hi)
    both = d.S3 & d.N1 & wm
    added = d.N1 & ~d.S3 & wm
    removed = d.S3 & ~d.N1 & wm
    mech[f"sets_{w}"] = {
        "overlap": summ(both, d),
        "added_N1_only": summ(added, d),
        "removed_S3_only": summ(removed, d),
        "added_sum_p_mean": float(d.loc[added, "sum_p"].mean()),
        "removed_sum_p_mean": float(d.loc[removed, "sum_p"].mean()),
        "added_sum_p_max": float(d.loc[added, "sum_p"].max()),
        "removed_sum_p_min": float(d.loc[removed, "sum_p"].min()),
    }
    # Control: S3 restricted to races with sum_p < 1 (a pure 'race-level filter' with no re-ranking)
    for thr in [1.0, 1.05]:
        mech[f"sets_{w}"][f"S3_in_races_sum_p_lt_{thr}"] = summ(d.S3 & wm & (d.sum_p < thr), d)
        mech[f"sets_{w}"][f"S3_in_races_sum_p_ge_{thr}"] = summ(d.S3 & wm & (d.sum_p >= thr), d)
        mech[f"sets_{w}"][f"N1_in_races_sum_p_lt_{thr}"] = summ(d.N1 & wm & (d.sum_p < thr), d)
        mech[f"sets_{w}"][f"N1_in_races_sum_p_ge_{thr}"] = summ(d.N1 & wm & (d.sum_p >= thr), d)
    # Control 2: EV_ens > 1.2 / sum_p  (identical to N1 algebraically) -> check
    assert ((d.EV_ens / d.sum_p > 1.2) == d.N1)[wm].all()

# field size and odds band strata
d["fs_bin"] = pd.cut(d.field_size, [0, 10, 13, 15, 16, 18, 99], labels=["<=10", "11-13", "14-15", "16", "17-18", ">18"], right=True)
d["odds_bin"] = pd.cut(d.odds, [0, 5, 10, 20, 40, 100, 1e9], labels=["<5", "5-10", "10-20", "20-40", "40-100", "100+"])
strata = {}
for key in ["fs_bin", "odds_bin"]:
    strata[key] = {}
    for b, g in d.groupby(key, observed=True):
        strata[key][str(b)] = {a: summ(d[a] & (d[key] == b), d) for a in ["S3", "N1"]}
        strata[key][str(b)]["mean_sum_p_S3"] = float(d.loc[d.S3 & (d[key] == b), "sum_p"].mean()) if (d.S3 & (d[key] == b)).any() else None
mech["strata"] = strata
# Does sum_p correlate with field size / favourite odds?
r1 = d.drop_duplicates("race_id")
mech["corr_sum_p_field_size"] = float(np.corrcoef(r1.sum_p, r1.field_size)[0, 1])
mech["mean_sum_p_by_field_size"] = {str(int(k)): float(v) for k, v in r1.groupby("field_size").sum_p.mean().items()}
# market-expected ROI (takeout proxy) of selected sets: mean q*odds
for a in ["S3", "N1"]:
    mech[f"mean_q_odds_{a}"] = float((d.loc[d[a], "q"] * d.loc[d[a], "odds"]).mean())
    mech[f"mean_odds_{a}"] = float(d.loc[d[a], "odds"].mean())
    mech[f"median_odds_{a}"] = float(d.loc[d[a], "odds"].median())
    mech[f"mean_field_size_{a}"] = float(d.loc[d[a], "field_size"].mean())

# ---------------- paired race-day bootstrap (own implementation) -----------------
def paired_boot(d: pd.DataFrame, arms: list[str], B: int, seed: int, universe: str = "all_days"):
    if universe == "all_days":
        days = np.array(sorted(d.race_date.unique()))
    else:  # days with >=1 bet in any arm
        m = np.zeros(len(d), bool)
        for a in arms:
            m |= d[a].to_numpy()
        days = np.array(sorted(d.loc[m, "race_date"].unique()))
    idx = pd.Index(days)
    D = len(days)
    num = np.zeros((len(arms), D))
    den = np.zeros((len(arms), D))
    for k, a in enumerate(arms):
        x = d[d[a]]
        gi = idx.get_indexer(x.race_date)
        assert (gi >= 0).all()
        num[k] = np.bincount(gi, weights=x.payout.to_numpy(), minlength=D)
        den[k] = np.bincount(gi, weights=x.stake.to_numpy(), minlength=D)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, D, size=(B, D))
    reps = np.empty((len(arms), B))
    for b in range(B):
        c = np.bincount(picks[b], minlength=D).astype(float)
        reps[:, b] = (num @ c) / (den @ c)
    out = {"n_days_universe": D}
    for k, a in enumerate(arms):
        out[a] = {"roi": float(num[k].sum() / den[k].sum()),
                  "ci": [float(np.nanpercentile(reps[k], 2.5)), float(np.nanpercentile(reps[k], 97.5))]}
        if k > 0:
            diff = reps[k] - reps[0]
            out[a]["delta_vs_S3"] = out[a]["roi"] - out[arms[0]]["roi"]
            out[a]["delta_ci"] = [float(np.nanpercentile(diff, 2.5)), float(np.nanpercentile(diff, 97.5))]
            out[a]["p_delta_le_0"] = float(np.mean(diff <= 0))
    return out


boot = {}
for w, (lo, hi) in {"ALL": (2013, 2026), "POST": (2019, 2026), "R": (2023, 2026)}.items():
    sub = d[d.year.between(lo, hi)]
    boot[f"{w}_alldays_B4000_seed7"] = paired_boot(sub, ["S3", "N1", "N2"], 4000, 7)
    boot[f"{w}_betdays_B4000_seed7"] = paired_boot(sub, ["S3", "N1", "N2"], 4000, 7, universe="bet_days")
print(json.dumps(boot, indent=1))

# Save per-bet payout series of S3 and N1 (for the power study) with race_date ordering.
for a in ["S3", "N1"]:
    d.loc[d[a], ["race_id", "race_date", "year", "horse_number", "odds", "won", "payout", "sum_p", "EV_ens", "EV_norm"]] \
        .sort_values(["race_date", "race_id", "horse_number"]).to_parquet(OUT / f"bets_{a}.parquet", index=False)

report["mechanism"] = mech
report["bootstrap"] = boot
(OUT / "recompute.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
print("saved", OUT / "recompute.json")
