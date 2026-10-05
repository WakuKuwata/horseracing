"""R07_routing — post-hoc diagnostics (NOT pre-registered; reported as deviations/diagnostics).

1. Corrected pipeline calibration. The registered exchangeable-ratio null centred the permuted ratios
   with kappa = sum(A r)/sum(A) (A-weighted). Under a random permutation E[sum A r_pi] = r_bar * sum A
   (simple mean), so the registered construction is NOT centred at c whenever r and A are correlated.
   Here kappa = mean(r) (simple), which makes E_pi[sum B*] = c * sum A exactly.
2. Heavy-tail robustness: theta after dropping the k largest events by B (k=1,2,3), per window.
3. Mean-of-ratios estimator theta_mean = mean_e(B_e/A_e)/c (each hit event weighted equally); under
   fair per-pool pricing E[B/A | event] = c for every event, so this is a pricing test that is not
   dominated by record payouts. Day-cluster bootstrap CI and one-sided p in the W1 direction.

Run: cd training && uv run python ../scripts/roi_explore/missed_20261004/r07_posthoc.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r07_routing as M  # noqa: E402
from horseracing_eval.bootstrap import (  # noqa: E402
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
)

OUT = M.OUT


def calibration_corrected(ev, tk, n_sim=M.N_SIM):
    fw = 0
    per = defaultdict(int)
    for s in range(1, n_sim + 1):
        rng = np.random.default_rng(s)
        evs = ev.copy()
        for cl in M.CELLS:
            pair = cl[0]
            hi2 = M.window_bounds(pair, M.W2)[1]
            mask = (evs.cell == cl) & (((evs.race_date >= M.W1[0]) & (evs.race_date <= M.W1[1]))
                                       | ((evs.race_date >= M.W2[0]) & (evs.race_date <= hi2)))
            idx = np.flatnonzero(mask.values)
            if idx.size == 0:
                continue
            A = evs.A.values[idx]
            r = evs.B.values[idx] / (M.NULL_C[pair] * A)
            rbar = float(r.mean())
            perm = rng.permutation(idx.size)
            evs.loc[evs.index[idx], "B"] = M.NULL_C[pair] * A * r[perm] / rbar
        w1 = M.eval_window(evs, tk, M.W1, b=50, seed=M.SEED)
        w2 = M.eval_window(evs, tk, M.W2, b=M.B_SIM, seed=M.SEED + s)
        sign, pv, rej, adj, *_ = M.run_tests(w1, w2)
        if any(rej.values()):
            fw += 1
        for cl, v in rej.items():
            per[cl] += int(v)
    return {"n_sim": n_sim, "b_boot": M.B_SIM, "centering": "simple mean of r within cell (W1+W2 pooled)",
            "familywise_fpr": fw / n_sim, "per_cell_rejection_rate": {cl: per[cl] / n_sim for cl in M.CELLS}}


def drop_topk(ev, tk):
    out = {}
    for tag, w in (("W1", M.W1), ("W2", M.W2)):
        for cl in M.CELLS:
            pair = cl[0]
            lo, hi = M.window_bounds(pair, w)
            e = ev[(ev.cell == cl) & (ev.race_date >= lo) & (ev.race_date <= hi)]
            days = sorted(set(ev[(ev.pair == pair) & (ev.race_date >= lo) & (ev.race_date <= hi)].race_date))
            res = {}
            for k in (0, 1, 2, 3):
                ek = e.drop(e.nlargest(k, "B").index) if k else e
                A = M.day_matrix(ek, days, "A")
                B = M.day_matrix(ek, days, "B")
                bt = race_block_ratio_bootstrap_ci_v1(B / M.NULL_C[pair], A, days, b=M.B_MAIN, seed=M.SEED)
                res[f"k{k}"] = {"theta": float(bt.point[0]), "lo": float(bt.ci_low[0]), "hi": float(bt.ci_high[0]),
                                "n": int(len(ek))}
            out[f"{tag}_{cl}"] = res
    return out


def mean_of_ratios(ev, w1_sign):
    out = {}
    pv = {}
    for tag, w in (("W1", M.W1), ("W2", M.W2)):
        for cl in M.CELLS:
            pair = cl[0]
            lo, hi = M.window_bounds(pair, w)
            e = ev[(ev.cell == cl) & (ev.race_date >= lo) & (ev.race_date <= hi)].copy()
            days = sorted(set(ev[(ev.pair == pair) & (ev.race_date >= lo) & (ev.race_date <= hi)].race_date))
            e["rr"] = e.B / (M.NULL_C[pair] * e.A)
            e["one"] = 1.0
            num = M.day_matrix(e, days, "rr")
            den = M.day_matrix(e, days, "one")
            bt = race_block_ratio_bootstrap_ci_v1(num, den, days, b=M.B_MAIN, seed=M.SEED)
            st = {"theta_mean": float(bt.point[0]), "lo": float(bt.ci_low[0]), "hi": float(bt.ci_high[0]),
                  "n": int(len(e)), "median_r_over_c": float(e.rr.median()) if len(e) else None}
            if tag == "W2":
                s = w1_sign.get(cl, 1)
                if s == 1:
                    p = centered_one_sided_p_from_replicates(bt.replicates[0], float(bt.point[0]))
                else:
                    bi = race_block_ratio_bootstrap_ci_v1(den, num, days, b=M.B_MAIN, seed=M.SEED)
                    p = centered_one_sided_p_from_replicates(bi.replicates[0], float(bi.point[0]))
                st["p_W2_dirW1"] = p
                pv[cl] = p
            out[f"{tag}_{cl}"] = st
    rej, adj = M.holm(pv)
    for cl in pv:
        out[f"W2_{cl}"]["p_holm"] = adj[cl]
        out[f"W2_{cl}"]["holm_reject"] = rej[cl]
    return out


def main():
    ev = pd.read_parquet(OUT / "events.parquet")
    races, rh, rr, ex, qq = M.load()
    ev2, tk, _ = M.build_events(races, rh, rr, ex)
    assert len(ev2) == len(ev) and np.allclose(ev2.B.values, ev.B.values), "events drifted since main run"
    res = json.loads((OUT / "results.json").read_text())
    w1_sign_th = {cl: (1 if res["tests"][cl]["sign_W1"] == 1 else -1) for cl in M.CELLS}
    # mean-of-ratios uses its OWN W1 sign (theta_mean W1 >= 1 -> +1)
    mor_w1 = mean_of_ratios(ev, {cl: 1 for cl in M.CELLS})
    sign_mor = {cl: (1 if mor_w1[f"W1_{cl}"]["theta_mean"] >= 1 else -1) for cl in M.CELLS}
    mor = mean_of_ratios(ev, sign_mor)
    for cl in M.CELLS:
        mor[f"W2_{cl}"]["sign_W1_mor"] = sign_mor[cl]
    dk = drop_topk(ev, tk)
    cal = calibration_corrected(ev, tk)
    out = {"note": "post-hoc diagnostics, not pre-registered", "calibration_corrected": cal,
           "drop_topk": dk, "mean_of_ratios": mor, "primary_sign_W1": w1_sign_th}
    (OUT / "posthoc.json").write_text(json.dumps(M.clean(out), indent=1, ensure_ascii=False))
    print(json.dumps(M.clean(cal), ensure_ascii=False))
    for cl in M.CELLS:
        print(cl, {k: round(v["theta"], 3) for k, v in dk[f"W2_{cl}"].items()},
              "W1mor", round(mor[f"W1_{cl}"]["theta_mean"], 3), "W2mor", round(mor[f"W2_{cl}"]["theta_mean"], 3),
              (round(mor[f"W2_{cl}"]["lo"], 3), round(mor[f"W2_{cl}"]["hi"], 3)),
              "p", round(mor[f"W2_{cl}"]["p_W2_dirW1"], 5), "holm", mor[f"W2_{cl}"]["holm_reject"])


if __name__ == "__main__":
    main()
