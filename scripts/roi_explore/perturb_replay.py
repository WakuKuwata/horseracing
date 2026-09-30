"""Arm C executability probe: select with NOISY odds (simulating pre-close odds), settle at closing.

Loads the yearly boosters saved by direct_return_model.py (--save-last-model PREFIX → PREFIX_{year}.txt + PREFIX.spec.json),
rebuilds the market features from perturbed odds odds' = odds·exp(N(0,σ)) within each race (q', ranks, favourite structure),
predicts, applies the fixed policies (EV' > τ, optional odds' < 21), and settles at the TRUE closing odds.

    cd training && uv run python ../scripts/roi_explore/perturb_replay.py --prefix ../artifacts/roi_explore/results/armC_binary_saved_model
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import evaluate as ev  # noqa: E402

ART = ev.ART
MARKET_COLS = ["odds", "q", "popularity", "odds_rank", "q_share_of_fav", "fav_q", "fav_odds", "second_odds",
               "odds_gap12", "q_entropy_norm", "n_fav_under_2", "n_odds_under_10"]


def rebuild_market(df: pd.DataFrame, odds: np.ndarray) -> pd.DataFrame:
    d = df.copy()
    d["odds"] = odds
    g = d.groupby("race_id", sort=False)
    inv = 1.0 / d["odds"]
    d["q"] = inv / inv.groupby(d["race_id"]).transform("sum")
    order = d.sort_values(["race_id", "odds", "horse_number"]).index
    rank = np.empty(len(d)); rank[order] = d.loc[order].groupby("race_id", sort=False).cumcount().to_numpy() + 1
    d["odds_rank"] = rank; d["popularity"] = rank
    fav = d[d["odds_rank"] == 1].set_index("race_id")["odds"]; sec = d[d["odds_rank"] == 2].set_index("race_id")["odds"]
    d["fav_odds"] = d["race_id"].map(fav).astype(float); d["second_odds"] = d["race_id"].map(sec).astype(float)
    d["odds_gap12"] = d["second_odds"] - d["fav_odds"]
    d["fav_q"] = d.groupby("race_id")["q"].transform("max"); d["q_share_of_fav"] = d["q"] / d["fav_q"]
    ent = (-(d["q"] * np.log(d["q"].clip(lower=1e-12)))).groupby(d["race_id"]).transform("sum")
    d["q_entropy_norm"] = ent / np.log(d["field_size"].clip(lower=2))
    d["n_fav_under_2"] = (d["odds"] < 2.0).astype(float).groupby(d["race_id"]).transform("sum")
    d["n_odds_under_10"] = (d["odds"] < 10.0).astype(float).groupby(d["race_id"]).transform("sum")
    return d


def build_X(d: pd.DataFrame, spec: dict) -> np.ndarray:
    X = d[spec["features"]].astype(float).copy()
    for c in spec["cats"]:
        mp = spec["cat_maps"][c]
        X[c] = d[c].astype(object).map(lambda v: mp.get(str(v), np.nan) if v is not None and not (isinstance(v, float) and np.isnan(v)) else np.nan).astype(float)
    return X.to_numpy(dtype=np.float32)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--years", default="2019-2026")
    ap.add_argument("--sigmas", default="0,0.05,0.1,0.2,0.3")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args(argv)
    spec = json.loads(pathlib.Path(args.prefix + ".spec.json").read_text())
    y0, y1 = (int(x) for x in args.years.split("-"))
    df = pd.read_parquet(ART / "rows.parquet")
    df = df[df["race_ok"] & ~df["dead_heat"] & (df["year"] >= y0) & (df["year"] <= y1)].sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    closing = df["odds"].to_numpy(dtype=float)
    payout_close = df["won"].to_numpy().astype(float) * closing * 100.0
    years = df["year"].to_numpy()
    day_idx = pd.factorize(df["race_date"])[0]
    boosters = {yv: lgb.Booster(model_file=f"{args.prefix}_{yv}.txt") for yv in range(y0, y1 + 1) if pathlib.Path(f"{args.prefix}_{yv}.txt").exists()}
    print("boosters:", sorted(boosters))
    rng = np.random.default_rng(args.seed)
    out = {}
    base_sel = None
    for sigma in [float(s) for s in args.sigmas.split(",")]:
        reps = 1 if sigma == 0 else args.reps
        acc = {}
        for r in range(reps):
            noise = np.exp(rng.normal(0.0, sigma, size=len(df))) if sigma > 0 else np.ones(len(df))
            odds_p = np.maximum(closing * noise, 1.0)
            d = rebuild_market(df, odds_p)
            X = build_X(d, spec)
            pred = np.full(len(d), np.nan)
            for yv, bst in boosters.items():
                te = years == yv
                if te.any():
                    p_hat = bst.predict(X[te])
                    pred[te] = 100.0 * (p_hat * odds_p[te] - 1.0) if spec["objective"] == "binary" else p_hat
            for tau in (0.0, 10.0, 20.0):
                for cap in (None, 21.0):
                    m = pred > tau
                    if cap:
                        m &= odds_p < cap
                    sel = np.flatnonzero(m)
                    key = f"EV'>{tau:g}" + (f"&odds'<{cap:g}" if cap else "")
                    po = payout_close[sel]
                    rec = acc.setdefault(key, {"n": [], "roi": [], "hits": [], "overlap": []})
                    rec["n"].append(int(len(sel))); rec["roi"].append(float(po.mean() / 100.0) if len(sel) else None)
                    rec["hits"].append(int((po > 0).sum()))
                    if sigma == 0:
                        base_sel = base_sel or {}
                        base_sel[key] = set(sel.tolist())
                    elif base_sel and key in base_sel:
                        inter = len(base_sel[key] & set(sel.tolist())); union = len(base_sel[key] | set(sel.tolist()))
                        rec["overlap"].append(inter / union if union else None)
        out[sigma] = {k: {"n_mean": float(np.mean(v["n"])), "roi_mean": float(np.mean([x for x in v["roi"] if x is not None])) if any(x is not None for x in v["roi"]) else None,
                          "roi_reps": v["roi"], "hits_mean": float(np.mean(v["hits"])),
                          "jaccard_vs_closing": float(np.mean(v["overlap"])) if v["overlap"] else None} for k, v in acc.items()}
        print(f"sigma={sigma}: " + " | ".join(f"{k}: roi={v['roi_mean']:.3f} n={v['n_mean']:.0f} J={v['jaccard_vs_closing'] if v['jaccard_vs_closing'] is None else round(v['jaccard_vs_closing'], 2)}" for k, v in out[sigma].items() if v["roi_mean"] is not None))
    res_dir = ART / "results" / "armC_perturb_replay"
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "result.json").write_text(json.dumps({"prefix": args.prefix, "years": args.years, "reps": args.reps, "by_sigma": out}, ensure_ascii=False, indent=1, default=ev._default))
    return 0


if __name__ == "__main__":
    sys.exit(main())
