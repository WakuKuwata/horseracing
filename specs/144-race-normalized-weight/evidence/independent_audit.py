"""Independent audit of 144; no experiment implementation imports or model fitting.

Selection/scoring helpers come from the frozen independent 143 auditor, not the
experiment's reporting code. Weight decomposition and four paired contrasts are
computed here separately from the 144 implementation.
"""
from __future__ import annotations

import os
for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[key] = "1"

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/144-race-normalized-weight"
OLD142 = ROOT / "artifacts/142-roi-policy-model-improvement"
OLD143 = ROOT / "artifacts/143-existing-feature-roi"
KEYS = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "field_size"]
ARMS = ("baseline", "weighted_baseline", "within_race", "race_only")
CONTRASTS = (("within_race", "baseline"), ("within_race", "weighted_baseline"),
             ("race_only", "baseline"), ("race_only", "weighted_baseline"))
helper_path = ROOT / "specs/143-existing-feature-roi/evidence/independent_audit.py"
spec = importlib.util.spec_from_file_location("independent143_audit_helpers", helper_path)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
sha, read, equal = helpers.sha, helpers.read, helpers.equal


def folder(arm, year, seed):
    if arm == "baseline":
        return OLD142 / f"models/seed_{seed:02d}/year_{year}/baseline"
    if arm == "weighted_baseline":
        return OLD143 / f"weighted_models/seed_{seed:02d}/year_{year}/weighted_baseline"
    return OUT / f"models/seed_{seed:02d}/year_{year}/{arm}"


def ensemble(arm, protocol, counter):
    pieces = []
    params = read(OLD143 / "protocol.json")["models"]["params"]
    features = read(OLD143 / "protocol.json")["base_features"]
    for year in range(2019, 2027):
        first, total = None, None
        for seed in range(1, 16):
            path = folder(arm, year, seed)
            rec = read(path / "receipt.json")
            recipe = rec["recipe"]
            assert recipe["seed"] == seed and recipe["rounds"] == 300
            assert recipe["train_end"] < recipe["target_start"]
            assert recipe["train_start"].startswith("2007-")
            assert rec["actual_iterations"] == 300
            assert rec["roundtrip_max_abs_raw_difference"] <= 1e-12
            for name, digest in rec["hashes"].items():
                assert sha(path / name) == digest, (arm, year, seed, name)
                counter["artifact_hashes"] += 1
            fs = read(path / "feature_spec.json")
            order = fs.get("feature_order", fs["features"] + fs["cats"])
            assert order == features
            expected = dict(params, seed=seed, metric="binary_logloss")
            if arm != "baseline":
                expected["objective"] = "binary"
            assert fs["params"] == expected
            assert fs["trained_for_year"] == year and fs["train_through"] == year - 1
            assert fs["training_cutoff"] == recipe["train_end"]
            if arm in ("within_race", "race_only"):
                assert recipe["arm"] == "weighted_baseline"
                assert recipe["identity"]["research_arm"] == arm
                assert recipe["identity"]["protocol_sha256"] == sha(OUT / "protocol.json")
                assert recipe["features"] == features
                assert recipe["params"] == expected
            frame = pd.read_parquet(path / "predictions.parquet")
            assert frame.year.eq(year).all() and len(frame) == recipe["target_rows"]
            if first is None:
                first = frame[KEYS].copy()
                total = np.zeros(len(frame))
            else:
                assert first.equals(frame[KEYS])
            total += frame.p.to_numpy(float)
            counter["fits"] += 1
        first["p"] = total / 15
        pieces.append(first)
    return pd.concat(pieces, ignore_index=True)


def independent_weights():
    """Reconstruct original143 then a and b without calling any fit/weight code."""
    p143 = read(OLD143 / "protocol.json")
    teacher = pd.read_parquet(ROOT / p143["weights"])
    source = pd.read_parquet(OLD142 / "model_comparison/baseline_ensemble.parquet")
    source = source.loc[source.year.le(2025)].reset_index(drop=True)
    assert teacher[["race_id", "horse_id"]].equals(source[["race_id", "horse_id"]])
    np.testing.assert_array_equal(teacher.p_oof.to_numpy(), source.p.to_numpy())
    assert teacher.year.between(2019, 2025).all()
    assert (teacher.oof_train_through == teacher.year - 1).all()
    columns = ["race_id", "horse_id", "horse_number", "year", "odds", "race_date", "race_ok", "dead_heat", "field_size"]
    pool = pd.read_parquet(ROOT / p143["rows"], columns=columns,
                           filters=[("race_date", ">=", "2007-01-01"), ("race_date", "<=", "2026-06-26")])
    ok = (pool.race_ok & ~pool.dead_heat).groupby(pool.race_id).transform("all")
    pool = pool.loc[ok & pool.race_id.ne("202504040407")].sort_values(["race_id", "horse_number"])
    results = []
    for year in range(2019, 2027):
        train = pool.loc[pool.year.lt(year)].reset_index(drop=True)
        np.testing.assert_array_equal(train.groupby("race_id").size().to_numpy(),
                                      train.groupby("race_id").field_size.first().to_numpy())
        joined = train.merge(teacher[["race_id", "horse_id", "p_oof", "oof_train_through"]],
                             on=["race_id", "horse_id"], how="left", sort=False, validate="one_to_one")
        assert joined[["race_id", "horse_id"]].equals(train[["race_id", "horse_id"]])
        present = joined.p_oof.notna()
        assert (present == joined.oof_train_through.notna()).all()
        assert (joined.loc[present, "oof_train_through"] < joined.loc[present, "year"]).all()
        informative = present.groupby(joined.race_id).transform("all").to_numpy()
        raw = np.ones(len(train))
        ev = joined.loc[informative, "p_oof"].to_numpy() * joined.loc[informative, "odds"].to_numpy()
        raw[informative] += np.exp(-0.5 * ((ev - 1.2) / 0.2) ** 2)
        w = raw.copy()
        if informative.any():
            w[informative] /= w[informative].mean()
        ids = joined.race_id.to_numpy()
        starts = np.r_[0, np.flatnonzero(ids[1:] != ids[:-1]) + 1]
        counts = np.diff(np.r_[starts, len(ids)])
        # Reproduce the declared ordered floating-point arithmetic for SHA,
        # and independently check its meaning with a groupby calculation.
        a = np.repeat(np.add.reduceat(w, starts) / counts, counts)
        semantic_a = pd.Series(w).groupby(joined.race_id).transform("mean").to_numpy()
        np.testing.assert_allclose(a, semantic_a, atol=5e-16, rtol=0)
        b = w / a
        expected = {"weighted_baseline": w, "race_only": a, "within_race": b}
        np.testing.assert_allclose(a * b, w, atol=1e-15, rtol=1e-15)
        np.testing.assert_allclose(pd.Series(b).groupby(joined.race_id).mean(), 1, atol=5e-16, rtol=0)
        assert pd.Series(a).groupby(joined.race_id).nunique().eq(1).all()
        checks = {}
        for arm, vector in expected.items():
            assert np.isfinite(vector).all() and (vector > 0).all()
            np.testing.assert_array_equal(vector[~informative], np.ones((~informative).sum()))
            assert abs(vector.mean() - 1) < 5e-15
            digest = hashlib.sha256(vector.tobytes()).hexdigest()
            for seed in range(1, 16):
                rec = read(folder(arm, year, seed) / "receipt.json")
                assert rec["recipe"]["weights"]["vector_sha256"] == digest, (arm, year, seed)
                if year == 2019:
                    base = pd.read_parquet(folder("baseline", year, seed) / "predictions.parquet")
                    current = pd.read_parquet(folder(arm, year, seed) / "predictions.parquet")
                    assert base[KEYS].equals(current[KEYS])
                    np.testing.assert_array_equal(base.p.to_numpy(), current.p.to_numpy())
            checks[arm] = dict(vector_sha256=digest, mean=float(vector.mean()),
                               minimum=float(vector.min()), maximum=float(vector.max()))
        results.append(dict(year=year, training_rows=len(train), informative_rows=int(informative.sum()),
                            neutral_rows=int((~informative).sum()), weights=checks))
    return results


def bootstrap(tables):
    dates = sorted(tables["baseline"].race_date.unique())
    draws = np.random.default_rng(20261024).multinomial(len(dates), np.full(len(dates), 1 / len(dates)), size=4000)
    daily = {arm: frame.groupby("race_date").agg(stake=("stake_yen", "sum"), payout=("payout_yen", "sum"),
             budget=("budget_yen", "sum"), profit=("profit_yen", "sum"), nll=("winner_nll", "sum"),
             races=("race_id", "size")).reindex(dates) for arm, frame in tables.items()}
    def interval(point, samples):
        good = np.asarray(samples)[np.isfinite(samples)]
        return dict(difference=float(point), ci95=np.quantile(good, [.025, .975]).tolist(),
                    bonferroni_familywise95_ci=np.quantile(good, [.05 / 8, 1 - .05 / 8]).tolist(),
                    valid_replicates=len(good))
    results = {}
    for newarm, oldarm in CONTRASTS:
        old, new = daily[oldarm], daily[newarm]
        old_roi = (draws @ old.payout) / (draws @ old.stake)
        new_roi = (draws @ new.payout) / (draws @ new.stake)
        results[f"{newarm}_minus_{oldarm}"] = dict(
            winner_nll=interval((new.nll-old.nll).sum()/old.races.sum(), draws @ (new.nll-old.nll)/(draws @ old.races)),
            profit_per_budget=interval((new.profit-old.profit).sum()/old.budget.sum(), draws @ (new.profit-old.profit)/(draws @ old.budget)),
            deployed_roi=interval(new.payout.sum()/new.stake.sum()-old.payout.sum()/old.stake.sum(), new_roi-old_roi))
    return results


def sensitivity(tables):
    result = {}
    for newarm, oldarm in CONTRASTS:
        old = tables[oldarm].set_index("race_id")
        new = tables[newarm].set_index("race_id")
        assert old.index.equals(new.index)
        delta = new.profit_yen-old.profit_yen
        years = old.race_date.str[:4].astype(int)
        calendar = old.race_date.str[5:].le("06-21")
        target = "202608030111"
        result[f"{newarm}_minus_{oldarm}"] = dict(
            full_profit_difference_yen=float(delta.sum()),
            year2026_profit_difference_yen=float(delta.loc[years.eq(2026)].sum()),
            known_race_difference_yen=float(delta.loc[target]),
            excluding_known_race_full_yen=float(delta.drop(target).sum()),
            excluding_known_race_2026_yen=float(delta.loc[years.eq(2026)].drop(target).sum()),
            same_calendar_by_year_yen={str(y): float(delta.loc[years.eq(y) & calendar].sum()) for y in range(2019, 2027)},
            largest_positive_race_differences=delta.nlargest(5).to_dict(),
            largest_negative_race_differences=delta.nsmallest(5).to_dict())
    return result


def replay():
    checks = []
    for year in (2019, 2024, 2026):
        for arm in ("within_race", "race_only"):
            for seed in (1, 8, 15):
                path = folder(arm, year, seed)
                fs = read(path / "feature_spec.json")
                pred = pd.read_parquet(path / "predictions.parquet").iloc[:64]
                cols = list(dict.fromkeys(["race_id", "horse_id"] + fs["features"] + fs["cats"]))
                original = pd.read_parquet(ROOT / read(OLD143 / "protocol.json")["rows"], columns=cols,
                                           filters=[("year", "=", year)]).set_index(["race_id", "horse_id"])
                selected = original.loc[pd.MultiIndex.from_frame(pred[["race_id", "horse_id"]])]
                matrix = np.column_stack([selected[fs["features"]].to_numpy(np.float32)] + [
                    selected[c].astype("string").map(fs["cat_maps"][c]).to_numpy(np.float32, na_value=np.nan) for c in fs["cats"]])
                booster = lgb.Booster(model_file=str(path / "model.txt"))
                assert booster.feature_name() == fs["feature_order"]
                actual = expit(booster.predict(matrix, raw_score=True, num_threads=1))
                np.testing.assert_array_equal(actual, pred.p.to_numpy())
                checks.append(dict(arm=arm, year=year, seed=seed, rows=64, max_abs_p_difference=0.))
    return checks


def audit():
    protocol = read(OUT / "protocol.json")
    assert sha(OUT / "protocol.json") == (OUT / "protocol.sha256").read_text().strip()
    for name, digest in protocol["inputs_and_source_sha256"].items():
        assert sha(ROOT / name) == digest, name
    counter = dict(fits=0, artifact_hashes=0)
    frames = {arm: ensemble(arm, protocol, counter) for arm in ARMS}
    current = pd.read_parquet(OLD142 / "existing_policies/inputs.parquet")
    frames["current_reference"] = current.loc[current.year.ge(2019), KEYS + ["p_raw"]].rename(columns={"p_raw": "p"})
    folder_report = OUT / "comparison"
    saved = read(folder_report / "summary.json")
    tables = {}
    for arm, frame in frames.items():
        base = frames["baseline"]
        assert base[KEYS].reset_index(drop=True).equals(frame[KEYS].sort_values(["race_id", "horse_number"]).reset_index(drop=True))
        stored = pd.read_parquet(folder_report / f"{arm}_ensemble.parquet")
        assert frame.sort_values(["race_id", "horse_number"]).reset_index(drop=True).equals(stored.sort_values(["race_id", "horse_number"]).reset_index(drop=True))
        scored, races, bets = helpers.score(frame)
        equal(scored, saved["arms"][arm], arm)
        stored_races = pd.read_parquet(folder_report / f"{arm}_races.parquet")
        assert races[["race_date", "race_id"]].equals(stored_races[["race_date", "race_id"]])
        for column in ("stake_yen", "payout_yen", "profit_yen", "budget_yen", "winner_nll"):
            np.testing.assert_allclose(races[column], stored_races[column], atol=1e-12, rtol=1e-12)
        stored_bets = pd.read_parquet(folder_report / f"{arm}_bets.parquet")
        assert bets[["race_id", "horse_id"]].reset_index(drop=True).equals(stored_bets[["race_id", "horse_id"]].reset_index(drop=True))
        np.testing.assert_array_equal(bets.units.to_numpy(), stored_bets.units.to_numpy())
        tables[arm] = races
    paired = bootstrap(tables)
    equal(paired, saved["paired"]["results"], "paired")
    diagnostic = sensitivity(tables)
    for contrast, values in diagnostic.items():
        strongest = next(iter(values["largest_positive_race_differences"]))
        largest_delta = values["largest_positive_race_differences"][strongest]
        equal(dict(known_race_delta_yen=values["known_race_difference_yen"],
                   without_known_race_delta_yen=values["excluding_known_race_full_yen"],
                   top_difference_race=strongest, top_difference_yen=largest_delta,
                   without_top_difference_yen=values["full_profit_difference_yen"]-largest_delta,
                   same_calendar_jan1_jun21_by_year=values["same_calendar_by_year_yen"],
                   before2026_delta_yen=values["full_profit_difference_yen"]-values["year2026_profit_difference_yen"],
                   year2026_delta_yen=values["year2026_profit_difference_yen"]),
              saved["sensitivity"][contrast], f"sensitivity.{contrast}")
    result = dict(auditor="raceweight144_review", protocol_sha256=sha(OUT / "protocol.json"),
                  summary_sha256=sha(folder_report / "summary.json"), counters=counter,
                  independent_weights=independent_weights(), independent_saved_body_replays=replay(),
                  paired=paired, sensitivity=diagnostic,
                  no_model_fits_performed=True, can_adopt=False, untouched_holdout=False, blockers=[])
    result["audit_script_sha256"] = sha(__file__)
    result["independent143_helpers_sha256"] = sha(helper_path)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        helpers.self_test()
    else:
        result = audit()
        output = Path(__file__).with_name("independent_final.json")
        with output.open("x") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        print(json.dumps(dict(evidence=str(output), counters=result["counters"], blockers=[])))
