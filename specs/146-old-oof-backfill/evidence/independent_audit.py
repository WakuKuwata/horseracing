"""Independent 146 audit; no experiment imports, model fits, or artifact edits.

--stage mentors audits the completed first-stage material before candidate fits.
The default final stage also independently scores and compares all four arms.
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
OUT = ROOT / "artifacts/146-old-oof-backfill"
OLD142 = ROOT / "artifacts/142-roi-policy-model-improvement"
OLD143 = ROOT / "artifacts/143-existing-feature-roi"
KEYS = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "field_size"]
ARM = "weighted_backfill"
CONTRASTS = ((ARM, "baseline"), (ARM, "weighted_baseline"))
HELPER_PATH = ROOT / "specs/143-existing-feature-roi/evidence/independent_audit.py"
spec = importlib.util.spec_from_file_location("independent143_helpers_for146", HELPER_PATH)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
sha, read, equal = helpers.sha, helpers.read, helpers.equal


def protocol(stage):
    path = OUT / ("mentor_protocol.json" if stage == "mentor" else "protocol.json")
    assert sha(path) == path.with_suffix(".sha256").read_text().strip()
    doc = read(path)
    assert doc["stage"] == stage and doc["feature"] == "146-old-oof-backfill"
    assert doc["seeds"] == list(range(1, 16))
    assert doc["max_workers"] == 2 and doc["threads_each"] == 1
    assert doc["models"] == read(OLD143 / "protocol.json")["models"]
    assert doc["features"] == read(OLD143 / "protocol.json")["base_features"]
    assert doc["weight_recipe"] == read(OLD143 / "protocol.json")["weight_recipe"]
    assert doc["can_adopt"] is False and doc["untouched_holdout"] is False
    for name, digest in doc["inputs_and_source_sha256"].items():
        path_to_check = (ROOT / name).resolve()
        assert path_to_check.is_relative_to(ROOT) and sha(path_to_check) == digest, name
    return doc


def pool(doc, include_categories=False):
    columns = KEYS + ["race_ok", "dead_heat"]
    if include_categories:
        columns += [c for c in doc["features"][-11:] if c not in columns]
    data = pd.read_parquet(ROOT / doc["rows"], columns=columns,
        filters=[("race_date", ">=", "2007-01-01"), ("race_date", "<=", "2026-06-26")])
    assert data.year.eq(pd.to_datetime(data.race_date).dt.year).all()
    eligible = (data.race_ok & ~data.dead_heat).groupby(data.race_id).transform("all")
    data = data.loc[eligible & data.race_id.ne("202504040407")].sort_values(
        ["race_id", "horse_number"], kind="stable").reset_index(drop=True)
    assert not data.duplicated(["race_id", "horse_id"]).any()
    assert not data.duplicated(["race_id", "horse_number"]).any()
    assert data.groupby("race_id").won.sum().eq(1).all()
    np.testing.assert_array_equal(data.groupby("race_id").size(), data.groupby("race_id").field_size.first())
    return data


def folder(stage, year, seed):
    directory = "mentor_models" if stage == "mentor" else "models"
    arm = "baseline" if stage == "mentor" else ARM
    return OUT / f"{directory}/seed_{seed:02d}/year_{year}/{arm}"


def expected_identity(stage, doc, year, seed):
    arm = "baseline" if stage == "mentor" else ARM
    path = OUT / ("mentor_protocol.json" if stage == "mentor" else "protocol.json")
    return dict(version=1, stage=stage, protocol_sha256=sha(path), rows_sha256=sha(ROOT / doc["rows"]),
        weights_sha256=sha(ROOT / doc["weights"]) if stage == "candidate" else None,
        year=year, seed=seed, arms=[arm], features=doc["features"], research_arm=arm,
        kernel_arm="baseline" if stage == "mentor" else "weighted_baseline",
        source_sha256=sha(ROOT / "scripts/roi_explore/roi_improvement_146/models.py"),
        dependencies={name: sha(ROOT / relative) for name, relative in (
            ("models143", "scripts/roi_explore/roi_improvement_143/models.py"),
            ("models142", "scripts/roi_explore/roi_improvement_142/models.py"),
            ("legacy", "scripts/roi_explore/direct_return_model.py"))},
        model_recipe=doc["models"], weight_recipe=doc["weight_recipe"],
        python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, lightgbm=lgb.__version__,
        research_only=True, can_adopt=False, untouched_holdout=False)


def completion(stage, doc):
    marker = read(OUT / ("mentor_models_complete.json" if stage == "mentor" else "models_complete.json"))
    years = range(2013, 2019) if stage == "mentor" else range(2019, 2027)
    directory, arm = ("mentor_models", "baseline") if stage == "mentor" else ("models", ARM)
    expected = {f"{directory}/seed_{s:02d}/year_{y}/{name}" for y in years for s in range(1, 16)
                for name in ("identity.json", "evaluation.json", f"{arm}/receipt.json")}
    assert marker["stage"] == stage and marker["jobs"] == len(years)*15 and marker["fits"] == len(years)*15
    assert marker["protocol_sha256"] == sha(OUT / ("mentor_protocol.json" if stage == "mentor" else "protocol.json"))
    assert marker["can_adopt"] is False and set(marker["hashes"]) == expected
    for name, digest in marker["hashes"].items():
        assert sha(OUT / name) == digest, name
    return len(expected)


def new_ensemble(stage, doc, data, counter, full=True):
    years = range(2013, 2019) if stage == "mentor" else range(2019, 2027)
    arm = "baseline" if stage == "mentor" else ARM
    pieces, checks = [], []
    cats, numeric = doc["features"][-11:], doc["features"][:-11]
    for year in years:
        train, target = data.loc[data.year.lt(year)], data.loc[data.year.eq(year), KEYS].reset_index(drop=True)
        cat_maps = {c: {str(v): i for i, v in enumerate(pd.unique(train[c].dropna().astype(str)))} for c in cats}
        first, total = None, None
        for seed in range(1, 16):
            path = folder(stage, year, seed)
            if not (path / "receipt.json").exists() and not full:
                continue
            rec = read(path / "receipt.json")
            recipe = rec["recipe"]
            assert recipe["seed"] == seed and recipe["rounds"] == 300 and rec["actual_iterations"] == 300
            assert rec["roundtrip_max_abs_raw_difference"] <= 1e-12
            assert recipe["train_start"] == train.race_date.min() and recipe["train_end"] == train.race_date.max()
            assert recipe["target_start"] == target.race_date.min() and recipe["target_end"] == target.race_date.max()
            assert recipe["train_end"] < recipe["target_start"]
            assert recipe["train_rows"] == len(train) and recipe["target_rows"] == len(target)
            identity = expected_identity(stage, doc, year, seed)
            assert recipe["identity"] == identity, (stage, year, seed, "identity")
            params = dict(doc["models"]["params"], seed=seed, metric="binary_logloss")
            if stage == "mentor":
                assert recipe["arm"] == "baseline" and recipe["overrides"] == {} and recipe["earlystop"] is False
                assert rec["best_iteration"] == 300
            else:
                params["objective"] = "binary"
                assert recipe["arm"] == "weighted_baseline" and recipe["params"] == params
                assert recipe["features"] == doc["features"]
            assert set(rec["hashes"]) == {"model.txt", "predictions.parquet", "feature_spec.json"}
            for name, digest in rec["hashes"].items():
                assert sha(path / name) == digest, (stage, year, seed, name)
                counter["artifact_hashes"] += 1
            fs = read(path / "feature_spec.json")
            assert fs["features"] == numeric and fs["cats"] == cats and fs["cat_maps"] == cat_maps
            assert fs["params"] == params and fs["objective"] == "binary" and fs["best_iteration"] == 300
            assert fs["train_from"] == 2007 and fs["train_through"] == year-1 and fs["trained_for_year"] == year
            assert fs["training_cutoff"] == train.race_date.max()
            assert read(path.parent / "identity.json") == identity
            evaluation = read(path.parent / "evaluation.json")
            assert evaluation["identity"] == identity and evaluation["stage"] == stage
            assert evaluation["year"] == year and evaluation["seed"] == seed and set(evaluation["arms"]) == {arm}
            equal(dict(metrics=rec["metrics"], elapsed_s=rec["elapsed_s"], actual_iterations=300), evaluation["arms"][arm])
            frame = pd.read_parquet(path / "predictions.parquet")
            assert frame[KEYS].equals(target), (stage, year, seed, "population")
            assert frame.p.between(0, 1).all() and np.isfinite(frame.p).all()
            sums = frame.groupby("race_id").p.transform("sum")
            np.testing.assert_allclose(frame.p / sums, frame.p_race, atol=1e-15, rtol=1e-15)
            if first is None:
                first, total = frame[KEYS].copy(), np.zeros(len(frame))
            else:
                assert first.equals(frame[KEYS])
            total += frame.p.to_numpy(float)
            counter["fits"] += 1
            checks.append(dict(stage=stage, year=year, seed=seed, train_rows=len(train), target_rows=len(target)))
        if first is not None and full:
            first["p"] = total / 15
            pieces.append(first)
    return pd.concat(pieces, ignore_index=True) if pieces else None, checks


def teacher_check(mentors, data):
    teacher = pd.read_parquet(OUT / "weights.parquet")
    old = pd.read_parquet(OLD143 / "weights.parquet")
    assert not teacher.duplicated(["race_id", "horse_id"]).any()
    assert teacher.year.between(2013, 2025).all() and teacher.oof_train_through.eq(teacher.year-1).all()
    assert teacher.p_oof.between(0, 1).all() and np.isfinite(teacher.p_oof).all()
    order = ["race_id", "horse_id"]
    historical = teacher.loc[teacher.year.ge(2019)].sort_values(order).reset_index(drop=True)
    pd.testing.assert_frame_equal(historical, old.sort_values(order).reset_index(drop=True), check_exact=True)
    past = teacher.loc[teacher.year.le(2018)].sort_values(order).reset_index(drop=True)
    ordered_mentors = mentors.sort_values(order).reset_index(drop=True)
    assert past[["race_id", "horse_id", "year", "race_date"]].equals(ordered_mentors[["race_id", "horse_id", "year", "race_date"]])
    np.testing.assert_array_equal(past.p_oof.to_numpy(), ordered_mentors.p.to_numpy())
    covered = data.loc[data.year.between(2013, 2025), order].sort_values(order).reset_index(drop=True)
    assert teacher[order].sort_values(order).reset_index(drop=True).equals(covered)
    assert len(teacher) == 608523 and len(past) == 287252 and len(old) == 321271
    manifest = read(OUT / "weights_manifest.json")
    assert manifest["rows"] == 608523 and manifest["new_rows"] == 287252 and manifest["original_rows"] == 321271
    assert manifest["rows_by_year"] == {str(y): int(n) for y, n in teacher.groupby("year").size().items()}
    assert manifest["mentor_protocol_sha256"] == sha(OUT / "mentor_protocol.json")
    assert manifest["mentor_completion_sha256"] == sha(OUT / "mentor_models_complete.json")
    assert manifest["original_weights_sha256"] == sha(OLD143 / "weights.parquet")
    assert manifest["weights_sha256"] == sha(OUT / "weights.parquet")
    assert manifest["original_values_preserved_exactly"] is True and manifest["neutral_years"] == list(range(2007, 2013))
    expected_details = []
    for year in range(2013, 2019):
        for seed in range(1, 16):
            path = folder("mentor", year, seed) / "receipt.json"
            expected_details.append(dict(year=year, seed=seed, elapsed_s=read(path)["elapsed_s"], receipt_sha256=sha(path)))
    assert manifest["mentor_fits"] == expected_details
    assert manifest["mentor_worker_seconds"] == sum(item["elapsed_s"] for item in expected_details)
    return dict(new_teacher_rows=len(past), new_teacher_races=int(past.race_id.nunique()),
        preserved_old_rows=len(old), total_rows=len(teacher), weights_sha256=sha(OUT / "weights.parquet"),
        old_values_bitwise_preserved=True, raw_mentor_seed_means_exact=True)


def independent_weights(doc, data, full=True):
    teacher = pd.read_parquet(ROOT / doc["weights"])
    checks = []
    for year in range(2019, 2027):
        train = data.loc[data.year.lt(year)].reset_index(drop=True)
        joined = train.merge(teacher[["race_id", "horse_id", "p_oof", "oof_train_through"]],
            on=["race_id", "horse_id"], how="left", sort=False, validate="one_to_one")
        assert joined[["race_id", "horse_id"]].equals(train[["race_id", "horse_id"]])
        present = joined.p_oof.notna()
        assert present.equals(joined.year.ge(2013))
        assert present.eq(joined.oof_train_through.notna()).all()
        assert joined.loc[present, "oof_train_through"].eq(joined.loc[present, "year"]-1).all()
        informative = present.groupby(joined.race_id).transform("all").to_numpy()
        w = np.ones(len(train))
        ev = joined.loc[informative, "p_oof"].to_numpy() * joined.loc[informative, "odds"].to_numpy()
        w[informative] += np.exp(-.5 * ((ev-1.2)/.2)**2)
        raw_mean = float(w[informative].mean())
        w[informative] /= raw_mean
        assert int((~informative).sum()) == 287052
        np.testing.assert_array_equal(w[~informative], np.ones((~informative).sum()))
        info = dict(recipe=doc["weight_recipe"], available=int(present.sum()), unavailable=int((~present).sum()),
            informative_rows=int(informative.sum()), informative_races=int(joined.loc[informative, "race_id"].nunique()),
            neutral_rows=int((~informative).sum()), neutral_races=int(joined.loc[~informative, "race_id"].nunique()),
            raw_mean=raw_mean, mean=float(w.mean()), minimum=float(w.min()), maximum=float(w.max()),
            vector_sha256=hashlib.sha256(w.tobytes()).hexdigest())
        verified_seeds = []
        for seed in range(1, 16):
            receipt = folder("candidate", year, seed)/"receipt.json"
            if not receipt.exists() and not full:
                continue
            assert read(receipt)["recipe"]["weights"] == info
            verified_seeds.append(seed)
        checks.append(dict(year=year, training_rows=len(train), verified_seeds=verified_seeds, **info))
    return checks


def bootstrap(tables, seed):
    dates = sorted(tables["baseline"].race_date.unique())
    assert len(dates) == 807
    draws = np.random.default_rng(seed).multinomial(len(dates), np.full(len(dates), 1/len(dates)), size=4000)
    daily = {arm: frame.groupby("race_date").agg(stake=("stake_yen", "sum"), payout=("payout_yen", "sum"),
        budget=("budget_yen", "sum"), profit=("profit_yen", "sum"), nll=("winner_nll", "sum"),
        races=("race_id", "size")).reindex(dates) for arm, frame in tables.items()}
    def interval(point, samples):
        good = np.asarray(samples)[np.isfinite(samples)]
        return dict(difference=float(point), ci95=np.quantile(good, [.025, .975]).tolist(),
            bonferroni_familywise95_ci=np.quantile(good, [.0125, .9875]).tolist(), valid_replicates=len(good))
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
        old, new = tables[oldarm].set_index("race_id"), tables[newarm].set_index("race_id")
        assert old.index.equals(new.index)
        delta = new.profit_yen-old.profit_yen
        years, calendar = old.race_date.str[:4].astype(int), old.race_date.str[5:].le("06-21")
        known, top = "202608030111", delta.idxmax()
        result[f"{newarm}_minus_{oldarm}"] = dict(known_race_delta_yen=float(delta.loc[known]),
            without_known_race_delta_yen=float(delta.drop(known).sum()), top_difference_race=top,
            top_difference_yen=float(delta.loc[top]), without_top_difference_yen=float(delta.drop(top).sum()),
            same_calendar_jan1_jun21_by_year={str(y): float(delta.loc[years.eq(y)&calendar].sum()) for y in range(2019, 2027)},
            before2026_delta_yen=float(delta.loc[years.lt(2026)].sum()), year2026_delta_yen=float(delta.loc[years.eq(2026)].sum()))
    return result


def replay(stage, doc, complete=True):
    checks = []
    years = (2013, 2016, 2018) if stage == "mentor" else (2019, 2024, 2026)
    for year in years:
        columns = list(dict.fromkeys(["race_id", "horse_id"]+doc["features"]))
        original = pd.read_parquet(ROOT/doc["rows"], columns=columns, filters=[("year", "=", year)]).set_index(["race_id", "horse_id"])
        for seed in (1, 8, 15):
            path = folder(stage, year, seed)
            if not (path/"receipt.json").exists() and not complete:
                continue
            fs = read(path/"feature_spec.json")
            pred = pd.read_parquet(path/"predictions.parquet").iloc[:64]
            selected = original.loc[pd.MultiIndex.from_frame(pred[["race_id", "horse_id"]])]
            x = np.column_stack([selected[fs["features"]].to_numpy(np.float32)] + [
                selected[c].astype("string").map(fs["cat_maps"][c]).to_numpy(np.float32, na_value=np.nan) for c in fs["cats"]])
            booster = lgb.Booster(model_file=str(path/"model.txt"))
            assert booster.feature_name() == doc["features"] and booster.current_iteration() == 300
            actual = expit(booster.predict(x, raw_score=True, num_threads=1))
            np.testing.assert_array_equal(actual, pred.p.to_numpy())
            checks.append(dict(stage=stage, year=year, seed=seed, rows=len(pred), max_abs_p_difference=0.))
    return checks


def audit(stage):
    mentor_doc = protocol("mentor")
    data = pool(mentor_doc, include_categories=True)
    counter = dict(fits=0, artifact_hashes=0)
    if stage == "first":
        _, checks = new_ensemble("mentor", mentor_doc, data, counter, full=False)
        return dict(counters=counter, fit_checks=checks, saved_body_replays=replay("mentor", mentor_doc, complete=False),
            blockers=[], no_model_fits_performed=True)
    mentor_inventory = completion("mentor", mentor_doc)
    mentors, mentor_checks = new_ensemble("mentor", mentor_doc, data, counter)
    teachers = teacher_check(mentors, data)
    result = dict(auditor="oof146_review", mentor_protocol_sha256=sha(OUT/"mentor_protocol.json"),
        counters=counter, mentor_completion_hashes=mentor_inventory, teacher_checks=teachers,
        mentor_fit_checks=mentor_checks, saved_body_replays=replay("mentor", mentor_doc),
        no_model_fits_performed=True, can_adopt=False, untouched_holdout=False, blockers=[],
        audit_script_sha256=sha(__file__), independent143_helpers_sha256=sha(HELPER_PATH))
    if stage == "mentors":
        return result
    doc = protocol("candidate")
    changed = {"stage", "frozen_at", "arms", "window", "weights", "mentor_protocol_sha256", "inputs_and_source_sha256"}
    assert {key: value for key, value in doc.items() if key not in changed} == {
        key: value for key, value in mentor_doc.items() if key not in changed}
    assert doc["mentor_protocol_sha256"] == sha(OUT / "mentor_protocol.json")
    assert doc["window"] == dict(mentor_doc["window"], model_outer=list(range(2019, 2027)))
    equal(mentor_doc["inputs_and_source_sha256"], doc["inputs_and_source_sha256"], "stage1bindings")
    assert doc["contrasts"] == [list(pair) for pair in CONTRASTS]
    assert doc["bootstrap"]["family_size"] == 2 and doc["bootstrap"]["repetitions"] == 4000
    result["candidate_completion_hashes"] = completion("candidate", doc)
    frames = {arm: helpers.ensemble(arm, read(OLD143/"protocol.json"), counter) for arm in ("baseline", "weighted_baseline")}
    frames[ARM], result["candidate_fit_checks"] = new_ensemble("candidate", doc, data, counter)
    current = pd.read_parquet(OLD142/"existing_policies/inputs.parquet")
    frames["current_reference"] = current.loc[current.year.ge(2019), KEYS+["p_raw"]].rename(columns={"p_raw":"p"})
    saved = read(OUT/"comparison/summary.json")
    tables = {}
    for arm, frame in frames.items():
        ordered = frame.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
        assert frames["baseline"][KEYS].reset_index(drop=True).equals(ordered[KEYS])
        stored = pd.read_parquet(OUT/f"comparison/{arm}_ensemble.parquet")
        assert ordered.equals(stored.sort_values(["race_id", "horse_number"]).reset_index(drop=True))
        scored, races, bets = helpers.score(frame)
        equal(scored, saved["arms"][arm], arm)
        stored_races = pd.read_parquet(OUT/f"comparison/{arm}_races.parquet")
        assert races[["race_date", "race_id"]].equals(stored_races[["race_date", "race_id"]])
        for column in ("stake_yen", "payout_yen", "profit_yen", "budget_yen", "winner_nll"):
            np.testing.assert_allclose(races[column], stored_races[column], atol=1e-12, rtol=1e-12)
        stored_bets = pd.read_parquet(OUT/f"comparison/{arm}_bets.parquet")
        assert bets[["race_id", "horse_id"]].reset_index(drop=True).equals(stored_bets[["race_id", "horse_id"]].reset_index(drop=True))
        np.testing.assert_array_equal(bets.units.to_numpy(), stored_bets.units.to_numpy())
        np.testing.assert_allclose(bets.payout_yen, stored_bets.payout_yen, atol=1e-10, rtol=0)
        tables[arm] = races
    paired, diagnostic = bootstrap(tables, doc["bootstrap"]["seed"]), sensitivity(tables)
    equal(paired, saved["paired"]["results"], "paired")
    equal(diagnostic, saved["sensitivity"], "sensitivity")
    result.update(protocol_sha256=sha(OUT/"protocol.json"), summary_sha256=sha(OUT/"comparison/summary.json"),
        independent_weights=independent_weights(doc, data), paired=paired, sensitivity=diagnostic)
    result["saved_body_replays"] += replay("candidate", doc)
    assert counter == dict(fits=450, artifact_hashes=1350)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--stage", choices=["first", "mentors", "final"], default="final")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        helpers.self_test()
    else:
        result = audit(args.stage)
        output = Path(__file__).with_name(f"independent_{args.stage}.json")
        with output.open("x") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        print(json.dumps(dict(evidence=str(output), counters=result["counters"], blockers=[])))
