"""Read-only R05 coverage correction for the frozen price diagnostic.

Adds all archive-confirmed winning payouts, never changes an existing selection.
The original 232-race subset is preserved as the R02 affected-race subset.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/roi_comprehensive_20261005/price_robustness"
ARC = ROOT / "artifacts/roi_explore/missed_20261004/R05_settlement/archived_final_odds.csv"
FILES = [OUT / "scored_pairs.parquet", OUT / "perturbed_targets.csv", OUT / "results.json", OUT / "freeze.json", ARC]
RESULT = OUT / "price_official_supplement.json"
FREEZE = OUT / "price_official_supplement.freeze.json"
B = 4000
SEED = 20261005


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def interval(rep):
    finite = rep[np.isfinite(rep)]
    return {"lo": float(np.quantile(finite, .025)) if len(finite) else None,
            "hi": float(np.quantile(finite, .975)) if len(finite) else None,
            "finite_reps": len(finite)}


def aggregate(d, masks):
    dates, di = np.unique(d.race_date.to_numpy(), return_inverse=True)
    rng = np.random.default_rng(SEED)
    draws = rng.multinomial(len(dates), np.full(len(dates), 1 / len(dates)), size=B)
    pay = d.settle_multiplier.to_numpy(float)
    won = d.won.to_numpy(bool)
    result, reps = {}, {}
    for name, mask in masks.items():
        den = np.bincount(di, weights=mask.astype(float), minlength=len(dates))
        num = np.bincount(di, weights=mask * pay, minlength=len(dates))
        ds, ps = draws @ den, draws @ num
        rep = np.divide(ps, ds, out=np.full(B, np.nan), where=ds > 0)
        reps[name] = rep
        n = int(mask.sum())
        result[name] = {"n": n, "hits": int((mask & won).sum()), "stake_yen": n * 100,
                        "payout_yen": float(pay[mask].sum() * 100),
                        "roi": float(pay[mask].mean()) if n else None, "ci95": interval(rep),
                        "n_races": int(d.loc[mask, "race_id"].nunique()),
                        "n_days": int(d.loc[mask, "race_date"].nunique())}
    parents = {"S1_price_guard": "S1", "S3_price_guard": "S3", "N1": "S3",
               "residual_EV12": "S3", "extended_EV12": "S3"}
    for name, parent in parents.items():
        if result[name]["roi"] is not None and result[parent]["roi"] is not None:
            result[name]["parent"] = parent
            result[name]["paired_roi_delta"] = result[name]["roi"] - result[parent]["roi"]
            result[name]["paired_delta_ci95"] = interval(reps[name] - reps[parent])
    return {"day_universe": len(dates), "metrics": result}


def main():
    if FREEZE.exists() or RESULT.exists():
        raise RuntimeError("Supplement already exists; refusing overwrite")
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in FILES}
    fz = {
        "date": "2026-10-05", "input_sha256": hashes, "source_sha256": sha(Path(__file__)),
        "can_adopt": False, "eligible_for_verdict": False,
        "outcome_exposure": "Original price outcomes and the preliminary corrected-coverage counts/ROI were already read. This is a coverage correction, not a new selected policy or unexposed test.",
        "selection": "Keep every frozen price policy unchanged. Common subset = races whose unique winner key matches R05 archive with positive official_payout_yen and db_won true. No filtering by outcome amount or policy return.",
        "settlement": "Use archived winner payout / 100 and zero for losers; require equality to the original frozen settlement on every retained horse.",
        "bootstrap": {"block": "race date", "reps": B, "seed": SEED,
                      "universe": "all common-subset race dates, including no-purchase days; synchronized ratios and paired differences; 95% percentile; finite replicate count recorded"},
        "preservation": "Original freeze.json/results.json/scored_pairs.parquet and all R02/R05 files remain unchanged.",
    }
    save(FREEZE, fz)
    d = pd.read_parquet(OUT / "scored_pairs.parquet")
    arc = pd.read_csv(ARC, dtype={"race_id": str, "horse_id": str})
    for x in (d, arc):
        x["race_id"] = x.race_id.astype(str)
        x["horse_id"] = x.horse_id.astype(str)
    assert not d.duplicated(["race_id", "horse_id"]).any()
    assert not arc.duplicated(["race_id", "horse_id"]).any()
    assert d.groupby("race_id").won.sum().eq(1).all()
    winners = d.loc[d.won, ["race_id", "horse_id", "horse_number", "race_date", "settle_multiplier", "official_race"]]
    aw = winners.merge(arc[["race_id", "horse_id", "horse_number", "race_date", "db_won", "official_payout_yen", "affected"]],
                       on=["race_id", "horse_id"], how="left", suffixes=("", "_arc"), validate="one_to_one")
    valid = aw.official_payout_yen.notna() & (aw.official_payout_yen > 0)
    known = aw.loc[valid].copy()
    assert (known.horse_number == known.horse_number_arc).all()
    assert (known.race_date == known.race_date_arc).all()
    assert known.db_won.eq(True).all()
    assert np.isfinite(known.official_payout_yen).all()
    np.testing.assert_allclose(known.settle_multiplier.to_numpy() * 100, known.official_payout_yen.to_numpy(), atol=1e-8, rtol=0)
    confirmed = set(known.race_id)
    off = d.race_id.isin(confirmed).to_numpy()
    assert set(d.loc[d.official_race, "race_id"]).issubset(confirmed)
    by_race = known.set_index("race_id").official_payout_yen
    official_pay = np.where(d.won, d.race_id.map(by_race).to_numpy(float) / 100, 0)
    np.testing.assert_allclose(official_pay[off], d.settle_multiplier.to_numpy()[off], atol=1e-10, rtol=0)
    pert = pd.read_csv(OUT / "perturbed_targets.csv", dtype={"horse_id": str})
    v = pert.pivot(index=["pair_id", "horse_id"], columns="mult", values="perturbed_ev").reset_index()
    aligned = d[["pair_id", "horse_id"]].merge(v, how="left", validate="one_to_one")
    assert aligned.loc[d.J_S3, [.9, 1.1]].notna().all().all()
    guard = d.J_S3.to_numpy(bool) & (aligned[.9].to_numpy() > 1.2) & (aligned[1.1].to_numpy() > 1.2)
    masks = {"S1": d.J_S1.to_numpy(bool), "S3": d.J_S3.to_numpy(bool), "N1": d.N1_J.to_numpy(bool),
             "residual_EV12": d.residual_EV12_J.to_numpy(bool), "extended_EV12": d.extended_EV12_J.to_numpy(bool),
             "S1_price_guard": guard & d.J_S1.to_numpy(bool), "S3_price_guard": guard}
    original = json.loads((OUT / "results.json").read_text())
    # Recheck whole-population point metrics while leaving the original file unchanged.
    for name, mask in masks.items():
        old = original["metrics"][name]
        assert old["n"] == int(mask.sum()) and old["hits"] == int((mask & d.won.to_numpy()).sum())
        np.testing.assert_allclose(old["roi"], d.settle_multiplier.to_numpy()[mask].mean(), atol=1e-12, rtol=0)
    subset = d.loc[off].copy().reset_index(drop=True)
    subset["settle_multiplier"] = official_pay[off]
    metrics = aggregate(subset, {k: m[off] for k, m in masks.items()})
    days = known.groupby("race_date").agg(races=("race_id", "size"), original_R02_official=("official_race", "sum"))
    result = {
        "supplement_freeze_sha256": sha(FREEZE), "can_adopt": False, "eligible_for_verdict": False,
        "population": {"all_races": int(d.race_id.nunique()), "all_horses": len(d),
                       "original_R02_official_races": int(d.loc[d.official_race, "race_id"].nunique()),
                       "all_archive_confirmed_races": len(confirmed), "additional_archive_confirmed_races": int((~known.official_race).sum()),
                       "archive_unconfirmed_races": int(d.race_id.nunique() - len(confirmed)),
                       "official_horses": len(subset), "official_days": int(subset.race_date.nunique()),
                       "date_min": subset.race_date.min(), "date_max": subset.race_date.max(),
                       "by_date": {date: {k: int(v) for k, v in rec.items()} for date, rec in days.to_dict(orient="index").items()}},
        "alignment": {"race_horse_unique": True, "unique_winner_per_race": True, "archive_winner_flag_verified": True,
                      "horse_number_and_date_match": True, "settlement_changed_horses": 0, "whole_population_points_unchanged": True},
        "original_subset_interpretation": "The 232-race original official subset contains affected=1 races only, because R02 populated archive payouts only when correcting those races. The additional 157 affected=0 races already had the same payout via stored final odds.",
        "archive_confirmed_common_subset": metrics,
        "interpretation": "Descriptive 15-day coverage correction on already-read outcomes. Very few hits; confidence intervals do not establish profitability; original policy comparisons are unchanged.",
    }
    for path in FILES:
        assert sha(path) == hashes[str(path.relative_to(ROOT))]
    result["original_inputs_and_results_hashes_unchanged"] = True
    save(RESULT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
