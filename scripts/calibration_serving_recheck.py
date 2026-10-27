"""135 Step1: what does the 132 top2/top3 calibration add to PRODUCTION — equivalence vs real gaps.

132 arm C ("annual_stage_discount") derives top2/top3 from the mixture win with stage λ fit on
prior-year OOS mixed6 wins of the same regime. Production (131) derives top2/top3 from the same
mixture win with λ re-fit daily from the latest persisted run per race. Three questions, no fits:

1. Equivalence — given the SAME win vector and the SAME λ, is the production computation
   (``assemble_predictions(eps=0, stage_discount)``) identical to 132's arm C? If yes, 132's gain
   over "head mean" is not a new improvement of production; production already computes this way.
2. Material conversion — on the SAME 132 OOS material, does production's sample rule (no
   complete-results requirement, engine clip+renormalise) change λ or the head quality vs 132's
   rule? Controlled comparison of the conversion only.
3. Irreproducible part — production's λ comes from the latest persisted runs across model
   generations (2026 backfills are in-sample). That population cannot be rebuilt from frozen inputs;
   we only report the actual production λ values that were recorded (129 frozen anchor as of
   2026-08-24; the 2026-09-13 fit captured by 133) next to the 132 OOS λ, and the head-quality
   sensitivity of applying each λ to the 2026 mixed6 preweight wins (diagnostic, post hoc).

Data cutoff 2026-09-06: everything here is 111-snapshot material through 2026-08-23, plus λ values
recorded in earlier artifacts (no DB access).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "serving/src"))
from horseracing_eval.stage_discount import StageDiscount, TopkSample, fit_stage_discount  # noqa: E402
from horseracing_training.predictor import assemble_predictions  # noqa: E402

SOURCE132 = ROOT / "artifacts/132-accuracy-first-batch/topk"
ANCHOR129 = ROOT / "artifacts/129-candidate-mixture-serving/anchor/stage-discount.json"
CAPTURE133 = ROOT / "artifacts/133-prospective-paired-comparison/records/capture"
SPEC = ROOT / "specs/135-mixture-reproducibility"
CUTOFF = dt.date(2026, 9, 6)
YEARS = tuple(range(2020, 2027))
EPS_ENGINE = 1e-6


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False, default=str)


def load_regime(regime):
    recs = pickle.load((SOURCE132 / f"{regime}-predictions.pkl").open("rb"))
    summary = json.loads((SOURCE132 / f"{regime}-summary.json").read_text())
    lam = {int(f["year"]): (float(f["lambda2"]), float(f["lambda3"]), bool(f["fallback"])) for f in summary["annual_stage_fits"]}
    for r in recs:
        if dt.date.fromisoformat(r["race_date"]) > CUTOFF:
            raise ValueError("132 material exceeds the 135 cutoff")
    return recs, lam, summary


def labels_of(rec):
    lab = {h: (w, t2, t3) for h, w, t2, t3 in rec["labels"]}
    ids = list(rec["ids"])
    y = np.array([lab.get(h, (0, 0, 0)) for h in ids], dtype=int)
    complete = rec["n_result_rows"] is not None and rec["n_result_rows"] >= len(ids)
    return y, complete


def finishers(y):
    """(i1, i2, i3) indices from win/top2/top3 flags; None when a position is not unique."""
    sets = [set(np.flatnonzero(y[:, 0])), set(np.flatnonzero(y[:, 1])), set(np.flatnonzero(y[:, 2]))]
    groups = (sets[0], sets[1] - sets[0], sets[2] - sets[1])
    return tuple(int(next(iter(g))) if len(g) == 1 else None for g in groups)


def engine_normalize(win):
    p = np.clip(np.asarray(win, dtype=float), EPS_ENGINE, 1 - EPS_ENGINE)
    return p / p.sum()


def sample(rec, *, rule):
    y, complete = labels_of(rec)
    win = rec["arms"]["mean_win_harville"][:, 0]
    i1, i2, i3 = finishers(y)
    if rule == "132":
        if not complete:
            return None
        return TopkSample(tuple(map(float, win)), i1, i2, i3)
    if rule == "production":  # to_topk_samples: unique winner required, engine normalisation, no completeness gate
        if i1 is None:
            return None
        return TopkSample(tuple(map(float, engine_normalize(win))), i1, i2, i3)
    raise ValueError(rule)


def heads_via_production(ids, win, lam):
    sd = StageDiscount(lam[0], lam[1], 0, 0, lam[2])
    out = assemble_predictions(list(ids), np.asarray(win, dtype=float), eps=0.0, stage_discount=sd)
    return np.array([[out[h].win, out[h].top2, out[h].top3] for h in ids])


def head_metrics(pred, y):
    out = {}
    for j, name in enumerate(("win", "top2", "top3")):
        p = np.clip(pred[:, j], 1e-15, 1 - 1e-15)
        yy = y[:, j].astype(float)
        out[name] = {"logloss": float(np.mean(-(yy * np.log(p) + (1 - yy) * np.log(1 - p)))),
                     "brier": float(np.mean((p - yy) ** 2)), "n": int(len(p))}
    return out


def equivalence(recs, lam):
    worst = {"top2": 0.0, "top3": 0.0, "win": 0.0}
    n = 0
    for r in recs:
        year = dt.date.fromisoformat(r["race_date"]).year
        ref = r["arms"]["annual_stage_discount"]
        mine = heads_via_production(r["ids"], r["arms"]["mean_win_harville"][:, 0], lam[year])
        for j, name in enumerate(("win", "top2", "top3")):
            worst[name] = max(worst[name], float(np.max(np.abs(mine[:, j] - ref[:, j]))))
        n += len(r["ids"])
    return {"max_abs_diff": worst, "n_horses": n, "equivalent": max(worst.values()) <= 1e-12,
            "meaning": ("production's display computation (assemble_predictions eps=0 + stage discount) on the same "
                        "win and λ reproduces 132 arm C exactly; 132's 'C beats head-mean' is therefore a statement about "
                        "the production convention itself, not an addition to production")}


def conversion_control(recs, lam132):
    """Fit λ walk-forward under both sample rules on the same material; score year Y heads with each."""
    by_year = {}
    prior = []
    out = {}
    for year in YEARS:
        cur = [r for r in recs if dt.date.fromisoformat(r["race_date"]).year == year]
        fits = {}
        for rule in ("132", "production"):
            samples = [s for s in (sample(r, rule=rule) for r in prior) if s is not None]
            sd = fit_stage_discount(samples, min_races=300)
            fits[rule] = {"lambda2": sd.lambda2, "lambda3": sd.lambda3, "fallback": sd.fallback,
                          "n_stage2": sd.n_races_l2, "n_stage3": sd.n_races_l3}
        fits["132_recorded"] = {"lambda2": lam132[year][0], "lambda3": lam132[year][1], "fallback": lam132[year][2]}
        # score year Y under both λ (complete-results races, started-all)
        preds = {rule: [] for rule in ("132", "production")}
        ys = []
        for r in cur:
            y, complete = labels_of(r)
            if not complete:
                continue
            ys.append(y)
            for rule in preds:
                f = fits[rule]
                preds[rule].append(heads_via_production(r["ids"], r["arms"]["mean_win_harville"][:, 0],
                                                        (f["lambda2"], f["lambda3"], f["fallback"])))
        if ys:
            Y = np.concatenate(ys)
            m = {rule: head_metrics(np.concatenate(preds[rule]), Y) for rule in preds}
            fits["head_metrics"] = m
            fits["production_minus_132_logloss"] = {h: m["production"][h]["logloss"] - m["132"][h]["logloss"] for h in ("top2", "top3")}
        by_year[str(year)] = fits
        prior.extend(cur)
    out["by_year"] = by_year
    out["incomplete_results_races_in_material"] = int(sum(1 for r in recs if not labels_of(r)[1]))
    return out


def recorded_production_lambdas():
    out = {}
    a = json.loads(ANCHOR129.read_text())
    out["129_frozen_anchor_asof_2026-08-24"] = {"lambda2": a["lambda2"], "lambda3": a["lambda3"], "n_races_l2": a["n_races_l2"],
                                               "sha256": digest(ANCHOR129)}
    caps = sorted(CAPTURE133.glob("*.json"))
    if caps:
        rec = json.loads(caps[0].read_text())
        sd = rec.get("production_stage_discount_of_day")
        if sd:
            out["133_production_fit_2026-09-13"] = {k: sd[k] for k in ("lambda2", "lambda3", "n_races_l2", "n_races_l3", "method")}
    return out


def sensitivity_2026(recs, lam132, recorded):
    """Apply each recorded λ and the 132 OOS λ to the 2026 mixed6 preweight wins (post hoc, diagnostic)."""
    cur = [r for r in recs if dt.date.fromisoformat(r["race_date"]).year == 2026]
    ys, wins = [], []
    for r in cur:
        y, complete = labels_of(r)
        if complete:
            ys.append(y); wins.append((r["ids"], r["arms"]["mean_win_harville"][:, 0]))
    Y = np.concatenate(ys)
    cands = {"132_oos_2026": lam132[2026]}
    for k, v in recorded.items():
        cands[k] = (v["lambda2"], v["lambda3"], False)
    cands["harville_lambda1"] = (1.0, 1.0, True)
    out = {}
    for name, lam in cands.items():
        pred = np.concatenate([heads_via_production(ids, w, lam) for ids, w in wins])
        out[name] = {"lambda": lam[:2], **{h: head_metrics(pred, Y)[h] for h in ("top2", "top3")}}
    return {"n_races": len(wins), "n_horses": int(len(Y)), "by_lambda": out,
            "caveat": ("recorded production λ were fit with data through 2026-08 / 2026-09-12 (including in-sample 2026 "
                       "backfills) and are applied here to 2026 races post hoc; this is a sensitivity of the display to λ, "
                       "not an out-of-sample comparison")}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default=str(SPEC / "evidence/step1-calibration-recheck.json"))
    a = p.parse_args(argv)
    result = {"artifact_kind": "135_step1_calibration_serving_recheck", "cutoff": str(CUTOFF), "booster_fits": 0,
              "sources": {str(p): digest(p) for p in (SOURCE132 / "full-predictions.pkl", SOURCE132 / "preweight-predictions.pkl",
                                                      SOURCE132 / "full-summary.json", SOURCE132 / "preweight-summary.json")},
              "regimes": {}}
    recorded = recorded_production_lambdas()
    result["recorded_production_lambdas"] = recorded
    for regime in ("preweight", "full"):
        recs, lam, summary = load_regime(regime)
        block = {"n_races": len(recs), "equivalence": equivalence(recs, lam), "conversion_control": conversion_control(recs, lam)}
        if regime == "preweight":
            block["sensitivity_2026_preweight"] = sensitivity_2026(recs, lam, recorded)
        result["regimes"][regime] = block
        print(json.dumps({"regime": regime, "equivalent": block["equivalence"]["equivalent"],
                          "max_abs_diff": block["equivalence"]["max_abs_diff"],
                          "conversion_2026": {k: v for k, v in block["conversion_control"]["by_year"]["2026"].items() if k != "head_metrics"}},
                         ensure_ascii=False, default=str))
    result["irreproducible"] = {
        "what": "production λ is re-fit daily from the latest persisted run per race (all model versions, 2026 backfills "
                "in-sample); that population is not in any frozen input and is not rebuilt here",
        "what_is_reported_instead": "recorded production λ values (129 anchor 2026-08-24, 133 capture 2026-09-13) and their "
                                    "post-hoc sensitivity on 2026 preweight wins",
    }
    result["conclusion"] = {
        "equivalence": all(result["regimes"][r]["equivalence"]["equivalent"] for r in result["regimes"]),
        "statement": ("Given the same win vector and λ, production already computes 132's arm C. The 132 result "
                      "(C better than head-mean) validates the current production convention; it is not an additional "
                      "improvement to deploy. Differences between 132 C and production lie only in the λ fitting material "
                      "(OOS same-regime mixed6 vs latest persisted runs), which is irreproducible from frozen inputs; "
                      "the conversion rule itself is controlled above."),
    }
    result["created_at"] = dt.datetime.now(dt.UTC).isoformat()
    result["can_adopt"] = False
    write_new(a.output, result)


if __name__ == "__main__":
    main()
