"""R04 POST-HOC diagnostic (NOT pre-registered): how much of dR2(ens15 | q) is a pure non-linear recalibration of
the market price, i.e. what an odds/q-only model already captures?

Uses the existing odds-only arm C binary model (features = odds, q only; annual walk-forward, trained from 1986 —
for a price-only recalibration the training start is not a horse-information leak) as a third component.

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r04_posthoc_oddsonly.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r04_common as C  # noqa: E402
import r04_delta_r2 as R  # noqa: E402

TAG = "armC_binary_drop-all_but_odds_oddsonly"
#: second post-hoc diagnostic: the same 77-feature single-seed model trained from 1986 (137 research v1)
TAG_V1 = "armC_binary_drop-sameday+weightlive_serving_v1"


def main():
    d, prov = C.load_population()
    f = C.RES / TAG / "predictions.parquet"
    v = pd.read_parquet(f, columns=["race_id", "horse_id", "pred"]).rename(columns={"pred": "pred_odd"})
    d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    miss = int(d.pred_odd.isna().sum())
    if miss:
        raise SystemExit(f"oddsonly missing {miss} rows")
    d["p_odd"] = (1.0 + d.pred_odd / 100.0) / d.odds
    f1 = C.RES / TAG_V1 / "predictions.parquet"
    v1 = pd.read_parquet(f1, columns=["race_id", "horse_id", "pred"]).rename(columns={"pred": "pred_v1"})
    d = d.merge(v1, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    if d.pred_v1.isna().any():
        raise SystemExit(f"v1 missing {int(d.pred_v1.isna().sum())} rows")
    d["p_v1"] = (1.0 + d.pred_v1 / 100.0) / d.odds
    eng = R.Engine(d, {"q": "q", "ens": "p_ens", "odd": "p_odd", "mev": "p_mev", "v1": "p_v1"})
    pre = eng.prequential({"M0": ["q"], "ens": ["q", "ens"], "odd": ["q", "odd"], "odd_ens": ["q", "odd", "ens"],
                           "odd_mev": ["q", "odd", "mev"], "mev": ["q", "mev"], "v1": ["q", "v1"],
                           "mev_v1": ["q", "mev", "v1"]})
    incr = {"odd|q": ("M0", "odd"), "ens|q,odd": ("odd", "odd_ens"), "ens|q": ("M0", "ens"),
            "mev|q,odd": ("odd", "odd_mev"), "v1_1986|q": ("M0", "v1"), "v1_1986_vs_mev2007": ("mev", "v1"),
            "v1_1986|q,mev2007": ("mev", "mev_v1")}
    out = {"posthoc": True, "not_preregistered": True, "oddsonly_sha256": C.sha(f), "v1_sha256": C.sha(f1),
           "increments": R.strip(R.increments(eng, pre, incr)), "fits": pre["fits"]}
    sl = pre["scored_lo"]
    days, years, logn = eng.day[sl:], eng.year[sl:], eng.logN[sl:]
    yearly = []
    for name, (red, full) in incr.items():
        num = pre["nll"][red] - pre["nll"][full]
        for y in sorted(set(years.tolist())):
            k = years == y
            yearly.append({"increment": name, "year": int(y), "point": float(num[k].sum() / logn[k].sum())})
    out["yearly"] = yearly
    (C.OUT / "posthoc_oddsonly.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps(out["increments"], indent=1))


if __name__ == "__main__":
    main()
