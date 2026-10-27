"""Past-only selection thresholds for R10 V1; exploratory, never an adoption gate.

Run freeze, then run as separate invocations. Existing research files are read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/roi_comprehensive_20261005/online_normalization"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
PRIOR = ROOT / "artifacts/roi_explore/missed_20261004/R10_norm_meta/prereg.json"
TEST = ROOT / "scripts/tests/test_online_normalization_20261005.py"
TAGS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
PREDS = {s: ROOT / "artifacts/roi_explore/results" / tag / "predictions.parquet" for s, tag in TAGS.items()}
WINDOWS = {"ALL": [2013, 2026], "D": [2013, 2018], "Q": [2019, 2022],
           "R": [2023, 2026], "POST": [2019, 2026]}
ARMS = ["N1", "N2", "N3", "N4"]
B = 4000
SEED = 20261005
CUTOFF = "2026-06-26"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def input_hashes() -> dict:
    return {str(p.relative_to(ROOT)): sha(p) for p in [ROWS, PRIOR, *PREDS.values()]}


def freeze() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "protocol.json"
    if target.exists():
        raise RuntimeError("Protocol already exists; refusing to overwrite")
    hashes = input_hashes()
    prior = json.loads(PRIOR.read_text())
    assert hashes[str(ROWS.relative_to(ROOT))] == prior["inputs"]["rows"]["sha256"]
    for seed, path in PREDS.items():
        assert hashes[str(path.relative_to(ROOT))] == prior["inputs"]["ens15_predictions"]["sha256"][str(seed)]
    protocol = {
        "study": "R10 V1 online selection supplement", "date": "2026-10-05",
        "exploratory": True, "outcomes_previously_seen": True, "can_adopt": False,
        "prior_exposure": "R10 and earlier ROI reports were read; these windows are reused, not untouched confirmation.",
        "input_sha256": hashes,
        "source_sha256": {str(Path(__file__).relative_to(ROOT)): sha(Path(__file__)),
                          str(TEST.relative_to(ROOT)): sha(TEST)},
        "population": "rows_2007 race_ok & ~dead_heat, 2010-01-01 through 2026-06-26; started horses; whole races; no new DB reads",
        "apply_years": list(range(2013, 2027)), "windows": WINDOWS, "date_max": CUTOFF,
        "scores": {"EV_s": "1+pred_s/100", "p_s": "EV_s/stored odds", "p_bar": "mean 15 p_s",
                   "EV_ens": "mean 15 EV_s", "EV_norm": "p_bar / sum_race(p_bar) * stored odds"},
        "baseline": "S3 = EV_ens > 1.2 over all population horses; no price cap or interval guard",
        "arms": {
            "N1": "all horses, EV_norm > 1.2",
            "N2": "parent EV_ens > 1, EV_norm > 1.2",
            "N3": "all horses; prior-year-only S3 count / prior-year all-horse count = r; threshold = prior-year EV_norm quantile(1-r, linear); current EV_norm >= threshold",
            "N4": "parent EV_ens > 1; prior-year S3 count / prior-year parent count = r; threshold = prior-year parent EV_norm quantile(1-r, linear); current parent EV_norm >= threshold"},
        "threshold_policy": "Use previous year's saved annual OOF predictions (not current-year model on past data). Exactly previous calendar year, no pooling or tuning. If prior parent is empty or S3 count zero, threshold unavailable and no bets; if r=1, minimum score. Ties included with >=. Never re-match current-year counts.",
        "stake": "100 yen on every selected horse; simultaneous horses all selected",
        "settlement": "won * stored odds * 100; payout approximation, not historical official-payout verification. R05 contaminated tail excluded by 2026-06-26 cutoff.",
        "bootstrap": {"reps": B, "seed": SEED, "block": "race date",
                      "day_universe": "all eligible race dates in each window, including days with zero bets in both compared arms",
                      "method": "same sampled dates for baseline and all arms; each arm's total payout / total stake; paired ROI difference vs S3; 2.5/97.5 percentile intervals",
                      "zero_stake_replicate": "exclude nonfinite replicate and report finite count"},
        "report": "all four arms, baseline, ALL/D/Q/R/POST and all years; thresholds and tie/count drift; n/hits/races/days/stake/payout/ROI, per-race selection counts, normalized probability sum parity; no winning-branch selection",
        "inference": "exploratory descriptive intervals, unadjusted for prior outcome exposure and multiple arms; no profitability verdict or Holm claim",
        "stress": "top 5 winning payouts set to zero while original stakes retained, descriptive only",
    }
    save(target, protocol)
    (OUT / "protocol.sha256").write_text(sha(target) + "\n")
    print(json.dumps({"frozen": str(target), "sha256": sha(target)}), flush=True)


def select_masks(years, ev, norm, apply_years):
    years, ev, norm = np.asarray(years), np.asarray(ev), np.asarray(norm)
    parent = ev > 1.0
    base = ev > 1.2
    applied = np.isin(years, apply_years)
    masks = {"S3": base & applied, "N1": (norm > 1.2) & applied,
             "N2": parent & (norm > 1.2) & applied,
             "N3": np.zeros(years.size, bool), "N4": np.zeros(years.size, bool)}
    thresholds = []
    for year in apply_years:
        for name, eligible in [("N3", np.ones(years.size, bool)), ("N4", parent)]:
            prev = (years == year - 1) & eligible
            cur = (years == year) & eligible
            n_prev = int(prev.sum())
            s3_prev = int((prev & base).sum())
            ratio = s3_prev / n_prev if n_prev else None
            threshold = float(np.quantile(norm[prev], 1 - ratio, method="linear")) if n_prev and s3_prev else None
            if threshold is not None:
                masks[name] |= cur & (norm >= threshold)
            thresholds.append({"arm": name, "apply_year": int(year), "source_year": int(year - 1),
                               "prior_candidates": n_prev, "prior_S3": s3_prev, "prior_S3_ratio": ratio,
                               "threshold": threshold,
                               "prior_selected_by_threshold": int((prev & (norm >= threshold)).sum()) if threshold is not None else 0,
                               "prior_ties_at_threshold": int((prev & (norm == threshold)).sum()) if threshold is not None else 0,
                               "current_candidates": int(cur.sum()), "current_selected": int((masks[name] & cur).sum()),
                               "current_S3": int((cur & base).sum()),
                               "current_ties_at_threshold": int((cur & (norm == threshold)).sum()) if threshold is not None else 0})
    return masks, thresholds


def load_data():
    cols = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "race_ok", "dead_heat"]
    raw = pd.read_parquet(ROWS, columns=cols)
    in_date = raw.race_date.between("2010-01-01", CUTOFF)
    exclusion = {"raw_rows": len(raw), "outside_date_rows": int((~in_date).sum()),
                 "bad_race_rows_in_date": int((in_date & ~raw.race_ok).sum()),
                 "dead_heat_rows_in_date": int((in_date & raw.dead_heat).sum()),
                 "dead_heat_races_in_date": int(raw.loc[in_date & raw.dead_heat, "race_id"].nunique())}
    d = raw[in_date & raw.race_ok & ~raw.dead_heat].copy().reset_index(drop=True)
    keys = ["race_id", "horse_id"]
    parity = []
    evs = []
    canonical = None
    for seed, path in PREDS.items():
        p = pd.read_parquet(path)
        assert not p.duplicated(keys).any()
        keyhash = hashlib.sha256(pd.util.hash_pandas_object(p[keys], index=False).values.tobytes()).hexdigest()
        if canonical is None:
            canonical = p[keys + ["odds", "ret", "race_date", "year"]].copy()
        else:
            pd.testing.assert_frame_equal(p[keys + ["odds", "ret", "race_date", "year"]], canonical)
        merged = d[keys + ["odds", "race_date", "year"]].merge(p, on=keys, how="left", validate="one_to_one", suffixes=("", "_pred"))
        assert merged.pred.notna().all()
        assert np.array_equal(merged.odds, merged.odds_pred)
        assert np.array_equal(merged.race_date, merged.race_date_pred)
        assert np.array_equal(merged.year, merged.year_pred)
        evs.append(1.0 + merged.pred.to_numpy(float) / 100.0)
        parity.append({"seed": seed, "full_prediction_rows": len(p), "selected_rows": len(merged),
                       "ordered_key_sha256": keyhash, "common_metadata_exact": True})
    evs = np.asarray(evs)
    ev = evs.mean(axis=0)
    odds = d.odds.to_numpy(float)
    pb = (evs / odds).mean(axis=0)
    err = float(np.max(np.abs(pb * odds - ev)))
    assert err < 1e-12 and np.isfinite(pb).all() and (pb > 0).all()
    ridx, race_ids = pd.factorize(d.race_id, sort=True)
    race_n = np.bincount(ridx)
    assert (race_n >= 2).all()
    assert (np.bincount(ridx, weights=d.won.to_numpy(int)) == 1).all()
    sum_p = np.bincount(ridx, weights=pb)
    normp = pb / sum_p[ridx]
    norm = normp * odds
    norm_sum = np.bincount(ridx, weights=normp)
    assert np.max(np.abs(norm_sum - 1)) < 1e-12
    d["EV_ens"], d["EV_norm"] = ev, norm
    d["payout"] = d.won.to_numpy(float) * odds * 100
    quant = [.01, .05, .5, .95, .99]
    audit = {"exclusions": exclusion, "rows": len(d), "races": len(race_ids), "days": d.race_date.nunique(),
             "date_min": d.race_date.min(), "date_max": d.race_date.max(), "seed_parity": parity,
             "EV_mean_probability_formula_max_abs_error": err,
             "raw_probability_sum_quantiles": {str(q): float(np.quantile(sum_p, q)) for q in quant},
             "normalized_probability_sum_max_abs_error": float(np.max(np.abs(norm_sum - 1)))}
    return d, audit


def stats(d, mask):
    n = int(mask.sum())
    x = d.loc[mask]
    payout = float(x.payout.sum())
    winners = x.payout[x.payout > 0].to_numpy()
    per_race = x.groupby("race_id").size()
    return {"n_bets": n, "n_hits": int(x.won.sum()), "n_races": int(x.race_id.nunique()),
            "n_days": int(x.race_date.nunique()), "stake": n * 100, "payout": payout,
            "roi": payout / (n * 100) if n else None,
            "max_winning_payout_share": float(winners.max() / payout) if payout else None,
            "roi_zero_top5_payouts_stakes_retained": float((payout - np.sort(winners)[-5:].sum()) / (n * 100)) if n else None,
            "selected_per_race_histogram": {str(int(k)): int(v) for k, v in per_race.value_counts().sort_index().items()}}


def ratio_samples(day_stakes, day_payouts, draws):
    st = draws @ day_stakes
    pa = draws @ day_payouts
    return np.divide(pa, st, out=np.full(pa.shape, np.nan), where=st > 0)


def ci(a):
    finite = a[np.isfinite(a)]
    return {"lo": float(np.quantile(finite, .025)) if finite.size else None,
            "hi": float(np.quantile(finite, .975)) if finite.size else None,
            "finite_reps": int(finite.size)}


def evidence(d, masks, window_mask, seed):
    names = ["S3", *ARMS]
    dates = sorted(d.loc[window_mask, "race_date"].unique())
    idx = pd.Categorical(d.race_date, categories=dates).codes
    day_st = np.zeros((len(dates), len(names)))
    day_pa = np.zeros_like(day_st)
    output = {}
    for k, name in enumerate(names):
        m = window_mask & masks[name]
        day_st[:, k] = np.bincount(idx[m], weights=np.full(m.sum(), 100), minlength=len(dates))
        day_pa[:, k] = np.bincount(idx[m], weights=d.payout.to_numpy()[m], minlength=len(dates))
        output[name] = stats(d, m)
    rng = np.random.default_rng(seed)
    reps = []
    for start in range(0, B, 200):
        draws = rng.multinomial(len(dates), np.full(len(dates), 1 / len(dates)), size=min(200, B - start))
        reps.append(ratio_samples(day_st, day_pa, draws))
    reps = np.vstack(reps)
    for k, name in enumerate(names):
        output[name]["ci95"] = ci(reps[:, k])
        if name != "S3":
            output[name]["delta_roi_vs_S3"] = output[name]["roi"] - output["S3"]["roi"] if output[name]["roi"] is not None and output["S3"]["roi"] is not None else None
            output[name]["delta_ci95_vs_S3"] = ci(reps[:, k] - reps[:, 0])
            base, arm = masks["S3"] & window_mask, masks[name] & window_mask
            output[name]["overlap_bets_with_S3"] = int((base & arm).sum())
            output[name]["added_bets"] = int((arm & ~base).sum())
            output[name]["removed_bets"] = int((base & ~arm).sum())
    return {"bootstrap_day_universe": len(dates), "arms": output}


def run():
    target = OUT / "protocol.json"
    assert target.exists(), "Run freeze first"
    assert sha(target) == (OUT / "protocol.sha256").read_text().strip()
    p = json.loads(target.read_text())
    assert p["input_sha256"] == input_hashes()
    for name, digest in p["source_sha256"].items():
        assert sha(ROOT / name) == digest
    if (OUT / "results.json").exists():
        raise RuntimeError("Results already exist; refusing overwrite")
    d, audit = load_data()
    masks, thresholds = select_masks(d.year.to_numpy(), d.EV_ens.to_numpy(), d.EV_norm.to_numpy(), list(range(2013, 2027)))
    save(OUT / "thresholds.json", thresholds)
    save(OUT / "data_audit.json", audit)
    results = {"protocol_sha256": sha(target), "exploratory": True, "can_adopt": False,
               "settlement": p["settlement"], "windows": {}}
    for k, (name, (lo, hi)) in enumerate(WINDOWS.items()):
        wm = d.year.between(lo, hi).to_numpy()
        results["windows"][name] = evidence(d, masks, wm, SEED + k)
        print(json.dumps({"window": name, "roi": {a: results["windows"][name]["arms"][a]["roi"] for a in ["S3", *ARMS]}}), flush=True)
    years = []
    for year in range(2013, 2027):
        for name, mask in masks.items():
            rec = stats(d, mask & (d.year.to_numpy() == year))
            rec.pop("selected_per_race_histogram")
            years.append({"year": year, "arm": name, **rec})
    pd.DataFrame(years).to_csv(OUT / "per_year.csv", index=False)
    save(OUT / "results.json", results)
    assert p["input_sha256"] == input_hashes()
    save(OUT / "completion.json", {"input_hashes_unchanged": True, "protocol_sha256": sha(target),
                                   "results_sha256": sha(OUT / "results.json"), "all_four_arms_reported": True})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["freeze", "run"])
    args = parser.parse_args()
    freeze() if args.action == "freeze" else run()
