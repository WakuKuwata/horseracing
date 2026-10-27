"""Independent 143 artifact audit. Does not import the experiment's implementation.

Run after a completed report with training/.venv/bin/python PATH --stage features
or --stage weighted. --self-test exercises selection, cash, labels and pairing.
No fitting, database access, mutation of research outputs or parameter selection.
"""
from __future__ import annotations

import os
for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[key] = "1"

import argparse
import hashlib
import json
from pathlib import Path
import platform

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/143-existing-feature-roi"
OLD = ROOT / "artifacts/142-roi-policy-model-improvement"
KEYS = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "field_size"]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for piece in iter(lambda: stream.read(1 << 20), b""):
            h.update(piece)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def equal(a, b, label="value"):
    if isinstance(a, dict):
        assert set(a) <= set(b), (label, set(a) - set(b))
        for key, value in a.items():
            equal(value, b[key], f"{label}.{key}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), label
        for i, (x, y) in enumerate(zip(a, b)):
            equal(x, y, f"{label}[{i}]")
    elif isinstance(a, (int, float, np.number)) and not isinstance(a, bool):
        assert np.isclose(a, b, atol=1e-10, rtol=1e-12, equal_nan=True), (label, a, b)
    else:
        assert a == b, (label, a, b)


def quality(frame):
    y = frame.won.to_numpy(float)
    p = np.clip(frame.p.to_numpy(float), 1e-15, 1 - 1e-15)
    sums = pd.Series(p).groupby(frame.race_id.to_numpy()).transform("sum").to_numpy()
    pn = p / sums
    losses = pd.Series(-y * np.log(pn)).groupby(frame.race_id.to_numpy()).sum()
    def ece(v):
        bins = np.minimum((v * 10).astype(int), 9)
        return float(sum(abs((y[bins == i] - v[bins == i]).sum()) for i in range(10)) / len(y))
    return dict(rows=len(frame), races=len(losses), winner_nll=float(losses.mean()),
                winner_nll_sum=float(losses.sum()), ece_race=ece(pn), ece_raw=ece(p),
                binary_logloss=float(-np.mean(y * np.log(p) + (1-y) * np.log1p(-p))))


def point(races, bought):
    stake, payout, budget = (float(races[c].sum()) for c in ("stake_yen", "payout_yen", "budget_yen"))
    profit = payout - stake
    wealth = np.r_[0., races.profit_yen.cumsum().to_numpy()]
    losing = longest = 0
    for value in races.loc[races.stake_yen > 0, "profit_yen"]:
        losing = losing + 1 if value < 0 else 0
        longest = max(longest, losing)
    wins = np.sort(bought.payout_yen.to_numpy())[::-1]
    top1, top5 = float(wins[:1].sum()), float(wins[:5].sum())
    ratio = lambda a, b: float(a / b) if b else None
    return dict(n_races=len(races), n_days=int(races.race_date.nunique()),
                n_betting_races=int((races.stake_yen > 0).sum()), n_bets=len(bought),
                n_hits=int(bought.won.sum()), n_hit_races=int((races.payout_yen > 0).sum()),
                skip_rate=float((races.stake_yen == 0).mean()), stake_yen=stake, payout_yen=payout,
                profit_yen=profit, budget_yen=budget, unspent_cash_yen=budget-stake,
                budget_return=ratio(profit, budget), deployed_roi=ratio(payout, stake),
                max_race_settlement_drawdown_yen=float((np.maximum.accumulate(wealth)-wealth).max()),
                max_consecutive_losing_races=longest,
                profit_without_top1_payout_yen=profit-top1, profit_without_top5_payouts_yen=profit-top5,
                budget_return_without_top1_payout=ratio(profit-top1, budget),
                budget_return_without_top5_payouts=ratio(profit-top5, budget),
                top1_payout_share=ratio(top1, payout), top5_payout_share=ratio(top5, payout))


def score(frame):
    """Select each race from raw EV and integer stakes with independent per-race loops."""
    d = frame.sort_values(["race_date", "race_id", "horse_number", "horse_id"]).reset_index(drop=True)
    assert not d.duplicated(["race_id", "horse_id"]).any()
    assert not d.duplicated(["race_id", "horse_number"]).any()
    assert d.p.between(0, 1).all() and np.isfinite(d.p).all()
    d["p_eval"] = np.clip(d.p.to_numpy(float), 1e-15, 1-1e-15)
    d["ev"] = d.p_eval * d.odds
    d["units"] = 0
    for rid, field in d.groupby("race_id", sort=False):
        assert len(field) == field.field_size.iloc[0] and field.won.sum() == 1
        eligible = field.loc[field.ev > 1.2].sort_values(
            ["ev", "horse_number", "horse_id"], ascending=[False, True, True], kind="stable").head(10)
        if len(eligible):
            d.loc[eligible.index, "units"] = 10 // len(eligible)
    d["stake_yen"] = 100 * d.units
    d["payout_yen"] = 100 * d.units * d.won * d.odds
    rows = []
    for (date, rid), field in d.groupby(["race_date", "race_id"], sort=True):
        stake = float(field.stake_yen.sum())
        pay = float(field.payout_yen.sum())
        normalized_win = float(field.loc[field.won.astype(bool), "p_eval"].iloc[0] / field.p_eval.sum())
        rows.append(dict(race_date=date, race_id=rid, stake_yen=stake, payout_yen=pay,
                         profit_yen=pay-stake, budget_yen=1000, winner_nll=-np.log(normalized_win)))
    races = pd.DataFrame(rows)
    bets = d.loc[d.units > 0].copy()
    result = dict(quality=quality(d), policy=point(races, bets), by_year={}, ev_band_calibration={})
    for label, low, high in (("le1", -np.inf, 1.), ("1to1_2", 1., 1.2), ("gt1_2", 1.2, np.inf)):
        selected = d.loc[d.ev.gt(low) & d.ev.le(high)]
        total_p, total_y = float(selected.p_eval.sum()), int(selected.won.sum())
        result["ev_band_calibration"][label] = dict(n=len(selected), won=total_y, sum_p=total_p,
                                                   won_over_p=total_y/total_p if total_p else None)
    for year, group in d.groupby("year"):
        ids = set(group.race_id)
        result["by_year"][str(year)] = dict(quality=quality(group.reset_index(drop=True)),
            policy=point(races.loc[races.race_id.isin(ids)], bets.loc[bets.race_id.isin(ids)]))
    return result, races, bets


def bootstrap(tables):
    base = tables["baseline"]
    dates = sorted(base.race_date.unique())
    draws = np.random.default_rng(20261019).multinomial(len(dates), np.full(len(dates), 1 / len(dates)), size=4000)
    def daily(frame):
        return frame.groupby("race_date").agg(
            stake=("stake_yen", "sum"), payout=("payout_yen", "sum"), budget=("budget_yen", "sum"),
            profit=("profit_yen", "sum"), nll=("winner_nll", "sum"), races=("race_id", "size")).reindex(dates)
    old = daily(base)
    results = {}
    def intervals(point_value, values):
        good = np.asarray(values)[np.isfinite(values)]
        return dict(difference=float(point_value) if point_value is not None else None,
                    ci95=np.quantile(good, [.025, .975]).tolist() if len(good) else None,
                    bonferroni_familywise95_ci=np.quantile(good, [.05/6, 1-.05/6]).tolist() if len(good) else None,
                    valid_replicates=len(good))
    for arm, frame in tables.items():
        if arm in ("baseline", "current_reference"):
            continue
        assert base[["race_date", "race_id", "budget_yen"]].equals(frame[["race_date", "race_id", "budget_yen"]])
        new = daily(frame)
        old_stake, new_stake = draws @ old.stake, draws @ new.stake
        old_roi = np.divide(draws @ old.payout, old_stake, out=np.full(4000, np.nan), where=old_stake > 0)
        new_roi = np.divide(draws @ new.payout, new_stake, out=np.full(4000, np.nan), where=new_stake > 0)
        roi_point = (new.payout.sum()/new.stake.sum()-old.payout.sum()/old.stake.sum()
                     if new.stake.sum() > 0 and old.stake.sum() > 0 else None)
        results[arm] = {
            "winner_nll": intervals((new.nll-old.nll).sum()/old.races.sum(), draws @ (new.nll-old.nll)/(draws @ old.races)),
            "profit_per_budget": intervals((new.profit-old.profit).sum()/old.budget.sum(), draws @ (new.profit-old.profit)/(draws @ old.budget)),
            "deployed_roi": intervals(roi_point, new_roi-old_roi)}
    return results


def ensemble(arm, protocol, counter):
    yearly = []
    runtime = dict(python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, lightgbm=lgb.__version__)
    rows_hash = sha(ROOT / protocol["rows"])
    additions_hash = sha(ROOT / protocol["additions"])
    for year in range(2019, 2027):
        first, total = None, None
        for seed in range(1, 16):
            if arm == "baseline":
                folder = OLD / f"models/seed_{seed:02d}/year_{year}/baseline"
            else:
                stage = "weighted_models" if arm == "weighted_baseline" else "models"
                folder = OUT / stage / f"seed_{seed:02d}/year_{year}/{arm}"
            receipt = read(folder / "receipt.json")
            rec = receipt["recipe"]
            assert rec["seed"] == seed and rec["rounds"] == 300
            equal(dict(runtime, year=year, seed=seed), rec["identity"], f"{arm}.runtime")
            assert rec["train_end"] < rec["target_start"]
            assert rec["train_start"].startswith("2007-")
            assert receipt["actual_iterations"] == 300
            assert receipt["roundtrip_max_abs_raw_difference"] <= 1e-12
            for name, digest in receipt["hashes"].items():
                assert sha(folder / name) == digest
                counter["artifact_hashes"] += 1
            spec = read(folder / "feature_spec.json")
            assert spec["trained_for_year"] == year and spec["train_through"] == year-1
            assert spec["training_cutoff"] == rec["train_end"] and spec["objective"] == "binary"
            expected_features = protocol["base_features"]
            if arm not in ("baseline", "weighted_baseline"):
                expected_features = expected_features[:-11] + protocol["bundles"][arm] + expected_features[-11:]
            feature_order = spec.get("feature_order", spec["features"] + spec["cats"])
            assert feature_order == expected_features
            assert spec["cats"] == protocol["base_features"][-11:]
            if arm == "baseline":
                assert rec["arm"] == "binary" and rec["earlystop"] is False and rec["overrides"] == {}
                assert rec["identity"]["input_sha256"] == rows_hash
                assert rec["identity"]["features"] == protocol["base_features"]
                expected_params = dict(protocol["models"]["params"], seed=seed, metric="binary_logloss")
            else:
                assert rec["arm"] == arm
                assert rec["identity"]["protocol_sha256"] == sha(OUT / "protocol.json")
                assert rec["identity"]["rows_sha256"] == rows_hash
                assert rec["identity"]["additions_sha256"] == additions_hash
                assert rec["params"] == spec["params"]
                expected_params = dict(protocol["models"]["params"], seed=seed, metric="binary_logloss", objective="binary")
                assert rec["features"] == expected_features
                assert (rec["weights"] is not None) == (arm == "weighted_baseline")
            assert spec["params"] == expected_params
            p = pd.read_parquet(folder / "predictions.parquet")
            assert p.year.eq(year).all() and len(p) == rec["target_rows"]
            assert p.p.between(0, 1).all() and np.isfinite(p.p).all()
            if first is None:
                first = p[KEYS].copy()
                total = np.zeros(len(p))
            else:
                assert first.equals(p[KEYS])
            total += p.p.to_numpy(float)
            counter["fits"] += 1
        first["p"] = total / 15
        yearly.append(first)
    return pd.concat(yearly, ignore_index=True)


def replay(arms, protocol):
    checks = []
    extras = pd.read_parquet(ROOT / protocol["additions"])
    extras = extras.set_index(["race_id", "horse_id"])
    for year in (2019, 2024, 2026):
        base_cols = list(dict.fromkeys(["race_id", "horse_id"] + protocol["base_features"]))
        original = pd.read_parquet(ROOT / protocol["rows"], columns=base_cols, filters=[("year", "=", year)])
        original = original.set_index(["race_id", "horse_id"])
        for seed in (1, 8, 15):
            for arm in arms:
                stage = "weighted_models" if arm == "weighted_baseline" else "models"
                folder = OUT / stage / f"seed_{seed:02d}/year_{year}/{arm}"
                spec = read(folder / "feature_spec.json")
                pred = pd.read_parquet(folder / "predictions.parquet").iloc[:64]
                idx = pd.MultiIndex.from_frame(pred[["race_id", "horse_id"]])
                frame = original.loc[idx].join(extras.loc[idx], validate="one_to_one")
                x = np.column_stack([frame[spec["features"]].to_numpy(np.float32)] + [
                    frame[c].astype("string").map(spec["cat_maps"][c]).to_numpy(dtype=np.float32, na_value=np.nan)
                    for c in spec["cats"]])
                model = lgb.Booster(model_file=str(folder / "model.txt"))
                assert model.feature_name() == spec["feature_order"]
                actual = expit(model.predict(x, raw_score=True, num_threads=1))
                np.testing.assert_array_equal(actual, pred.p.to_numpy())
                checks.append(dict(year=year, seed=seed, arm=arm, rows=len(pred), max_abs_p_difference=0.))
    return checks


def weights_audit(protocol):
    weights = pd.read_parquet(ROOT / protocol["weights"])
    source = pd.read_parquet(OLD / "model_comparison/baseline_ensemble.parquet")
    source = source.loc[source.year.le(2025)].reset_index(drop=True)
    assert weights[["race_id", "horse_id"]].equals(source[["race_id", "horse_id"]])
    np.testing.assert_array_equal(weights.p_oof.to_numpy(), source.p.to_numpy())
    assert weights.year.between(2019, 2025).all() and (weights.oof_train_through == weights.year-1).all()
    columns = ["race_id", "horse_id", "year", "odds", "race_date", "race_ok", "dead_heat"]
    pool = pd.read_parquet(ROOT / protocol["rows"], columns=columns, filters=[("race_date", "<=", "2026-06-26")])
    ok = (pool.race_ok & ~pool.dead_heat).groupby(pool.race_id).transform("all")
    pool = pool.loc[ok & pool.race_id.ne("202504040407")].sort_values(["race_id", "horse_id"])
    # Hash comparison must follow the fit's horse-number order, not lexicographic horse IDs.
    order = pd.read_parquet(ROOT / protocol["rows"], columns=["race_id", "horse_id", "horse_number"],
                            filters=[("race_date", "<=", "2026-06-26")])
    pool = pool.merge(order, on=["race_id", "horse_id"], validate="one_to_one").sort_values(["race_id", "horse_number"])
    checks = []
    for year in range(2019, 2027):
        tr = pool.loc[pool.year.lt(year)].copy().reset_index(drop=True)
        m = tr.merge(weights[["race_id", "horse_id", "p_oof", "oof_train_through"]],
                     on=["race_id", "horse_id"], how="left", sort=False, validate="one_to_one")
        available = m.p_oof.notna()
        assert (m.loc[available, "oof_train_through"] < m.loc[available, "year"]).all()
        informative = available.groupby(m.race_id).transform("all")
        w = np.ones(len(m))
        ev = m.loc[informative, "p_oof"].to_numpy() * m.loc[informative, "odds"].to_numpy()
        w[informative] += np.exp(-.5*((ev-1.2)/.2)**2)
        if informative.any():
            w[informative] /= w[informative].mean()
        assert np.array_equal(w[~informative], np.ones((~informative).sum()))
        digest = hashlib.sha256(w.tobytes()).hexdigest()
        for seed in range(1, 16):
            rec = read(OUT / f"weighted_models/seed_{seed:02d}/year_{year}/weighted_baseline/receipt.json")
            info = rec["recipe"]["weights"]
            assert info["vector_sha256"] == digest
            equal(dict(available=int(available.sum()), informative_rows=int(informative.sum()),
                       neutral_rows=int((~informative).sum()), mean=float(w.mean()),
                       minimum=float(w.min()), maximum=float(w.max())), info)
            if year == 2019:
                old = pd.read_parquet(OLD / f"models/seed_{seed:02d}/year_2019/baseline/predictions.parquet")
                new = pd.read_parquet(OUT / f"weighted_models/seed_{seed:02d}/year_2019/weighted_baseline/predictions.parquet")
                assert old[KEYS].equals(new[KEYS])
                np.testing.assert_array_equal(old.p.to_numpy(), new.p.to_numpy())
        checks.append(dict(year=year, train_rows=len(tr), informative_rows=int(informative.sum()),
                           neutral_rows=int((~informative).sum()), vector_sha256=digest))
    return checks


def audit(stage):
    protocol = read(OUT / "protocol.json")
    assert sha(OUT / "protocol.json") == (OUT / "protocol.sha256").read_text().strip()
    for name, digest in protocol["inputs_and_source_sha256"].items():
        assert sha(ROOT / name) == digest
    all_arms = ["speed", "pace"] + (["weighted_baseline"] if stage == "weighted" else [])
    count = dict(fits=0, artifact_hashes=0)
    frames = {arm: ensemble(arm, protocol, count) for arm in ["baseline"] + all_arms}
    baseline = frames["baseline"]
    for arm, frame in frames.items():
        assert baseline[KEYS].equals(frame[KEYS]), arm
    results = {}
    for report_stage in (["features", "weighted"] if stage == "weighted" else ["features"]):
        folder = OUT / f"{report_stage}_comparison"
        saved = read(folder / "summary.json")
        current = pd.read_parquet(OLD / "existing_policies/inputs.parquet")
        current = current.loc[current.year.ge(2019), KEYS + ["p_raw"]].rename(columns={"p_raw": "p"})
        report_frames = {"current_reference": current, "baseline": baseline}
        for arm in (["speed", "pace"] if report_stage == "features" else ["weighted_baseline"]):
            report_frames[arm] = frames[arm]
        tables, points = {}, {}
        for arm, frame in report_frames.items():
            expected = pd.read_parquet(folder / f"{arm}_ensemble.parquet")
            assert frame.sort_values(["race_id", "horse_number"]).reset_index(drop=True).equals(
                expected.sort_values(["race_id", "horse_number"]).reset_index(drop=True))
            result, races, bets = score(frame)
            equal(result, saved["arms"][arm], arm)
            stored_races = pd.read_parquet(folder / f"{arm}_races.parquet")
            assert races[["race_date", "race_id"]].equals(stored_races[["race_date", "race_id"]])
            for c in ("stake_yen", "payout_yen", "profit_yen", "budget_yen", "winner_nll"):
                np.testing.assert_allclose(races[c], stored_races[c], atol=1e-12, rtol=1e-12)
            stored_bets = pd.read_parquet(folder / f"{arm}_bets.parquet")
            assert bets[["race_id", "horse_id"]].reset_index(drop=True).equals(stored_bets[["race_id", "horse_id"]].reset_index(drop=True))
            np.testing.assert_array_equal(bets.units.to_numpy(), stored_bets.units.to_numpy())
            np.testing.assert_allclose(bets.payout_yen, stored_bets.payout_yen, atol=1e-10)
            tables[arm], points[arm] = races, result["policy"]
        paired = bootstrap(tables)
        equal(paired, saved["paired"]["results"], report_stage)
        results[report_stage] = dict(points=points, paired=paired, summary_sha256=sha(folder / "summary.json"))
    replay_checks = replay(all_arms, protocol)
    weight_checks = weights_audit(protocol) if stage == "weighted" else None
    if stage == "weighted":
        marker = read(OUT / "comparison_marker.json")
        assert marker["protocol_sha256"] == sha(OUT / "protocol.json")
        assert marker["summary_sha256"] == sha(ROOT / marker["summary_path"])
        assert sha(OUT / "comparison_marker.json") == (OUT / "comparison_marker.sha256").read_text().strip()
    return dict(auditor="feature143_review", protocol_sha256=sha(OUT / "protocol.json"), stage=stage,
                counters=count, new_candidate_fits_verified=120*len(all_arms), baseline_reused_fits_verified=120,
                all_recipe_and_hash_checks_passed=True, reports=results,
                independent_saved_body_replays=replay_checks, independent_weights=weight_checks,
                no_model_fits_performed=True, can_adopt=False, untouched_holdout=False, blockers=[])


def self_test():
    rows = []
    for i in range(3):
        for horse in range(12):
            rows.append(dict(race_id=f"20190101000{i+1}", horse_id=f"h{horse:02d}", horse_number=horse+1,
                race_date=f"2019-01-0{i+1}", year=2019, field_size=12, won=int(horse == 1),
                odds=10., p=.15 if i == 0 else (.03 if i == 1 else (.2 if horse < 3 else .02))))
    d = pd.DataFrame(rows)
    result, races, bets = score(d)
    assert len(bets) == 13 and result["policy"]["stake_yen"] == 1900
    assert list(races.stake_yen) == [1000, 0, 900]
    assert result["policy"]["payout_yen"] == 4000
    assert list(bets.loc[bets.race_id.eq("201901010001"), "horse_number"]) == list(range(1, 11))
    changed = d.copy(); changed["won"] = changed.horse_number.eq(12).astype(int)
    _, _, again = score(changed)
    assert bets[["race_id", "horse_id", "units"]].equals(again[["race_id", "horse_id", "units"]])
    zero = bootstrap({"baseline": races, "speed": races})
    for value in zero["speed"].values():
        assert value["difference"] == 0 and value["ci95"] == [0, 0]
        assert value["bonferroni_familywise95_ci"] == [0, 0]
    print("Independent synthetic checks passed: cap/tie, cash, payout, label independence, identical pairing.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--stage", choices=["features", "weighted"])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
    if args.stage:
        result = audit(args.stage)
        result["audit_script_sha256"] = sha(__file__)
        path = Path(__file__).with_name(f"independent_{args.stage}.json")
        with path.open("x") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        print(json.dumps(dict(stage=args.stage, evidence=str(path), counters=result["counters"], blockers=[])))
