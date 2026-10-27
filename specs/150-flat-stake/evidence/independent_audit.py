"""Independent fixed100yen audit; no imports from implementation150.

Reconstruct all 240 existing fits and audit hashes, metadata and chronology.
Calculate race probability normalization, buy selections, integer stakes, quality, risk,
seven paired bootstraps and sensitivity independently. Never fit a model.
"""
from __future__ import annotations

import os
for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[key] = "1"

import argparse
import importlib.util
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/150-flat-stake"
OLD142 = ROOT / "artifacts/142-roi-policy-model-improvement"
OLD143 = ROOT / "artifacts/143-existing-feature-roi"
KEYS = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "field_size"]
SOURCE_ARMS = ("baseline", "weighted_baseline")
NORMALIZED = {"normalized_weighted_baseline": 1.0}
FLAT_PARENTS = {"flat_weighted_baseline": "weighted_baseline", "flat_normalized_weighted_baseline": "normalized_weighted_baseline"}
ARMS = ("current_reference", *SOURCE_ARMS, *NORMALIZED, *FLAT_PARENTS)
CONTRASTS = tuple((candidate, control) for candidate in FLAT_PARENTS for control in
    ("baseline", "weighted_baseline", "normalized_weighted_baseline")) + (("flat_normalized_weighted_baseline", "flat_weighted_baseline"),)
HELPER_PATH = ROOT / "specs/143-existing-feature-roi/evidence/independent_audit.py"
spec = importlib.util.spec_from_file_location("independent143_helpers_for150", HELPER_PATH)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
sha, read, equal = helpers.sha, helpers.read, helpers.equal


def folder(arm, year, seed):
    if arm == "baseline":
        return OLD142 / f"models/seed_{seed:02d}/year_{year}/baseline"
    stage = "weighted_models" if arm == "weighted_baseline" else "models"
    return OLD143 / stage / f"seed_{seed:02d}/year_{year}/{arm}"


def verify_sources(protocol):
    old = read(OLD143 / "protocol.json")
    old142 = read(OLD142 / "protocol.json")
    for original in (old142, old):
        for name, digest in original["inputs_and_source_sha256"].items():
            assert sha(ROOT / name) == digest, name
    for year in range(2019, 2027):
        for seed in range(1, 16):
            for arm in SOURCE_ARMS:
                path = folder(arm, year, seed)
                rec = read(path / "receipt.json")
                recipe, identity = rec["recipe"], rec["recipe"]["identity"]
                assert set(rec["hashes"]) == {"feature_spec.json", "model.txt", "predictions.parquet"}
                assert str((path / "receipt.json").relative_to(ROOT)) in protocol["inputs_and_source_sha256"]
                assert str((path / "predictions.parquet").relative_to(ROOT)) in protocol["inputs_and_source_sha256"]
                if arm == "baseline":
                    assert identity["protocol_sha256"] == sha(OLD142 / "protocol.json")
                    assert identity["source_sha256"] == sha(ROOT / "scripts/roi_explore/roi_improvement_142/models.py")
                    assert identity["dependency_sha256"]["direct_return_model"] == sha(ROOT / "scripts/roi_explore/direct_return_model.py")
                    assert identity["lower"] == "2007-01-01" and identity["upper"] == "2026-06-26"
                else:
                    assert identity["source_sha256"] == sha(ROOT / "scripts/roi_explore/roi_improvement_143/models.py")
                    assert identity["dependencies"] == dict(
                        legacy=sha(ROOT / "scripts/roi_explore/direct_return_model.py"),
                        models142=sha(ROOT / "scripts/roi_explore/roi_improvement_142/models.py"))
                    assert identity["bundles"] == old["bundles"]
                    assert identity["weights_sha256"] == (sha(ROOT / old["weights"]) if arm == "weighted_baseline" else None)
                    assert recipe["weights"] is None if arm == "pace" else recipe["weights"] is not None
                assert identity["research_only"] is True and identity["can_adopt"] is False
                assert identity["untouched_holdout"] is False


def bootstrap(tables):
    dates = sorted(tables["baseline"].race_date.unique())
    assert len(dates) == 807
    draws = np.random.default_rng(20261025).multinomial(len(dates), np.full(len(dates), 1 / len(dates)), size=4000)
    daily = {a: f.groupby("race_date").agg(stake=("stake_yen", "sum"), payout=("payout_yen", "sum"),
        budget=("budget_yen", "sum"), profit=("profit_yen", "sum"), nll=("winner_nll", "sum"),
        races=("race_id", "size")).reindex(dates) for a, f in tables.items()}
    def interval(point, samples):
        good = np.asarray(samples)[np.isfinite(samples)]
        return dict(difference=float(point), ci95=np.quantile(good, [.025, .975]).tolist(),
            bonferroni_familywise95_ci=np.quantile(good, [.05 / 14, 1 - .05 / 14]).tolist(),
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
        target, strongest = "202608030111", delta.idxmax()
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
    # Three fixed cross-year/seed bodies per source arm, selected without ROI.
    for year, seed in ((2019, 1), (2024, 8), (2026, 15)):
        cols = list(dict.fromkeys(["race_id", "horse_id"] + protocol["base_features"]))
        original = pd.read_parquet(ROOT / protocol["rows"], columns=cols, filters=[("year", "=", year)])
        original = original.set_index(["race_id", "horse_id"])
        for arm in SOURCE_ARMS:
            path = folder(arm, year, seed)
            fs = read(path / "feature_spec.json")
            pred = pd.read_parquet(path / "predictions.parquet").iloc[:64]
            idx = pd.MultiIndex.from_frame(pred[["race_id", "horse_id"]])
            selected = original.loc[idx].join(extras.loc[idx], validate="one_to_one")
            x = np.column_stack([selected[fs["features"]].to_numpy(np.float32)] + [
                selected[c].astype("string").map(fs["cat_maps"][c]).to_numpy(np.float32, na_value=np.nan) for c in fs["cats"]])
            booster = lgb.Booster(model_file=str(path / "model.txt"))
            expected = fs.get("feature_order", fs["features"] + fs["cats"])
            assert booster.feature_name() == expected
            actual = expit(booster.predict(x, raw_score=True, num_threads=1))
            np.testing.assert_array_equal(actual, pred.p.to_numpy())
            checks.append(dict(arm=arm, year=year, seed=seed, rows=len(pred), max_abs_p_difference=0.))
    return checks


def flat_score(frame):
    # Independent helper selects via per-field rawEV loop. Replace only stakes,
    # then independently rebuild every race and annual cash/risk measure.
    parent, races, bets = helpers.score(frame)
    original_keys = bets[["race_id", "horse_id"]].copy()
    bets = bets.copy()
    bets["units"] = 1
    bets["stake_yen"] = 100
    bets["payout_yen"] = 100 * bets.won * bets.odds
    aggregated = bets.groupby("race_id").agg(stake=("stake_yen", "sum"), payout=("payout_yen", "sum"))
    races = races.copy()
    races["stake_yen"] = races.race_id.map(aggregated.stake).fillna(0.)
    races["payout_yen"] = races.race_id.map(aggregated.payout).fillna(0.)
    races["profit_yen"] = races.payout_yen-races.stake_yen
    assert original_keys.equals(bets[["race_id", "horse_id"]])
    assert bets.stake_yen.eq(100).all() and races.stake_yen.le(1000).all()
    parent["policy"] = helpers.point(races, bets)
    for year in parent["by_year"]:
        ids = set(frame.loc[frame.year.eq(int(year)), "race_id"])
        parent["by_year"][year]["policy"] = helpers.point(races.loc[races.race_id.isin(ids)], bets.loc[bets.race_id.isin(ids)])
    return parent, races, bets


def audit():
    protocol = read(OUT / "protocol.json")
    assert sha(OUT / "protocol.json") == (OUT / "protocol.sha256").read_text().strip()
    for name, digest in protocol["inputs_and_source_sha256"].items():
        assert sha(ROOT / name) == digest, name
    assert protocol["contrasts"] == [list(pair) for pair in CONTRASTS]
    assert protocol["bootstrap"]["family_size"] == 7 and protocol["bootstrap"]["repetitions"] == 4000
    assert protocol["bootstrap"]["seed"] == 20261025
    assert protocol["new_fits"] == 0 and protocol["reused_control_fits"] == 240
    assert protocol["seeds"] == list(range(1, 16))
    equal(dict(training_from=2007, through="2026-06-26", model_outer=list(range(2019, 2027))), protocol["window"])
    old148 = ROOT / "artifacts/148-postprediction-normalization"
    assert sha(old148 / "protocol.json") == (old148 / "protocol.sha256").read_text().strip()
    for name, digest in read(old148 / "protocol.json")["inputs_and_source_sha256"].items():
        assert protocol["inputs_and_source_sha256"][name] == digest
    for name in ("run.py", "report.py"):
        assert f"scripts/roi_explore/roi_improvement_150/{name}" in protocol["inputs_and_source_sha256"]
    for flag in ("can_adopt", "untouched_holdout", "eligible_for_verdict"):
        assert protocol[flag] is False
    equal(dict(parents=["weighted_baseline","normalized_weighted_baseline"],
        candidates=list(FLAT_PARENTS),stake_yen_per_selected_horse=100,
        selection="exact frozen148 parent ticket keys, including EV>1.2/max10/tie order; no new selection",
        budget_yen_per_race=1000,max_horses=10,unspent_cash="all uninvested budget stays cash",
        prediction_change=False,fitted_parameters=0,stake_search=False),protocol["allocation"])
    assert protocol["proposal2"]=="executed"
    verify_sources(protocol)
    old = read(OLD143 / "protocol.json")
    counter = dict(fits=0, artifact_hashes=0)
    frames = {arm: helpers.ensemble(arm, old, counter) for arm in SOURCE_ARMS}
    assert counter == dict(fits=240, artifact_hashes=720)
    base = frames["baseline"]
    assert len(base) == 343883 and base.race_id.nunique() == 24855 and base.race_date.nunique() == 807
    assert base.race_date.min() == "2019-01-05" and base.race_date.max() == "2026-06-21"
    for frame in frames.values():
        assert base[KEYS].equals(frame[KEYS])
    norm_diagnostics = {}
    for candidate, exponent in NORMALIZED.items():
        parent = "weighted_baseline"
        raw = frames[parent]
        derived = raw[KEYS].copy()
        values = raw.p.to_numpy(float)
        # Independent field loops: no shared normalization implementation.
        normalized = np.zeros(len(raw), dtype=float)
        for positions in raw.groupby("race_id", sort=False).indices.values():
            selected = values[positions]
            assert len(positions) == raw.field_size.iloc[positions[0]]
            assert selected.sum() > 0 and np.isfinite(selected).all()
            normalized[positions] = selected / (selected.sum() ** exponent)
        derived["p"] = normalized
        sums = derived.groupby("race_id").p.sum().to_numpy()
        expected_sums = raw.groupby("race_id").p.sum().to_numpy() ** (1-exponent)
        np.testing.assert_allclose(sums, expected_sums, rtol=0, atol=1e-14)
        frames[candidate] = derived
        norm_diagnostics[candidate] = dict(parent=parent, exponent=exponent, max_abs_race_sum_error=float(abs(sums-expected_sums).max()))
    current = pd.read_parquet(OLD142 / "existing_policies/inputs.parquet")
    frames["current_reference"] = current.loc[current.year.ge(2019), KEYS + ["p_raw"]].rename(columns={"p_raw": "p"})
    saved = read(OUT / "comparison/summary.json")
    assert set(saved["arms"]) == set(ARMS)
    assert saved["new_fits"] == 0 and saved["reused_control_fits"] == 240
    assert saved["protocol_sha256"] == sha(OUT / "protocol.json")
    equal(protocol["allocation"],saved["allocation"])
    assert saved["proposal2"]=="executed"
    for flag in ("can_adopt", "untouched_holdout", "eligible_for_verdict"):
        assert saved[flag] is False
    for arm, parent in FLAT_PARENTS.items():
        frames[arm] = frames[parent].copy()
    tables = {}
    for arm, frame in frames.items():
        ordered = frame.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
        assert base[KEYS].reset_index(drop=True).equals(ordered[KEYS])
        stored = pd.read_parquet(OUT / f"comparison/{arm}_ensemble.parquet")
        stored = stored.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
        assert ordered[KEYS].equals(stored[KEYS]), ("ensemble_metadata", arm)
        np.testing.assert_allclose(ordered.p, stored.p, rtol=1e-14, atol=1e-15)
        # Audit policy from independently derived p, allowing only floating sum-order differences.

        scored, races, bets = flat_score(frame) if arm in FLAT_PARENTS else helpers.score(frame)
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
    for candidate, exponent in NORMALIZED.items():
        parent = "weighted_baseline"
        np.testing.assert_allclose(tables[candidate].winner_nll, tables[parent].winner_nll, atol=2e-14, rtol=0)
        norm_diagnostics[candidate]["max_abs_winner_nll_difference"] = float(abs(tables[candidate].winner_nll-tables[parent].winner_nll).max())
    for candidate,parent in FLAT_PARENTS.items():
        parent_bets=pd.read_parquet(OUT / f"comparison/{parent}_bets.parquet")
        candidate_bets=pd.read_parquet(OUT / f"comparison/{candidate}_bets.parquet")
        assert parent_bets[["race_id","horse_id"]].equals(candidate_bets[["race_id","horse_id"]])
        assert candidate_bets.stake_yen.eq(100).all()
        assert saved["arms"][candidate]["quality"]==saved["arms"][parent]["quality"]
    allocation_examples={}
    for rid in ("202609010807","202610011209","202608030111"):
        allocation_examples[rid]={}
        for arm,table in tables.items():
            allocation_examples[rid][arm]=table.loc[table.race_id.eq(rid),["stake_yen","payout_yen","profit_yen"]].iloc[0].to_dict()
    equal(allocation_examples,saved["allocation_examples"])
    paired, diagnostic = bootstrap(tables), sensitivity(tables)
    equal(paired, saved["paired"]["results"], "paired")
    equal(diagnostic, saved["sensitivity"], "sensitivity")
    return dict(auditor="flat150_review", protocol_sha256=sha(OUT / "protocol.json"),
        summary_sha256=sha(OUT / "comparison/summary.json"), counters=counter,
        frozen_file_count=len(protocol["inputs_and_source_sha256"]), independent_saved_body_replays=replay(),
        flat100_candidates_and_raw_full_controls_reconstructed=True, normalization_diagnostics=norm_diagnostics, points={a:saved["arms"][a]["policy"] for a in ARMS},
        paired=paired, sensitivity=diagnostic, allocation_examples=allocation_examples, no_model_fits_performed=True, independent_fixed100_cash_reaggregation_performed=True,
        can_adopt=False, untouched_holdout=False, blockers=[], audit_script_sha256=sha(__file__),
        independent143_helpers_sha256=sha(HELPER_PATH))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        helpers.self_test()
        rows=[]
        for i in range(3):
            for h in range(12):
                rows.append(dict(race_id=f"20190101000{i+1}",horse_id=f"h{h:02d}",horse_number=h+1,
                    race_date=f"2019-01-0{i+1}",year=2019,field_size=12,won=int(h==1),odds=10.,
                    p=.15 if i==0 else (.03 if i==1 else (.2 if h<3 else .02))))
        frame=pd.DataFrame(rows)
        point,races,bets=flat_score(frame)
        assert list(races.stake_yen)==[1000,0,300]
        assert len(bets)==13 and bets.units.eq(1).all()
        assert point["policy"]["stake_yen"]==1300
        assert point["policy"]["payout_yen"]==2000
        assert point["policy"]["unspent_cash_yen"]==1700
        assert point["by_year"]["2019"]["policy"]==point["policy"]
        print("Flat independent synthetic checks passed: selection, tie/cap, 100yen, cash, annual profit.")
    else:
        result = audit()
        output = Path(__file__).with_name("independent_final.json")
        with output.open("x") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        print(json.dumps(dict(evidence=str(output), counters=result["counters"], blockers=[])))
