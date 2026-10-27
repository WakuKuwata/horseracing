"""Research-only, strictly past annual market-model experiments (142).

Commands: inventory, benchmark, screen, evaluate.  Screen selects one binary
configuration on 2016--2018 only. Evaluate never changes that selection. Every
fit has its own booster, training-only category vocabulary and audited receipt.
No production loader or database is imported. Outputs must live in artifacts/.
"""
from __future__ import annotations

import os
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_key] = "1"

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.special import expit

SOURCE = Path(__file__).resolve()
ROOT = SOURCE.parents[3]
sys.path.insert(0, str(SOURCE.parents[1]))
import direct_return_model as legacy  # noqa: E402

LOWER = "2007-01-01"
UPPER = "2026-06-26"
SCREEN_YEARS = (2016, 2017, 2018)
OUTER_YEARS = tuple(range(2019, 2027))
DROP = set(legacy.GROUPS["sameday"] + legacy.GROUPS["weightlive"])
NUM = [x for x in legacy.NUM if x not in DROP]
CAT = [x for x in legacy.CAT if x not in DROP]
FEATURES = NUM + CAT
assert len(FEATURES) == 77
BASE_PARAMS = dict(learning_rate=.05, num_leaves=63, min_data_in_leaf=1000,
                   feature_fraction=.8, bagging_fraction=.8, bagging_freq=1,
                   lambda_l2=10., verbosity=-1, num_threads=1, max_bin=255,
                   deterministic=True, force_row_wise=True)
# Six fixed configurations, not an adaptive search. First is the current shape.
CANDIDATES = (
    dict(id="b63_m1000_l10", num_leaves=63, min_data_in_leaf=1000, lambda_l2=10.),
    dict(id="b31_m1000_l10", num_leaves=31, min_data_in_leaf=1000, lambda_l2=10.),
    dict(id="b63_m2000_l10", num_leaves=63, min_data_in_leaf=2000, lambda_l2=10.),
    dict(id="b31_m2000_l10", num_leaves=31, min_data_in_leaf=2000, lambda_l2=10.),
    dict(id="b63_m1000_l30", num_leaves=63, min_data_in_leaf=1000, lambda_l2=30.),
    dict(id="b31_m2000_l30", num_leaves=31, min_data_in_leaf=2000, lambda_l2=30.),
)
MAX_ROUNDS = 600
PATIENCE = 40
FIXED_ROUNDS = 300
# Source row aliases duplicate horse #15 while horse #16 is absent. Neither
# deleting one row nor guessing an ID repairs a complete starting field.
EXCLUDED_RACES = {"202504040407": "duplicate horse_number=15 under horse_id and nk:horse_id aliases; horse_number=16 missing; whole race excluded"}


def model_protocol() -> dict:
    return dict(candidates=list(CANDIDATES), max_rounds=MAX_ROUNDS, patience=PATIENCE,
                fixed_rounds=FIXED_ROUNDS, screen_seed=1, selection_metric="pooled_equal_race_winner_NLL",
                earlystop_metric="binary_logloss", softmax_bagging_fraction=1.)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def save_json(path: Path, obj) -> None:
    with path.open("x") as stream:
        stream.write(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def bounded_rows(raw: pd.DataFrame, upper: str = UPPER) -> pd.DataFrame:
    """First operation after read: remove all rows outside the declared window."""
    d = raw.loc[raw["race_date"].between(LOWER, min(upper, UPPER))].copy()
    dates = pd.to_datetime(d["race_date"], errors="raise")
    if not np.array_equal(d["year"].to_numpy(), dates.dt.year.to_numpy()):
        raise ValueError("year/date mismatch")
    # Drop whole races, never individual starters, when result completeness fails.
    eligible = (d["race_ok"] & ~d["dead_heat"]).groupby(d["race_id"]).transform("all")
    d = d.loc[eligible].sort_values(["race_id", "horse_number"], kind="stable").reset_index(drop=True)
    excluded = [dict(race_id=rid, reason=reason, rows=int((d.race_id == rid).sum()))
                for rid, reason in EXCLUDED_RACES.items() if (d.race_id == rid).any()]
    d = d.loc[~d.race_id.isin(EXCLUDED_RACES)].reset_index(drop=True)
    validate_races(d)
    d.attrs["excluded_races"] = excluded
    return d


def read_rows(path: Path, upper: str = UPPER) -> pd.DataFrame:
    columns = list(dict.fromkeys(["race_id", "horse_id", "race_date", "year", "race_ok", "dead_heat", "won"] + FEATURES))
    # Filtering also happens immediately in bounded_rows, independent of parquet engine pushdown.
    return bounded_rows(pd.read_parquet(path, columns=columns, filters=[("race_date", ">=", LOWER), ("race_date", "<=", min(upper, UPPER))]), upper)


def segments(race_ids) -> tuple[np.ndarray, np.ndarray]:
    ids = np.asarray(race_ids)
    if not len(ids):
        raise ValueError("empty race groups")
    starts = np.r_[0, np.flatnonzero(ids[1:] != ids[:-1]) + 1]
    if len(np.unique(ids[starts])) != len(starts):
        raise ValueError("race groups are not contiguous")
    return starts, np.diff(np.r_[starts, len(ids)])


def validate_races(d: pd.DataFrame) -> None:
    if d.empty:
        raise ValueError("empty bounded data")
    if d.duplicated(["race_id", "horse_id"]).any() or d.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("duplicate starter")
    if not d["race_id"].astype(str).str.fullmatch(r"\d{12}").all():
        raise ValueError("race_id must have 12 digits")
    starts, counts = segments(d["race_id"])
    if np.any(counts < 2) or not np.array_equal(np.repeat(counts, counts), d["field_size"].to_numpy()):
        raise ValueError("incomplete race group / field_size mismatch")
    y = d["won"].to_numpy(float)
    if not np.isin(y, [0., 1.]).all() or np.any(np.add.reduceat(y, starts) != 1):
        raise ValueError("each complete non-dead-heat race needs exactly one winner")
    for column in ("year", "race_date"):
        if d.groupby("race_id")[column].nunique().max() != 1:
            raise ValueError("race spans multiple years/dates")
    if not np.isfinite(d.odds).all() or (d.odds < 1).any():
        raise ValueError("invalid odds")


def annual_split(d: pd.DataFrame, year: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Inner train <= y-2, early-stop validation y-1, untouched target y."""
    tr = d.loc[d.year < year - 1]
    va = d.loc[d.year == year - 1]
    te = d.loc[d.year == year]
    if any(x.empty for x in (tr, va, te)):
        raise ValueError(f"missing annual split for {year}")
    if not (tr.race_date.max() < va.race_date.min() <= va.race_date.max() < te.race_date.min()):
        raise ValueError("non-past annual split")
    return tr, va, te


def category_maps(train: pd.DataFrame) -> dict:
    """Only observed training categories; unknown and missing both encode as NaN."""
    return {c: {str(v): i for i, v in enumerate(pd.unique(train[c].dropna().astype(str)))} for c in CAT}


def design_matrix(d: pd.DataFrame, maps: dict) -> np.ndarray:
    x = d[NUM].to_numpy(dtype=np.float32, copy=True)
    cats = [d[c].astype("string").map(maps[c]).to_numpy(dtype=np.float32, na_value=np.nan) for c in CAT]
    return np.column_stack([x] + cats)


def race_softmax(raw, counts) -> np.ndarray:
    counts = np.asarray(counts, dtype=np.int64)
    raw = np.asarray(raw, dtype=float)
    if np.any(counts < 1) or counts.sum() != len(raw) or not np.isfinite(raw).all():
        raise ValueError("invalid logits or group sizes")
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    z = np.exp(raw - np.repeat(np.maximum.reduceat(raw, starts), counts))
    return z / np.repeat(np.add.reduceat(z, starts), counts)


def softmax_derivatives(raw, labels, counts) -> tuple[np.ndarray, np.ndarray]:
    p = race_softmax(raw, counts)
    # 2 diag(p*(1-p)) - (diag(p)-p p') equals
    # sum_{i<j} p_i*p_j*(e_i+e_j)*(e_i+e_j)' and is therefore PSD.
    # This is a safe diagonal Hessian majorizer for every complete race.
    return p - np.asarray(labels), np.maximum(2. * p * (1. - p), 1e-8)


def softmax_objective(counts):
    return lambda raw, ds: softmax_derivatives(raw, ds.get_label(), counts)


def race_normalize(p, counts) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-15, 1.)
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    return p / np.repeat(np.add.reduceat(p, starts), counts)


def expected_calibration_error(y, p, bins=10) -> float:
    idx = np.minimum((np.asarray(p) * bins).astype(int), bins - 1)
    return float(sum(abs(np.sum(np.asarray(y)[idx == b] - np.asarray(p)[idx == b])) for b in range(bins)) / len(y))


def metrics(d: pd.DataFrame, p) -> dict:
    starts, counts = segments(d.race_id)
    y = d.won.to_numpy(float)
    p = np.clip(np.asarray(p, dtype=float), 1e-15, 1. - 1e-15)
    pr = race_normalize(p, counts)
    losses = -np.add.reduceat(y * np.log(pr), starts)
    return dict(rows=len(d), races=len(counts), winner_nll=float(losses.mean()),
                winner_nll_sum=float(losses.sum()), ece_race=expected_calibration_error(y, pr),
                ece_raw=expected_calibration_error(y, p), binary_logloss=float(-np.mean(y * np.log(p) + (1-y)*np.log1p(-p))))


def run_identity(args) -> dict:
    return dict(version=1, command=args.command, rows=str(args.rows.resolve()), input_sha256=sha(args.rows),
                source_sha256=sha(SOURCE), dependency_sha256={"direct_return_model": sha(Path(legacy.__file__))},
                python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, lightgbm=lgb.__version__,
                lower=LOWER, upper=UPPER, features=FEATURES, candidates=CANDIDATES,
                max_rounds=MAX_ROUNDS, patience=PATIENCE, fixed_rounds=FIXED_ROUNDS,
                seed=args.seed, year=args.year, arms=args.arms, benchmark_rounds=args.rounds,
                protocol_sha256=sha(args.protocol) if args.protocol else None,
                selection_sha256=sha(args.selection) if args.selection else None,
                research_only=True, can_adopt=False, untouched_holdout=False)


def verify_protocol(path: Path, rows: Path) -> dict:
    """Require the frozen sidecar, exact recipe, and all input/source hashes."""
    if sha(path) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("protocol sidecar hash mismatch")
    protocol = json.loads(path.read_text())
    expected = dict(feature="142-roi-policy-model-improvement", version=1,
                    seeds=list(range(1, 16)), max_workers=2, threads_each=1, can_adopt=False,
                    untouched_holdout=False, models=model_protocol())
    for key, value in expected.items():
        if canonical(protocol.get(key)) != canonical(value):
            raise ValueError(f"protocol constraint differs: {key}")
    window = protocol["window"]
    for key, value in dict(training_from=2007, through=UPPER, model_inner=list(SCREEN_YEARS), model_outer=list(OUTER_YEARS)).items():
        if window.get(key) != value:
            raise ValueError(f"protocol window differs: {key}")
    hashes = protocol["inputs_and_source_sha256"]
    for required in (rows.resolve(), SOURCE):
        key = str(required.relative_to(ROOT))
        if key not in hashes:
            raise ValueError(f"protocol does not bind {key}")
    for name, digest in hashes.items():
        path_to_verify = (ROOT / name).resolve()
        if not path_to_verify.is_relative_to(ROOT) or sha(path_to_verify) != digest:
            raise ValueError(f"protocol input/source hash mismatch: {name}")
    return protocol


def prepare_run(out: Path, identity: dict, resume: bool) -> None:
    out = out.resolve()
    if not out.is_relative_to(ROOT / "artifacts"):
        raise ValueError("research output must be inside this repository's artifacts/")
    marker = out / "identity.json"
    if out.exists():
        if not resume or not marker.exists() or canonical(json.loads(marker.read_text())) != canonical(identity):
            raise ValueError("output exists or complete run identity differs; choose a new output directory")
    else:
        out.mkdir(parents=True)
        save_json(marker, identity)


def fit_model(train: pd.DataFrame, target: pd.DataFrame, *, arm: str, seed: int, rounds: int,
              output: Path, identity: dict, overrides=None, earlystop=False) -> tuple[np.ndarray, dict]:
    if train.race_date.max() >= target.race_date.min():
        raise ValueError("fit target must be strictly later than training")
    softmax = arm == "softmax"
    overrides = overrides or {}
    recipe = dict(arm=arm, seed=seed, rounds=rounds, overrides=overrides, earlystop=earlystop,
                  train_start=str(train.race_date.min()), train_end=str(train.race_date.max()),
                  target_start=str(target.race_date.min()), target_end=str(target.race_date.max()),
                  train_rows=len(train), target_rows=len(target), identity=identity,
                  excluded_races=train.attrs.get("excluded_races", target.attrs.get("excluded_races", [])))
    receipt_path = output / "receipt.json"
    if output.exists():
        if not receipt_path.exists():
            raise ValueError(f"incomplete fit output; cannot safely resume: {output}")
        receipt = json.loads(receipt_path.read_text())
        if canonical(receipt["recipe"]) != canonical(recipe):
            raise ValueError("resumed fit recipe differs")
        for name, digest in receipt["hashes"].items():
            if sha(output / name) != digest:
                raise ValueError("resumed fit file hash differs")
        predictions = pd.read_parquet(output / "predictions.parquet")
        keys = ["race_id", "horse_id", "race_date", "year"]
        if not predictions[keys].reset_index(drop=True).equals(target[keys].reset_index(drop=True)):
            raise ValueError("resumed prediction rows differ")
        return predictions.p.to_numpy(), receipt
    output.mkdir(parents=True)
    start = time.monotonic()
    maps = category_maps(train)
    xtr, xte = design_matrix(train, maps), design_matrix(target, maps)
    _, tr_counts = segments(train.race_id)
    _, te_counts = segments(target.race_id)
    params = dict(BASE_PARAMS, seed=seed, **overrides)
    params["objective"] = softmax_objective(tr_counts) if softmax else "binary"
    if softmax:
        params.update(metric="None", bagging_fraction=1., bagging_freq=0, boost_from_average=False)
    else:
        params["metric"] = "binary_logloss"
    categorical = list(range(len(NUM), len(FEATURES)))
    ds = lgb.Dataset(xtr, train.won.to_numpy(float), categorical_feature=categorical, feature_name=FEATURES)
    kwargs = {}
    if earlystop:
        if softmax:
            raise ValueError("softmax is a single fixed-round configuration")
        val = lgb.Dataset(xte, target.won.to_numpy(float), categorical_feature=categorical, feature_name=FEATURES, reference=ds)
        kwargs = dict(valid_sets=[val], callbacks=[lgb.early_stopping(PATIENCE, verbose=False)])
    booster = lgb.train(params, ds, num_boost_round=rounds, **kwargs)
    best = int(booster.best_iteration or booster.current_iteration())
    raw = booster.predict(xte, raw_score=True, num_iteration=best, num_threads=1)
    p = race_softmax(raw, te_counts) if softmax else expit(raw)
    booster_path = output / "model.txt"
    booster.save_model(str(booster_path), num_iteration=best)
    reloaded = lgb.Booster(model_file=str(booster_path))
    loaded_raw = reloaded.predict(xte, raw_score=True, num_threads=1)
    difference = float(np.max(np.abs(loaded_raw - raw)))
    if difference > 1e-12:
        raise ValueError("booster roundtrip prediction mismatch")
    pr = race_normalize(p, te_counts)
    pred = target[["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "field_size"]].copy()
    pred["p"], pred["p_race"], pred["pred"] = p, pr, 100. * (p * target.odds.to_numpy(float) - 1.)
    pred.to_parquet(output / "predictions.parquet", index=False)
    recorded_params = {k: v for k, v in params.items() if k != "objective"}
    spec = dict(features=NUM, cats=CAT, cat_maps=maps, feature_hash=hashlib.sha256(canonical(FEATURES).encode()).hexdigest(),
                objective="race_softmax" if softmax else "binary", params=recorded_params, best_iteration=best,
                train_from=2007, train_through=int(train.year.max()), trained_for_year=int(target.year.min()),
                training_cutoff=str(train.race_date.max()), prediction="softmax(raw,complete_race)" if softmax else "sigmoid(raw)",
                research_only=True, can_adopt=False)
    save_json(output / "feature_spec.json", spec)
    receipt = dict(recipe=recipe, best_iteration=best, actual_iterations=int(booster.current_iteration()),
                   elapsed_s=time.monotonic()-start, metrics=metrics(target, p), roundtrip_max_abs_raw_difference=difference,
                   hashes={name: sha(output / name) for name in ("model.txt", "predictions.parquet", "feature_spec.json")})
    save_json(receipt_path, receipt)
    print(f"{output.name}: n={len(train):,} rounds={best} seconds={receipt['elapsed_s']:.1f}", flush=True)
    return p, receipt


def run_screen(d, args, identity):
    if args.seed != 1:
        raise ValueError("configuration screen is fixed to seed 1")
    records = []
    for candidate in CANDIDATES:
        overrides = {k: v for k, v in candidate.items() if k != "id"}
        sums, races = 0., 0
        folds = []
        for year in SCREEN_YEARS:
            tr, va, te = annual_split(d, year)
            _, selected = fit_model(tr, va, arm="binary", seed=1, rounds=MAX_ROUNDS,
                                    output=args.out / f"{candidate['id']}_{year}_earlystop", identity=identity,
                                    overrides=overrides, earlystop=True)
            _, final = fit_model(pd.concat([tr, va]), te, arm="binary", seed=1, rounds=selected["best_iteration"],
                                 output=args.out / f"{candidate['id']}_{year}_refit", identity=identity, overrides=overrides)
            m = final["metrics"]
            sums += m["winner_nll_sum"]
            races += m["races"]
            folds.append(dict(year=year, best_iteration=selected["best_iteration"], **m))
        records.append(dict(candidate=candidate, winner_nll=sums/races, races=races, folds=folds))
    selected = min(records, key=lambda r: r["winner_nll"])
    result = dict(identity=identity, selection_years=SCREEN_YEARS, selection_seed=1,
                  selection_metric="pooled_equal_race_winner_NLL", tie_rule="fixed_candidate_order",
                  selected=selected["candidate"], candidates=records, can_adopt=False, untouched_holdout=False)
    verify_protocol(args.protocol, args.rows)
    save_json(args.out / "selection.json", result)


def read_selection(path: Path, identity: dict) -> dict:
    selection = json.loads(path.read_text())
    si = selection["identity"]
    for key in ("input_sha256", "source_sha256", "dependency_sha256", "lower", "upper", "features", "candidates", "max_rounds", "patience", "fixed_rounds", "protocol_sha256"):
        if canonical(si[key]) != canonical(identity[key]):
            raise ValueError(f"selection identity mismatch: {key}")
    if selection["selection_years"] != list(SCREEN_YEARS) or selection["selection_seed"] != 1 or selection["selected"] not in CANDIDATES:
        raise ValueError("invalid frozen candidate selection")
    records = selection["candidates"]
    if [r["candidate"] for r in records] != list(CANDIDATES) or min(records, key=lambda r: r["winner_nll"])["candidate"] != selection["selected"]:
        raise ValueError("selection does not follow fixed candidate order / minimum NLL")
    return selection["selected"]


def run_evaluate(d, args, identity):
    if args.year not in OUTER_YEARS:
        raise ValueError("outer year must be 2019..2026")
    selected = read_selection(args.selection, identity) if "earlystop" in args.arms else None
    tr, va, te = annual_split(d, args.year)
    all_train = pd.concat([tr, va])
    records = {}
    for arm in args.arms:
        overrides = {k: v for k, v in selected.items() if k != "id"} if arm == "earlystop" else {}
        rounds = FIXED_ROUNDS
        if arm == "earlystop":
            _, selector = fit_model(tr, va, arm="binary", seed=args.seed, rounds=MAX_ROUNDS,
                                    output=args.out / "earlystop_selector", identity=identity, overrides=overrides, earlystop=True)
            rounds = selector["best_iteration"]
        _, receipt = fit_model(all_train, te, arm="softmax" if arm == "softmax" else "binary", seed=args.seed, rounds=rounds,
                               output=args.out / arm, identity=identity, overrides=overrides)
        records[arm] = dict(metrics=receipt["metrics"], best_iteration=rounds, elapsed_s=receipt["elapsed_s"])
    verify_protocol(args.protocol, args.rows)
    save_json(args.out / "evaluation.json", dict(identity=identity, year=args.year, seed=args.seed, arms=records,
               can_adopt=False, untouched_holdout=False, production_changed=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["inventory", "benchmark", "screen", "evaluate"])
    ap.add_argument("--rows", type=Path, default=ROOT / "artifacts/market_ev/rows_2007.parquet")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, choices=range(1, 16), default=1)
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--arms", nargs="+", choices=["baseline", "earlystop", "softmax"], default=["baseline", "earlystop", "softmax"])
    ap.add_argument("--rounds", type=int, default=10, help="benchmark only; full runs use the frozen round budget")
    ap.add_argument("--protocol", type=Path, help="required frozen protocol input for screen/evaluate")
    ap.add_argument("--selection", type=Path, help="screen/selection.json required for earlystop evaluation")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    if args.command in ("screen", "evaluate") and not args.protocol:
        ap.error("screen/evaluate require --protocol")
    if args.command == "evaluate" and "earlystop" in args.arms and not args.selection:
        ap.error("earlystop evaluation requires --selection")
    if args.command == "benchmark" and not 1 <= args.rounds <= 30:
        ap.error("benchmark rounds must be 1..30")
    if args.protocol:
        verify_protocol(args.protocol, args.rows)
    identity = run_identity(args)
    prepare_run(args.out, identity, args.resume)
    final_name = {"screen": "selection.json", "evaluate": "evaluation.json", "inventory": "inventory.json", "benchmark": "benchmark.json"}[args.command]
    if (args.out / final_name).exists():
        raise ValueError("run already complete; completed outputs are never overwritten")
    start = time.monotonic()
    upper = "2018-12-31" if args.command == "screen" else f"{args.year}-12-31" if args.command in ("evaluate", "benchmark") else UPPER
    d = read_rows(args.rows, upper=upper)
    inventory = dict(rows=len(d), races=int(d.race_id.nunique()), min_date=str(d.race_date.min()), max_date=str(d.race_date.max()),
                     rows_by_year={str(k): int(v) for k, v in d.groupby("year").size().items()}, input_read_s=time.monotonic()-start,
                     excluded_races=d.attrs.get("excluded_races", []))
    if args.command != "inventory" and not (args.out / "dataset_inventory.json").exists():
        save_json(args.out / "dataset_inventory.json", dict(identity=identity, **inventory))
    if args.command == "inventory":
        save_json(args.out / "inventory.json", dict(identity=identity, **inventory))
    elif args.command == "benchmark":
        tr, va, te = annual_split(d, args.year)
        receipts = {}
        for arm in args.arms:
            if arm == "earlystop":
                continue
            _, r = fit_model(pd.concat([tr, va]), te, arm="softmax" if arm == "softmax" else "binary", seed=args.seed,
                             rounds=args.rounds, output=args.out / arm, identity=identity)
            receipts[arm] = dict(elapsed_s=r["elapsed_s"], rounds=r["actual_iterations"],
                                 seconds_per_round_including_setup=r["elapsed_s"]/r["actual_iterations"])
        save_json(args.out / "benchmark.json", dict(identity=identity, inventory=inventory, fits=receipts,
                    projection_warning="Includes encoding, bin construction, serialization and prediction; do not extrapolate as pure per-tree cost."))
    elif args.command == "screen":
        # Early screen is forbidden from accessing outer-period features or labels.
        run_screen(d.loc[d.year <= max(SCREEN_YEARS)], args, identity)
    else:
        run_evaluate(d.loc[d.year <= args.year], args, identity)
    if args.protocol:
        verify_protocol(args.protocol, args.rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
