"""Power of the 138 checkpoint rule (300/600 points, race-day block percentile CI).

Source series: the actual S3 (and N1) stored-odds payout sequence, 2019-01..2026-06-26,
grouped by race day. Each simulated prospective run draws race days with replacement
(order randomised), concatenates their bets until 600 picks, rescales payouts so the true
ROI equals a target r (multiplicative; hit rate unchanged), then applies decide_checkpoint's
rule at 300 and 600 with the percentile race-day block bootstrap (same method as
horseracing_eval.bootstrap.race_block_ratio_bootstrap_ci_v1; B is reduced for speed and
validated against the real function at B=20000 on one draw).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(sys.argv[1])
B_SIM = 3000
N_SIM = 1000
SEED = 2026107
TARGETS = [0.85, 1.00, 1.12, 1.21]

try:
    from horseracing_eval.bootstrap import race_block_ratio_bootstrap_ci_v1
    HAVE_EVAL = True
except Exception as e:  # pragma: no cover
    print("eval import failed:", e)
    HAVE_EVAL = False


def day_ci(num, den, b, rng):
    """percentile CI of sum(num)/sum(den) over day blocks, b replicates."""
    D = len(num)
    picks = rng.integers(0, D, size=(b, D))
    reps = np.empty(b)
    for i in range(b):
        c = np.bincount(picks[i], minlength=D).astype(float)
        reps[i] = (num @ c) / (den @ c)
    return float(np.nanpercentile(reps, 2.5)), float(np.nanpercentile(reps, 97.5))


def decide(lo, hi, checkpoint):
    if lo > 1.0:
        return "passed"
    if hi < 1.0:
        return "failed"
    return "continue" if checkpoint == 300 else "undecided"


out = {"settings": {"B_sim": B_SIM, "n_sim": N_SIM, "seed": SEED, "targets": TARGETS,
                    "scaling": "payout * r / ROI_source (hit rate unchanged)"}}
for arm in ["S3", "N1"]:
    bets = pd.read_parquet(OUT / f"bets_{arm}.parquet")
    for wname, wm in {"ALL": bets.year >= 2013, "POST": bets.year >= 2019}.items():
        x = bets[wm]
        pay = x.payout.to_numpy() / 100.0  # per 100 yen -> ratio units
        mean, sd = pay.mean(), pay.std(ddof=1)
        hit = (pay > 0).mean()
        win_mean = pay[pay > 0].mean()
        # day-level design effect: SE of ROI by day bootstrap vs iid
        days = x.groupby("race_date").agg(num=("payout", "sum"), den=("payout", "size")).reset_index()
        num = days.num.to_numpy() / 100.0; den = days.den.to_numpy().astype(float)
        rng = np.random.default_rng(SEED)
        D = len(num); picks = rng.integers(0, D, size=(4000, D))
        reps = np.array([(num @ np.bincount(p, minlength=D)) / (den @ np.bincount(p, minlength=D)) for p in picks])
        se_boot = reps.std(ddof=1); se_iid = sd / np.sqrt(len(pay))
        deff = (se_boot / se_iid) ** 2
        n_half = (1.96 * sd / 0.12) ** 2            # expected lower bound = 1 at true ROI 1.12 (50% power)
        n_80 = ((1.96 + 0.8416) * sd / 0.12) ** 2   # 80% power one-sided-ish normal approx
        rec = {"n": int(len(pay)), "days": int(D), "bets_per_day": float(len(pay) / D),
               "mean_payout_per_100": float(mean), "sd_payout_per_100": float(sd), "hit_rate": float(hit),
               "mean_winning_payout_per_100": float(win_mean), "max_payout_per_100": float(pay.max()),
               "roi": float(mean), "se_iid": float(se_iid), "se_dayboot": float(se_boot), "design_effect": float(deff),
               "n_needed_true112_lower_gt_100_50pct_iid": float(n_half),
               "n_needed_true112_lower_gt_100_50pct_deff": float(n_half * deff),
               "n_needed_true112_lower_gt_100_80pct_iid": float(n_80),
               "n_needed_true112_lower_gt_100_80pct_deff": float(n_80 * deff)}
        out[f"{arm}_{wname}_empirical"] = rec
        print(arm, wname, json.dumps(rec, indent=1))

# ---- checkpoint simulation from the POST series of each arm ----
for arm in ["S3", "N1"]:
    bets = pd.read_parquet(OUT / f"bets_{arm}.parquet")
    x = bets[bets.year >= 2019]
    src_roi = x.payout.mean() / 100.0
    groups = [g.payout.to_numpy() / 100.0 for _, g in x.groupby("race_date")]
    nd = len(groups)
    rng = np.random.default_rng(SEED + (1 if arm == "N1" else 0))
    # validation of my CI vs eval function on one simulated run
    sim_res = {}
    for r in TARGETS:
        scale = r / src_roi
        tally = {300: {"passed": 0, "failed": 0, "continue": 0}, 600: {"passed": 0, "failed": 0, "undecided": 0, "not_reached": 0}}
        lows300, lows600, rois300, rois600 = [], [], [], []
        for s in range(N_SIM):
            seq, dayid, total = [], [], 0
            k = 0
            while total < 600:
                g = groups[rng.integers(0, nd)] * scale
                seq.append(g); dayid.append(np.full(len(g), k)); total += len(g); k += 1
            seq = np.concatenate(seq); dayid = np.concatenate(dayid)
            for cp in (300, 600):
                h = seq[:cp]; hd = dayid[:cp]
                num = np.bincount(hd, weights=h); den = np.bincount(hd).astype(float)
                keep = den > 0
                lo, hi = day_ci(num[keep], den[keep], B_SIM, rng)
                dec = decide(lo, hi, cp)
                if cp == 300:
                    tally[300][dec] += 1
                    lows300.append(lo); rois300.append(h.mean())
                    if dec != "continue":
                        tally[600]["not_reached"] += 1
                        break
                else:
                    tally[600][dec] += 1
                    lows600.append(lo); rois600.append(h.mean())
                    if HAVE_EVAL and s == 0:
                        keys = [f"2030-01-{i+1:02d}" if i < 28 else f"2030-{2 + (i - 28) // 28:02d}-{(i - 28) % 28 + 1:02d}" for i in range(keep.sum())]
                        res = race_block_ratio_bootstrap_ci_v1(num[keep], den[keep], keys, b=20000)
                        sim_res.setdefault("validation_vs_eval_fn", {})[str(r)] = {
                            "mine": [lo, hi], "eval_fn_b20000": [float(res.ci_low[0]), float(res.ci_high[0])]}
        p300 = {k: v / N_SIM for k, v in tally[300].items()}
        n_reach = N_SIM - tally[600]["not_reached"]
        p600_cond = {k: (v / n_reach if n_reach else None) for k, v in tally[600].items() if k != "not_reached"}
        p600_uncond = {k: v / N_SIM for k, v in tally[600].items()}
        # overall outcome of the two-stage rule
        overall = {"passed": p300["passed"] + p600_uncond["passed"],
                   "failed": p300["failed"] + p600_uncond["failed"],
                   "undecided_after_600": p600_uncond["undecided"]}
        sim_res[str(r)] = {"at_300": p300, "at_600_given_continue": p600_cond, "at_600_unconditional": p600_uncond,
                           "overall": overall,
                           "mean_ci_low_300": float(np.mean(lows300)), "mean_ci_low_600": float(np.mean(lows600)) if lows600 else None,
                           "sd_point_roi_300": float(np.std(rois300)), "sd_point_roi_600": float(np.std(rois600)) if rois600 else None}
        print(arm, r, json.dumps(sim_res[str(r)]))
    out[f"{arm}_checkpoint_sim_from_POST"] = {"source_roi": float(src_roi), "source_days": nd, "source_bets": int(len(x)), **sim_res}

(OUT / "power.json").write_text(json.dumps(out, indent=1))
print("saved")
