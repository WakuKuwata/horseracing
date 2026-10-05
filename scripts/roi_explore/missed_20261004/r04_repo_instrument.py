"""R04: run the UNCHANGED repo instrument eval/delta_r2.evaluate_delta_r2 on ens15 or mev (P1 authoritative).

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r04_repo_instrument.py --model ens
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import pathlib

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r04_common as C  # noqa: E402
from horseracing_eval.delta_r2 import DeltaR2Race, evaluate_delta_r2  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["ens", "mev"], required=True)
    a = ap.parse_args()
    t0 = time.time()
    d, prov = C.load_population()
    col = {"ens": "p_ens", "mev": "p_mev"}[a.model]
    races = []
    for rid, g in d.groupby("race_id", sort=False):
        w = np.flatnonzero(g.won.to_numpy())
        races.append(DeltaR2Race(race_id=rid, day=g.race_date.iloc[0], block=g.race_date.iloc[0][:4],
                                 winner_idx=int(w[0]), p=g[col].to_numpy(float), q=g.q.to_numpy(float)))
    print(f"{len(races)} races built in {time.time()-t0:.0f}s", flush=True)
    rep = evaluate_delta_r2(races, b=2000, seed=20260729)
    out = {"model": a.model, "provenance": prov, "elapsed_s": time.time() - t0, "result": rep.to_dict()}
    for k in ("ci_literal", "ci_model_given_market"):
        out["result"][k].pop("replicates", None)
    p = C.OUT / f"repo_instrument_{a.model}.json"
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k: out["result"][k] for k in ("delta_r2_model_given_market", "delta_r2_literal", "verdict")}))
    print(f"done {time.time()-t0:.0f}s -> {p}")


if __name__ == "__main__":
    main()
