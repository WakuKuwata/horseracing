"""142: frozen, local-only closing-odds policy research (never a live-buy backtest)."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from horseracing_eval.market_mix import MixCoefficients, mix_probabilities

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
MAX_DATE = "2026-06-26"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
COEFFICIENTS = HERE.parent / "market_mix_20261019/coefficients.json"
ARCHIVE = ROOT / "artifacts/roi_explore/missed_20261004/R05_settlement/archived_final_odds.csv"
DEFAULT_OUT = ROOT / "artifacts/142-roi-policy-model-improvement"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_new(path: Path, value: dict) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def seed_path(seed: int) -> Path:
    tag = "armC_binary" + ("" if seed == 1 else f"_seed{seed}")
    return ROOT / "artifacts/roi_explore/results" / f"{tag}_drop-sameday+weightlive_from2007_ens15/predictions.parquet"


def inputs() -> list[Path]:
    return [ROWS, COEFFICIENTS, ARCHIVE, *(seed_path(s) for s in range(1, 16))]


def source_files() -> list[Path]:
    return [HERE / name for name in ("run.py", "policies.py", "models.py", "experiment.py", "report.py")] + [
        ROOT / "eval/src/horseracing_eval/market_mix.py",
        HERE.parent / "direct_return_model.py",
    ]


def freeze(out: Path) -> dict:
    from models import CANDIDATES, MAX_ROUNDS, PATIENCE, FIXED_ROUNDS
    if (out / "protocol.json").exists():
        raise FileExistsError("142 protocol already exists; verify it instead of refreezing")
    paths = inputs() + source_files()
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError(missing)
    hashes = {str(p.relative_to(ROOT)): sha256(p) for p in paths}
    document = {
        "feature": "142-roi-policy-model-improvement", "version": 1,
        "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window": {"training_from": 2007, "through": MAX_DATE,
                   "policy_primary": [2019, 2026], "policy_secondary": [2013, 2018],
                   "model_inner": [2016, 2017, 2018], "model_outer": list(range(2019, 2027))},
        "scope": "local closing-odds research; new capture and live changes deferred",
        "seeds": list(range(1, 16)), "max_workers": 2, "threads_each": 1,
        "policy_budget_yen_per_race": 1000, "stake_unit_yen": 100,
        "allocation": "up to 10 eligible horses by frozen score; equal floor(10/n) units; remainder stays cash",
        "policies": ["raw_ev_gt_1_2", "mix_ev_gt_1_0", "mix_ev_gt_1_2",
                     "raw_gt_1_2_and_mix_gt_1_0", "market_favorite_reference"],
        "ranking": "raw-only policy uses raw EV; mixed and intersection policies use mix EV; favorite uses odds ascending; ties horse_number,horse_id ascending",
        "models": {"candidates": list(CANDIDATES), "max_rounds": MAX_ROUNDS,
                   "patience": PATIENCE, "fixed_rounds": FIXED_ROUNDS, "screen_seed": 1,
                   "selection_metric": "pooled_equal_race_winner_NLL", "earlystop_metric": "binary_logloss",
                   "softmax_bagging_fraction": 1.0},
        "bootstrap": {"block": "all eligible race dates including no-bet days",
                      "repetitions": 4000, "seed": 20261019,
                      "multiplicity": "Bonferroni intervals in addition to descriptive 95% intervals"},
        "inputs_and_source_sha256": hashes,
        "untouched_holdout": False, "can_adopt": False, "eligible_for_verdict": False,
        "limitations": ["Previously examined development periods; no untouched confirmation sample.",
                        "Closing odds are not decision-time quotes; live execution profitability is unmeasured.",
                        "Main payout = won * stored closing odds; archived official payouts are a separate subset.",
                        "141 coefficients are specific to existing ens15, not transferable to candidate models."],
    }
    out.mkdir(parents=True, exist_ok=True)
    write_new(out / "protocol.json", document)
    with (out / "protocol.sha256").open("x") as stream:
        stream.write(sha256(out / "protocol.json") + "\n")
    return document


def verify(out: Path) -> dict:
    protocol = out / "protocol.json"
    if sha256(protocol) != (out / "protocol.sha256").read_text().strip():
        raise ValueError("142 protocol changed")
    document = json.loads(protocol.read_text())
    for name, wanted in document["inputs_and_source_sha256"].items():
        if sha256(ROOT / name) != wanted:
            raise ValueError(f"142 input/source changed: {name}")
    return document


def bounded_read(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Apply the cutoff in the parquet reader, before any labels enter the comparison."""
    return pd.read_parquet(path, columns=columns, filters=[("race_date", "<=", MAX_DATE), ("year", ">=", 2007)])


def mix_by_year(frame: pd.DataFrame, table: dict) -> np.ndarray:
    probabilities = np.full(len(frame), np.nan)
    for year, idx in frame.groupby("year", sort=True).indices.items():
        record = table["coefficients"].get(str(year))
        if record is None or record["fit_through_year"] >= year or not record["converged"]:
            raise ValueError(f"Missing or non-past mixture for {year}")
        coef = MixCoefficients(**{k: record[k] for k in (
            "year", "a", "b", "fit_from_year", "fit_through_year", "n_races", "n_rows")},
            source_sha256=table["source_sha256"])
        sub = frame.iloc[idx]
        for positions in sub.groupby("race_id", sort=False).indices.values():
            indices = np.asarray(idx)[positions]
            probabilities[indices] = mix_probabilities(
                frame.p_raw.to_numpy()[indices], frame.odds.to_numpy()[indices], coef)
    return probabilities


def load_existing() -> tuple[pd.DataFrame, dict]:
    cols = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "race_ok", "dead_heat", "field_size"]
    raw, excluded = eligible_existing(bounded_read(ROWS, cols))
    raw = raw.sort_values(["race_id", "horse_number", "horse_id"]).reset_index(drop=True)
    key = ["race_id", "horse_id"]
    total = np.zeros(len(raw))
    for seed in range(1, 16):
        pred = bounded_read(seed_path(seed), key + ["race_date", "year", "odds", "pred"])
        joined = raw[key + ["race_date", "year", "odds"]].merge(pred, on=key, how="left", validate="one_to_one", suffixes=("", "_prediction"), sort=False)
        if joined.pred.isna().any() or not joined.race_date.eq(joined.race_date_prediction).all() or not joined.year.eq(joined.year_prediction).all():
            raise ValueError(f"Missing/date-misaligned predictions in seed {seed}")
        np.testing.assert_allclose(joined.odds, joined.odds_prediction, atol=1e-9, rtol=0)
        p = (1 + joined.pred.to_numpy(float) / 100) / joined.odds.to_numpy(float)
        if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
            raise ValueError(f"Invalid seed probabilities {seed}")
        total += p
    raw["p_raw"] = np.clip(total / 15, 1e-6, 1-1e-6)
    table = json.loads(COEFFICIENTS.read_text())
    raw["p_mix"] = mix_by_year(raw, table)
    raw["payout_yen_per_100"] = np.nan
    arc = pd.read_csv(ARCHIVE, dtype={"race_id": str, "horse_id": str})
    arc = arc[arc.race_date <= MAX_DATE]
    # No archive rows in the current closing-odds window: explicitly report this gap.
    if len(arc):
        raise ValueError("Archive coverage changed; implement and independently audit key-level settlement join before use")
    return raw, {"rows": len(raw), "races": raw.race_id.nunique(), "days": raw.race_date.nunique(),
                 "from": str(raw.race_date.min()), "through": str(raw.race_date.max()),
                 "archived_official_races_in_window": 0,
                 "excluded_structurally_invalid_races": excluded,
                 "settlement": "closing_odds_proxy", "all_seed_vectors_aligned": True}


def eligible_existing(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Preserve complete fields; never repair a duplicate ID by guessing a mapping."""
    raw = raw[(raw.race_ok & ~raw.dead_heat).groupby(raw.race_id).transform("all") & raw.year.between(2013, 2026)].copy()
    counts = raw.groupby("race_id").horse_id.transform("size")
    invalid = (raw.duplicated(["race_id", "horse_id"], keep=False)
               | raw.duplicated(["race_id", "horse_number"], keep=False)
               | raw.field_size.ne(counts) | raw.groupby("race_id").won.transform("sum").ne(1))
    excluded = sorted(raw.loc[invalid, "race_id"].unique().tolist())
    raw = raw[~raw.race_id.isin(excluded)].copy()
    return raw, excluded


def policies(out: Path) -> dict:
    from policies import evaluate
    verify(out)
    destination = out / "existing_policies"
    destination.mkdir(exist_ok=False)
    frame, inventory = load_existing()
    frame.to_parquet(destination / "inputs.parquet", index=False)
    results = {"inventory": inventory, "untouched_holdout": False, "can_adopt": False,
               "eligible_for_verdict": False, "windows": {}}
    for name, low, high in (("primary_2019_2026", 2019, 2026), ("secondary_2013_2018", 2013, 2018)):
        subset = frame[frame.year.between(low, high)].copy().reset_index(drop=True)
        result = evaluate(subset, bootstrap_repetitions=4000, seed=20261019)
        results["windows"][name] = result["summary"]
        for kind in ("races", "days", "bets"):
            result[kind].to_parquet(destination / f"{name}_{kind}.parquet", index=False)
    results["by_year"] = {}
    for year, subset in frame.groupby("year", sort=True):
        result = evaluate(subset.reset_index(drop=True), bootstrap_repetitions=4000, seed=20261019)
        results["by_year"][str(year)] = result["summary"]
    verify(out)
    write_new(destination / "summary.json", results)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["freeze", "verify", "policies"])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = {"freeze": freeze, "verify": verify, "policies": policies}[args.command](args.out)
    print(json.dumps({"command": args.command, "out": str(args.out),
                      "status": "complete", "inventory": result.get("inventory")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
