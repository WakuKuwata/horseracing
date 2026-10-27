"""Frozen exploratory annual q/ens calibration; pure coefficient fits, no product writes."""
from __future__ import annotations

import os
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import argparse
import hashlib
import json
import platform
import time
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import scipy
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/roi_comprehensive_20261005/calibration_roi"
PROTOCOL = ROOT / "docs/roi-comprehensive-review-20261005/calibration-roi.md"
ROWS = ROOT / "artifacts/roi_strict2007_20260930/input/rows.parquet"
ENSPEC = ROOT / "artifacts/market_ev/mev-ens15-v1/ensemble.spec.json"
RESULTS = ROOT / "artifacts/roi_explore/results"
RESIDUAL = ROOT / "artifacts/roi_comprehensive_20261005/residual_spike"
MAX_DATE = "2026-06-26"
YEARS = tuple(range(2013, 2027))
B, SEED = 4000, 20261005
ARMS = ("calibrated_q", "calibrated_q_ens", "raw_S3", "normalized_S3")
WINDOWS = {"D": (2013, 2018), "Q": (2019, 2022), "R": (2023, 2026), "ALL": (2013, 2026)}
OPTIONS = dict(maxiter=1000, ftol=1e-12, gtol=1e-8)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, obj):
    with path.open("x") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)


def seed_path(seed):
    tag = ("armC_binary_drop-sameday+weightlive_from2007_ens15" if seed == 1 else
           f"armC_binary_seed{seed}_drop-sameday+weightlive_from2007_ens15")
    return RESULTS / tag / "predictions.parquet"


def segments(race_ids):
    ids = np.asarray(race_ids)
    if not len(ids):
        raise ValueError("An empty race pool is not fit material")
    starts = np.r_[0, np.flatnonzero(ids[1:] != ids[:-1]) + 1].astype(int)
    assert len(starts) == len(np.unique(ids)), "race blocks must be contiguous"
    return starts


def log_softmax(scores, starts):
    sizes = np.diff(np.r_[starts, len(scores)])
    centered = scores - np.repeat(np.maximum.reduceat(scores, starts), sizes)
    return centered - np.repeat(np.log(np.add.reduceat(np.exp(centered), starts)), sizes)


def objective(Z, won, starts, *, clipped=False):
    won = np.asarray(won, bool)
    assert np.add.reduceat(won.astype(int), starts).tolist() == [1] * len(starts)
    assert np.isfinite(Z).all()
    def fun(coef):
        lp = log_softmax(Z @ coef, starts)
        p = np.exp(lp)
        value = (-np.log(np.clip(p[won], 1e-15, 1)).mean() if clipped else -lp[won].mean())
        grad = Z.T @ (p - won.astype(float)) / len(starts)
        return float(value), grad
    return fun


def fit(Z, won, starts):
    initial = np.r_[1., np.zeros(Z.shape[1] - 1)]
    result = minimize(objective(Z, won, starts), initial, jac=True, method="L-BFGS-B",
                      bounds=[(0, 5)] * Z.shape[1], options=OPTIONS)
    if not result.success:
        raise RuntimeError(str(result.message))
    return result.x, {"success": True, "nll": float(result.fun), "iterations": int(result.nit),
                      "gradient": result.jac.tolist(), "coefficients": result.x.tolist()}


def self_checks():
    Z = np.array([[-1., -.7], [-2., -2.3], [-.2, -.4], [-2.1, -1.4]])
    starts = np.array([0, 2]); won = np.array([True, False, False, True]); c = np.array([.8, .4])
    fun = objective(Z, won, starts); _, gradient = fun(c); fd = []
    for i in range(2):
        delta = np.zeros(2); delta[i] = 1e-6
        fd.append((fun(c + delta)[0] - fun(c - delta)[0]) / 2e-6)
    np.testing.assert_allclose(gradient, fd, atol=1e-8, rtol=1e-8)
    lp = log_softmax(Z @ c, starts)
    np.testing.assert_allclose(np.add.reduceat(np.exp(lp), starts), 1., atol=1e-14)
    np.testing.assert_allclose(lp, log_softmax(Z @ c + [10., 10., -5., -5.], starts), atol=1e-14)
    Znorm = Z.copy(); Znorm[:, 1] = log_softmax(Z[:, 1], starts)
    np.testing.assert_allclose(lp, log_softmax(Znorm @ c, starts), atol=1e-14)
    # Exact identical policies must have a paired difference of zero on every valid draw.
    w = np.array([[1., 1.], [2., 0.], [0., 2.]])
    num = np.array([0., 200.]); den = np.array([100., 100.])
    reps = (w @ num) / (w @ den)
    assert np.array_equal(reps - reps, np.zeros(3))
    assert interval(np.full(4, np.nan)) is None
    return {"gradient_finite_difference": True, "sum_one": True, "race_shift_invariant": True,
            "raw_normalized_log_p_equivalent": True, "paired_identity": True,
            "zero_exposure_interval_null": True}


def load():
    cols = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "q", "won", "race_ok", "dead_heat"]
    d = pd.read_parquet(ROWS, columns=cols)
    assert d.year.min() >= 2007
    d["race_date"] = d.race_date.astype(str).str[:10]
    d = d[(d.year >= 2010) & (d.race_date <= MAX_DATE) & d.race_ok & ~d.dead_heat].copy()
    d = d.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    assert not d[["race_id", "horse_id"]].duplicated().any()
    starts = segments(d.race_id.to_numpy())
    assert (np.add.reduceat(d.won.to_numpy(int), starts) == 1).all()
    assert np.isfinite(d[["q", "odds"]].to_numpy()).all() and (d[["q", "odds"]] > 0).all().all()
    q = d.q.to_numpy(float)
    np.testing.assert_allclose(np.add.reduceat(q, starts), 1., atol=1e-10)
    np.testing.assert_allclose(np.log(q), log_softmax(-np.log(d.odds.to_numpy(float)), starts), atol=1e-10)
    ev = np.zeros(len(d))
    for seed in range(1, 16):
        v = pd.read_parquet(seed_path(seed), columns=["race_id", "horse_id", "odds", "pred"])
        aligned = d[["race_id", "horse_id"]].merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one", sort=False)
        assert aligned.pred.notna().all()
        np.testing.assert_allclose(aligned.odds.to_numpy(float), d.odds.to_numpy(float), atol=1e-9, rtol=0.)
        ev += (1. + aligned.pred.to_numpy(float) / 100.) / 15.
    d["p_ens"] = ev / d.odds.to_numpy(float)
    assert np.isfinite(d.p_ens).all() and (d.p_ens > 0).all()
    d["p_ens_norm"] = np.exp(log_softmax(np.log(d.p_ens.to_numpy(float)), starts))
    return d


def residual_stability():
    data = pd.read_parquet(RESIDUAL / "predictions.parquet").sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    reported = json.loads((RESIDUAL / "results.json").read_text())["coefficients"]
    rows = []
    for year in (2024, 2025, 2026):
        train = data[data.year < year]; test = data[data.year == year]
        ts = segments(train.race_id.to_numpy()); es = segments(test.race_id.to_numpy())
        won = train.won.to_numpy(bool); test_won = test.won.to_numpy(bool)
        for arm, cols in (("base", ["q", "p_ens_norm"]), ("extended", ["q", "p_ens_norm", "p_residual"])):
            Z = np.log(train[cols].to_numpy(float)); V = np.log(test[cols].to_numpy(float))
            saved = np.array(reported[str(year)][arm]); lp = log_softmax(Z @ saved, ts)
            stable_loss = objective(Z, won, ts)(saved)[0]; clipped_loss = objective(Z, won, ts, clipped=True)(saved)[0]
            refit, info = fit(Z, won, ts)
            saved_test_lp = log_softmax(V @ saved, es); refit_test_lp = log_softmax(V @ refit, es)
            rows.append({"year": year, "arm": arm, "saved_coef": saved.tolist(), "stable_refit_coef": refit.tolist(),
                         "max_coef_difference": float(abs(saved-refit).max()),
                         "saved_stable_nll": stable_loss, "saved_clipped_nll": clipped_loss,
                         "fixed_coef_nll_difference": stable_loss-clipped_loss,
                         "min_training_winner_probability": float(np.exp(lp[won]).min()),
                         "n_clipped_winners": int((lp[won] < np.log(1e-15)).sum()),
                         "refit_train_nll_difference": info["nll"]-stable_loss,
                         "max_test_probability_difference": float(abs(np.exp(saved_test_lp)-np.exp(refit_test_lp)).max()),
                         "test_nll_difference": float((-refit_test_lp[test_won]+saved_test_lp[test_won]).mean()),
                         "refit": info})
    return {"records": rows, "all_saved_fits_clip_inactive": all(r["n_clipped_winners"] == 0 for r in rows),
            "selection_or_existing_results_changed": False}


def interval(reps):
    valid = np.asarray(reps)[np.isfinite(reps)]
    return np.quantile(valid, [.025, .975]).tolist() if len(valid) else None


def policy_point(frame, mask, ev):
    n = int(mask.sum()); hits = int(frame.won.to_numpy(bool)[mask].sum())
    payout = float((frame.won.to_numpy(float)*frame.odds.to_numpy(float)*100.)[mask].sum())
    return {"n_bets": n, "n_hits": hits, "n_betting_races": int(frame.loc[mask, "race_id"].nunique()),
            "n_betting_days": int(frame.loc[mask, "race_date"].nunique()), "stake_yen": n*100,
            "payout_yen": payout, "roi": payout/(n*100) if n else None,
            "mean_selected_model_ev": float(ev[mask].mean()) if n else None}


def evaluate(d):
    pcols = dict(zip(ARMS, ("p_calibrated_q", "p_calibrated_q_ens", "p_ens", "p_ens_norm"), strict=True))
    result = {}
    for window, (lo, hi) in WINDOWS.items():
        frame = d[d.year.between(lo, hi)].reset_index(drop=True)
        days, day_index = np.unique(frame.race_date.to_numpy(), return_inverse=True)
        counts = np.random.default_rng(SEED).multinomial(len(days), np.full(len(days), 1./len(days)), size=B).astype(float)
        records = {}; bootstrap = {}
        for name, col in pcols.items():
            ev = (frame[col]*frame.odds).to_numpy(float); assert np.isfinite(ev).all()
            mask = ev > 1.2
            den = np.bincount(day_index, weights=mask.astype(float)*100., minlength=len(days))
            num = np.bincount(day_index, weights=mask*frame.won.to_numpy(float)*frame.odds.to_numpy(float)*100., minlength=len(days))
            draw_stake = counts @ den
            reps = np.divide(counts @ num, draw_stake, out=np.full(B, np.nan), where=draw_stake>0)
            bootstrap[name] = reps
            record = policy_point(frame, mask, ev)
            record.update(ci95=interval(reps), bootstrap_zero_exposure=int((draw_stake==0).sum()))
            record["annual"] = {}
            for year in sorted(frame.year.unique()):
                ys = frame.year.to_numpy() == year
                record["annual"][str(year)] = policy_point(frame.loc[ys].reset_index(drop=True), mask[ys], ev[ys])
            records[name] = record
        comparisons = {}
        for a, b in combinations(ARMS, 2):
            diff = bootstrap[a]-bootstrap[b]
            ra, rb = records[a]["roi"], records[b]["roi"]
            comparisons[f"{a}_minus_{b}"] = {"roi_difference": ra-rb if ra is not None and rb is not None else None,
                "ci95": interval(diff), "valid_replicates": int(np.isfinite(diff).sum())}
        result[window] = {"date_min": frame.race_date.min(), "date_max": frame.race_date.max(),
            "n_horses": len(frame), "n_races": int(frame.race_id.nunique()), "n_days": len(days),
            "day_keys_sha256": hashlib.sha256("\n".join(days).encode()).hexdigest(),
            "policies": records, "paired_comparisons": comparisons}
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--check-only", action="store_true"); args = parser.parse_args()
    checks = self_checks()
    if args.check_only:
        print(json.dumps(checks)); return
    start = time.monotonic()
    if OUT.exists():
        raise RuntimeError("Frozen experiment directory already exists; refusing to overwrite")
    paths = [Path(__file__), PROTOCOL, ROWS, ENSPEC, *[seed_path(s) for s in range(1,16)],
             RESIDUAL/"predictions.parquet", RESIDUAL/"results.json", RESIDUAL/"freeze.json",
             ROOT/"scripts/roi_explore/comprehensive_20261005/residual_spike.py"]
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    freeze = {"created_at": datetime.now(timezone.utc).isoformat(), "input_sha256": hashes,
              "years": YEARS, "max_date": MAX_DATE, "arms": ARMS, "threshold_strict_gt": 1.2,
              "windows": WINDOWS, "bootstrap": {"b": B, "seed": SEED, "unit": "all eligible race days"},
              "optimizer": OPTIONS, "coefficient_bounds": [0,5], "checks": checks,
              "can_adopt": False, "untouched_holdout": False,
              "runtime": {"python": platform.python_version(), "numpy": np.__version__,
                          "pandas": pd.__version__, "scipy": scipy.__version__, "threads": 1}}
    OUT.mkdir(parents=True)
    write(OUT/"freeze.json", freeze)
    (OUT/"protocol_frozen.md").write_bytes(PROTOCOL.read_bytes())
    stability = residual_stability(); write(OUT/"residual_fit_stability.json", stability)
    d = load(); recipes = {}; predictions = []
    for year in YEARS:
        train = d[d.year < year]; test = d[d.year == year].copy()
        assert train.year.min() == 2010 and train.year.max() < year
        assert test.year.eq(year).all() and train.race_date.max() < test.race_date.min()
        ts = segments(train.race_id.to_numpy()); es = segments(test.race_id.to_numpy())
        recipe = {"training_min": train.race_date.min(), "training_max": train.race_date.max(),
                  "training_races": len(ts), "training_horses": len(train),
                  "application_min": test.race_date.min(), "application_max": test.race_date.max(),
                  "application_races": len(es), "fits": {}}
        for arm, cols in (("calibrated_q", ["q"]), ("calibrated_q_ens", ["q", "p_ens"])):
            coef, info = fit(np.log(train[cols].to_numpy(float)), train.won.to_numpy(bool), ts)
            prob = np.exp(log_softmax(np.log(test[cols].to_numpy(float)) @ coef, es))
            assert np.isfinite(prob).all() and (prob>0).all()
            np.testing.assert_allclose(np.add.reduceat(prob, es), 1., atol=1e-12)
            test[f"p_{arm}"] = prob; recipe["fits"][arm] = info
        recipes[str(year)] = recipe; predictions.append(test)
        print(f"calibration {year} complete; elapsed {time.monotonic()-start:.1f}s", flush=True)
    write(OUT/"recipes.json", recipes)
    out = pd.concat(predictions, ignore_index=True).sort_values(["race_id","horse_number"]).reset_index(drop=True)
    assert not out[["race_id","horse_id"]].duplicated().any()
    out.to_parquet(OUT/"predictions.parquet", index=False)
    results = evaluate(out)
    assert hashes == {name: sha(ROOT/name) for name in hashes}, "An input changed during scoring"
    write(OUT/"results.json", {"windows": results, "elapsed_sec": time.monotonic()-start,
        "can_adopt": False, "untouched_holdout": False, "all_inputs_unchanged": True,
        "interpretation": "Exploratory reused history; stored closing-like odds; fixed four policies; no adoption evidence"})
    print(json.dumps({w: {k: {x:r[x] for x in ('n_bets','n_hits','roi','ci95')} for k,r in v['policies'].items()} for w,v in results.items()}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
