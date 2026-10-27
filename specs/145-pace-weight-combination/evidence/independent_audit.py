"""Independent 145 audit: no experiment imports, fitting or artifact mutations.

The independently implemented 143 selection/scoring audit is reused. New-arm
identity, weights, reconstruction, bootstrap and body replay are checked here.
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
import platform

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/145-pace-weight-combination"
OLD142 = ROOT / "artifacts/142-roi-policy-model-improvement"
OLD143 = ROOT / "artifacts/143-existing-feature-roi"
KEYS = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "field_size"]
ARMS = ("baseline", "weighted_baseline", "pace", "weighted_pace")
CONTRASTS = tuple(("weighted_pace", arm) for arm in ARMS[:-1])
HELPER_PATH = ROOT / "specs/143-existing-feature-roi/evidence/independent_audit.py"
spec = importlib.util.spec_from_file_location("independent143_helpers_for145", HELPER_PATH)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
sha, read, equal = helpers.sha, helpers.read, helpers.equal


def folder(arm, year, seed):
    if arm == "baseline":
        return OLD142 / f"models/seed_{seed:02d}/year_{year}/baseline"
    if arm == "weighted_baseline":
        return OLD143 / f"weighted_models/seed_{seed:02d}/year_{year}/weighted_baseline"
    if arm == "pace":
        return OLD143 / f"models/seed_{seed:02d}/year_{year}/pace"
    return OUT / f"models/seed_{seed:02d}/year_{year}/{arm}"


def new_ensemble(protocol, counter):
    old = read(OLD143 / "protocol.json")
    expected_identity = dict(version=1, protocol_sha256=sha(OUT / "protocol.json"),
        rows_sha256=sha(ROOT / protocol["rows"]), additions_sha256=sha(ROOT / protocol["additions"]),
        weights_sha256=sha(ROOT / protocol["weights"]), arms=["weighted_pace"], bundles=old["bundles"],
        kernel_arm="weighted_pace", source_sha256=sha(ROOT / "scripts/roi_explore/roi_improvement_145/models.py"),
        dependencies={name: sha(ROOT / relative) for name, relative in
            (("models143", "scripts/roi_explore/roi_improvement_143/models.py"),
             ("models142", "scripts/roi_explore/roi_improvement_142/models.py"),
             ("legacy", "scripts/roi_explore/direct_return_model.py"))},
        python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, lightgbm=lgb.__version__,
        research_only=True, can_adopt=False, untouched_holdout=False)
    expected_features = old["base_features"][:-11] + old["bundles"]["pace"] + old["base_features"][-11:]
    assert len(expected_features) == 91 and len(set(expected_features)) == 91
    pieces = []
    for year in range(2019, 2027):
        first, total = None, None
        for seed in range(1, 16):
            path = folder("weighted_pace", year, seed)
            rec = read(path / "receipt.json")
            recipe = rec["recipe"]
            identity = recipe["identity"]
            assert recipe["seed"] == seed and recipe["rounds"] == 300
            assert recipe["train_end"] < recipe["target_start"]
            assert recipe["train_start"].startswith("2007-") and rec["actual_iterations"] == 300
            assert rec["roundtrip_max_abs_raw_difference"] <= 1e-12
            equal(dict(expected_identity, year=year, seed=seed), identity, f"identity.{year}.{seed}")
            assert recipe["arm"] == "weighted_pace"
            assert recipe["features"] == expected_features
            params = dict(old["models"]["params"], seed=seed, objective="binary", metric="binary_logloss")
            assert recipe["params"] == params
            assert recipe["weights"] is not None
            assert set(rec["hashes"]) == {"model.txt", "predictions.parquet", "feature_spec.json"}
            for name, digest in rec["hashes"].items():
                assert sha(path / name) == digest, (year, seed, name)
                counter["artifact_hashes"] += 1
            fs = read(path / "feature_spec.json")
            assert fs["feature_order"] == expected_features and fs["params"] == params
            assert fs["features"] == expected_features[:-11] and fs["cats"] == expected_features[-11:]
            assert fs["trained_for_year"] == year and fs["train_through"] == year - 1
            assert fs["training_cutoff"] == recipe["train_end"]
            assert read(path.parent / "identity.json") == identity
            evaluation = read(path.parent / "evaluation.json")
            assert evaluation["identity"] == identity
            assert evaluation["year"] == year and evaluation["seed"] == seed
            assert set(evaluation["arms"]) == {"weighted_pace"}
            equal(dict(metrics=rec["metrics"], elapsed_s=rec["elapsed_s"], actual_iterations=300),
                  evaluation["arms"]["weighted_pace"], f"evaluation.{year}.{seed}")
            frame = pd.read_parquet(path / "predictions.parquet")
            assert frame.year.eq(year).all() and len(frame) == recipe["target_rows"]
            assert frame.p.between(0, 1).all() and np.isfinite(frame.p).all()
            if year == 2019:
                pace = pd.read_parquet(folder("pace", year, seed) / "predictions.parquet")
                assert frame.equals(pace), ("neutral2019", seed)
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
    """Rebuild the original143 vector; no144 decomposition is permitted."""
    protocol = read(OLD143 / "protocol.json")
    teacher = pd.read_parquet(ROOT / protocol["weights"])
    source = pd.read_parquet(OLD142 / "model_comparison/baseline_ensemble.parquet")
    source = source.loc[source.year.le(2025)].reset_index(drop=True)
    assert teacher[["race_id", "horse_id"]].equals(source[["race_id", "horse_id"]])
    np.testing.assert_array_equal(teacher.p_oof.to_numpy(), source.p.to_numpy())
    assert teacher.year.between(2019, 2025).all()
    assert teacher.oof_train_through.eq(teacher.year - 1).all()
    columns = ["race_id", "horse_id", "horse_number", "year", "odds", "race_date", "race_ok", "dead_heat", "field_size"]
    pool = pd.read_parquet(ROOT / protocol["rows"], columns=columns,
                          filters=[("race_date", ">=", "2007-01-01"), ("race_date", "<=", "2026-06-26")])
    complete = (pool.race_ok & ~pool.dead_heat).groupby(pool.race_id).transform("all")
    pool = pool.loc[complete & pool.race_id.ne("202504040407")].sort_values(["race_id", "horse_number"])
    checks = []
    for year in range(2019, 2027):
        train = pool.loc[pool.year.lt(year)].reset_index(drop=True)
        np.testing.assert_array_equal(train.groupby("race_id").size(), train.groupby("race_id").field_size.first())
        joined = train.merge(teacher[["race_id", "horse_id", "p_oof", "oof_train_through"]],
                             on=["race_id", "horse_id"], how="left", sort=False, validate="one_to_one")
        assert joined[["race_id", "horse_id"]].equals(train[["race_id", "horse_id"]])
        present = joined.p_oof.notna()
        assert present.eq(joined.oof_train_through.notna()).all()
        assert (joined.loc[present, "oof_train_through"] < joined.loc[present, "year"]).all()
        informative = present.groupby(joined.race_id).transform("all").to_numpy()
        w = np.ones(len(train))
        ev = joined.loc[informative, "p_oof"].to_numpy() * joined.loc[informative, "odds"].to_numpy()
        w[informative] += np.exp(-.5 * ((ev - 1.2) / .2) ** 2)
        raw_mean = float(w[informative].mean()) if informative.any() else 1.
        w[informative] /= raw_mean
        np.testing.assert_array_equal(w[~informative], np.ones((~informative).sum()))
        digest = hashlib.sha256(w.tobytes()).hexdigest()
        info = dict(available=int(present.sum()), unavailable=int((~present).sum()),
                    informative_rows=int(informative.sum()), informative_races=int(joined.loc[informative, "race_id"].nunique()),
                    neutral_rows=int((~informative).sum()), neutral_races=int(joined.loc[~informative, "race_id"].nunique()),
                    raw_mean=raw_mean, mean=float(w.mean()), minimum=float(w.min()), maximum=float(w.max()),
                    vector_sha256=digest)
        for seed in range(1, 16):
            for arm in ("weighted_baseline", "weighted_pace"):
                recipe = read(folder(arm, year, seed) / "receipt.json")["recipe"]
                equal(info, recipe["weights"], f"weights.{arm}.{year}.{seed}")
                assert recipe["train_rows"] == len(train)
        checks.append(dict(year=year, training_rows=len(train), **info))
    return checks


def bootstrap(tables, seed):
    dates = sorted(tables["baseline"].race_date.unique())
    draws = np.random.default_rng(seed).multinomial(len(dates), np.full(len(dates), 1 / len(dates)), size=4000)
    daily = {arm: frame.groupby("race_date").agg(stake=("stake_yen", "sum"), payout=("payout_yen", "sum"),
             budget=("budget_yen", "sum"), profit=("profit_yen", "sum"), nll=("winner_nll", "sum"),
             races=("race_id", "size")).reindex(dates) for arm, frame in tables.items()}
    def interval(point, samples):
        good = np.asarray(samples)[np.isfinite(samples)]
        return dict(difference=float(point), ci95=np.quantile(good, [.025, .975]).tolist(),
                    bonferroni_familywise95_ci=np.quantile(good, [.05 / 6, 1 - .05 / 6]).tolist(),
                    valid_replicates=len(good))
    result = {}
    for newarm, oldarm in CONTRASTS:
        old, new = daily[oldarm], daily[newarm]
        result[f"{newarm}_minus_{oldarm}"] = dict(
            winner_nll=interval((new.nll-old.nll).sum()/old.races.sum(), draws @ (new.nll-old.nll)/(draws @ old.races)),
            profit_per_budget=interval((new.profit-old.profit).sum()/old.budget.sum(), draws @ (new.profit-old.profit)/(draws @ old.budget)),
            deployed_roi=interval(new.payout.sum()/new.stake.sum()-old.payout.sum()/old.stake.sum(),
                (draws @ new.payout)/(draws @ new.stake)-(draws @ old.payout)/(draws @ old.stake)))
    return result


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
        strongest = delta.idxmax()
        result[f"{newarm}_minus_{oldarm}"] = dict(
            known_race_delta_yen=float(delta.loc[target]), without_known_race_delta_yen=float(delta.drop(target).sum()),
            top_difference_race=strongest, top_difference_yen=float(delta.loc[strongest]),
            without_top_difference_yen=float(delta.drop(strongest).sum()),
            same_calendar_jan1_jun21_by_year={str(y): float(delta.loc[years.eq(y) & calendar].sum()) for y in range(2019, 2027)},
            before2026_delta_yen=float(delta.loc[years.lt(2026)].sum()), year2026_delta_yen=float(delta.loc[years.eq(2026)].sum()))
    return result


def replay():
    protocol = read(OLD143 / "protocol.json")
    extras = pd.read_parquet(ROOT / protocol["additions"]).set_index(["race_id", "horse_id"])
    checks = []
    for year in (2019, 2024, 2026):
        cols = list(dict.fromkeys(["race_id", "horse_id"] + protocol["base_features"]))
        original = pd.read_parquet(ROOT / protocol["rows"], columns=cols, filters=[("year", "=", year)])
        original = original.set_index(["race_id", "horse_id"])
        for seed in (1, 8, 15):
            path = folder("weighted_pace", year, seed)
            fs = read(path / "feature_spec.json")
            pred = pd.read_parquet(path / "predictions.parquet").iloc[:64]
            idx = pd.MultiIndex.from_frame(pred[["race_id", "horse_id"]])
            selected = original.loc[idx].join(extras.loc[idx], validate="one_to_one")
            x = np.column_stack([selected[fs["features"]].to_numpy(np.float32)] + [
                selected[c].astype("string").map(fs["cat_maps"][c]).to_numpy(np.float32, na_value=np.nan) for c in fs["cats"]])
            booster = lgb.Booster(model_file=str(path / "model.txt"))
            assert booster.feature_name() == fs["feature_order"]
            actual = expit(booster.predict(x, raw_score=True, num_threads=1))
            np.testing.assert_array_equal(actual, pred.p.to_numpy())
            checks.append(dict(arm="weighted_pace", year=year, seed=seed, rows=len(pred), max_abs_p_difference=0.))
    return checks


def audit():
    protocol = read(OUT / "protocol.json")
    assert sha(OUT / "protocol.json") == (OUT / "protocol.sha256").read_text().strip()
    for name, digest in protocol["inputs_and_source_sha256"].items():
        assert sha(ROOT / name) == digest, name
    assert protocol["contrasts"] == [list(pair) for pair in CONTRASTS]
    assert protocol["bootstrap"]["family_size"] == 3 and protocol["bootstrap"]["repetitions"] == 4000
    marker = read(OUT / "models_complete.json")
    assert marker["protocol_sha256"] == sha(OUT / "protocol.json")
    assert marker["jobs"] == 120 and marker["fits"] == 120 and marker["can_adopt"] is False
    expected_inventory = {f"models/seed_{s:02d}/year_{y}/{f}" for y in range(2019, 2027)
                          for s in range(1, 16) for f in ("identity.json", "evaluation.json", "weighted_pace/receipt.json")}
    assert set(marker["hashes"]) == expected_inventory
    for name, digest in marker["hashes"].items():
        assert sha(OUT / name) == digest, name
    counter = dict(fits=0, artifact_hashes=0)
    oldprotocol = read(OLD143 / "protocol.json")
    frames = {arm: helpers.ensemble(arm, oldprotocol, counter) for arm in ARMS[:-1]}
    frames["weighted_pace"] = new_ensemble(protocol, counter)
    current = pd.read_parquet(OLD142 / "existing_policies/inputs.parquet")
    frames["current_reference"] = current.loc[current.year.ge(2019), KEYS + ["p_raw"]].rename(columns={"p_raw": "p"})
    saved = read(OUT / "comparison/summary.json")
    tables = {}
    for arm, frame in frames.items():
        base = frames["baseline"]
        ordered = frame.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
        assert base[KEYS].reset_index(drop=True).equals(ordered[KEYS])
        stored = pd.read_parquet(OUT / f"comparison/{arm}_ensemble.parquet")
        assert ordered.equals(stored.sort_values(["race_id", "horse_number"]).reset_index(drop=True))
        scored, races, bets = helpers.score(frame)
        equal(scored, saved["arms"][arm], arm)
        stored_races = pd.read_parquet(OUT / f"comparison/{arm}_races.parquet")
        assert races[["race_date", "race_id"]].equals(stored_races[["race_date", "race_id"]])
        for column in ("stake_yen", "payout_yen", "profit_yen", "budget_yen", "winner_nll"):
            np.testing.assert_allclose(races[column], stored_races[column], atol=1e-12, rtol=1e-12)
        stored_bets = pd.read_parquet(OUT / f"comparison/{arm}_bets.parquet")
        assert bets[["race_id", "horse_id"]].reset_index(drop=True).equals(stored_bets[["race_id", "horse_id"]].reset_index(drop=True))
        np.testing.assert_array_equal(bets.units.to_numpy(), stored_bets.units.to_numpy())
        np.testing.assert_allclose(bets.payout_yen, stored_bets.payout_yen, atol=1e-10, rtol=0)
        tables[arm] = races
    paired = bootstrap(tables, protocol["bootstrap"]["seed"])
    equal(paired, saved["paired"]["results"], "paired")
    diagnostic = sensitivity(tables)
    equal(diagnostic, saved["sensitivity"], "sensitivity")
    return dict(auditor="paceweight145_review", protocol_sha256=sha(OUT / "protocol.json"),
                summary_sha256=sha(OUT / "comparison/summary.json"), counters=counter,
                independent_weights=independent_weights(), independent_saved_body_replays=replay(),
                paired=paired, sensitivity=diagnostic, no_model_fits_performed=True,
                can_adopt=False, untouched_holdout=False, blockers=[], audit_script_sha256=sha(__file__),
                independent143_helpers_sha256=sha(HELPER_PATH))


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
