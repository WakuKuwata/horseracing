"""Ensemble BEFORE scoring: 142 paired model and same-budget profit comparison."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import models
import policies
import run as research

ARMS = ("baseline", "earlystop", "softmax")


def load_ensemble(out: Path, arm: str, protocol: dict) -> tuple[pd.DataFrame, list]:
    frames, receipts = [], []
    for year in protocol["window"]["model_outer"]:
        base, total = None, None
        for seed in protocol["seeds"]:
            fit = out / "models" / f"seed_{seed:02d}" / f"year_{year}" / arm
            receipt = json.loads((fit / "receipt.json").read_text())
            if receipt["recipe"]["seed"] != seed or receipt["recipe"]["identity"]["protocol_sha256"] != research.sha256(out / "protocol.json"):
                raise ValueError("Model seed/protocol mismatch")
            for name, digest in receipt["hashes"].items():
                if research.sha256(fit / name) != digest:
                    raise ValueError(f"Saved fit changed: {fit / name}")
            current = pd.read_parquet(fit / "predictions.parquet")
            keys = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "field_size"]
            if not current.year.eq(year).all():
                raise ValueError("Model outer year mismatch")
            if base is None:
                base = current[keys].copy()
                total = np.zeros(len(base))
            elif not base.equals(current[keys]):
                raise ValueError("Seed populations differ")
            total += current.p.to_numpy(float)
            receipts.append({"year": year, "seed": seed, "arm": arm,
                             "best_iteration": receipt["best_iteration"], "elapsed_s": receipt["elapsed_s"],
                             "roundtrip_max_abs_raw_difference": receipt["roundtrip_max_abs_raw_difference"],
                             "receipt_sha256": research.sha256(fit / "receipt.json")})
        base["p"] = total / len(protocol["seeds"])
        frames.append(base)
    return pd.concat(frames, ignore_index=True), receipts


def score_arm(frame: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    d = frame.sort_values(["race_date", "race_id", "horse_number", "horse_id"]).reset_index(drop=True)
    models.validate_races(d.sort_values(["race_id", "horse_number"]))
    group = d.race_id
    original = d.p.to_numpy(float)
    if not np.isfinite(original).all() or np.any((original < 0) | (original > 1)):
        raise ValueError("Invalid ensemble probabilities")
    p = np.clip(original, 1e-15, 1-1e-15)
    d["p"] = p
    pn = p / d.groupby("race_id").p.transform("sum").to_numpy()
    nll = pd.Series(-np.log(pn[d.won.to_numpy(bool)]), index=d.loc[d.won.astype(bool), "race_id"])
    decisions = d.copy()
    decisions["p_raw"] = np.clip(p, 1e-15, 1-1e-15)
    # Unused by the raw policy; provide a valid complete-field vector to its shared validator.
    q = 1/d.odds
    decisions["p_mix"] = q/q.groupby(group).transform("sum")
    bets = policies.allocate(decisions, "raw_ev_gt_1_2")
    bets["race_date"] = d.race_date.to_numpy()
    bets["won"] = d.won.to_numpy()
    bets["payout_yen"] = bets.units.to_numpy()*d.won.to_numpy(float)*d.odds.to_numpy(float)*100
    race = bets.groupby(["race_date", "race_id"], sort=True).agg(stake_yen=("stake_yen", "sum"), payout_yen=("payout_yen", "sum")).reset_index()
    race["profit_yen"] = race.payout_yen-race.stake_yen
    race["budget_yen"] = 1000
    race["winner_nll"] = race.race_id.map(nll)
    summary = {"quality": models.metrics(d.sort_values(["race_id", "horse_number"]),
                   d.sort_values(["race_id", "horse_number"]).p.to_numpy()),
               "raw_ev_gt_1_2": policies._point(race, bets[bets.stake_yen > 0]), "ev_band_calibration": {}}
    for name, lo, hi in (("le1", -np.inf, 1.), ("1to1_2", 1., 1.2), ("gt1_2", 1.2, np.inf)):
        mask = (p*d.odds.to_numpy() > lo) & (p*d.odds.to_numpy() <= hi)
        den = p[mask].sum()
        summary["ev_band_calibration"][name] = {"n": int(mask.sum()), "won": int(d.won.to_numpy()[mask].sum()),
            "sum_p": float(den), "won_over_p": float(d.won.to_numpy()[mask].sum()/den) if den else None}
    summary["by_year"] = {}
    for year, sub in d.groupby("year"):
        sub = sub.sort_values(["race_id", "horse_number"])
        selected_races = race[race.race_id.isin(sub.race_id)]
        selected_bets = bets[(bets.stake_yen > 0) & bets.race_id.isin(sub.race_id)]
        summary["by_year"][str(year)] = {"quality": models.metrics(sub, sub.p.to_numpy()),
                                            "policy": policies._point(selected_races, selected_bets)}
    return summary, race


def paired_comparison(tables: dict[str, pd.DataFrame], repetitions=4000, seed=20261019) -> dict:
    baseline = tables["baseline"]
    days = sorted(baseline.race_date.unique())
    rng = np.random.default_rng(seed)
    draws = rng.multinomial(len(days), np.full(len(days), 1/len(days)), size=repetitions)
    result = {}
    for arm in ("earlystop", "softmax"):
        candidate = tables[arm]
        if not baseline[["race_id", "race_date"]].equals(candidate[["race_id", "race_date"]]):
            raise ValueError("Model paired populations differ")
        daily = pd.DataFrame({"day": baseline.race_date, "nll": candidate.winner_nll-baseline.winner_nll,
                              "profit": candidate.profit_yen-baseline.profit_yen, "budget": baseline.budget_yen,
                              "races": 1}).groupby("day").sum().reindex(days)
        nll = (draws @ daily.nll)/(draws @ daily.races)
        profit = (draws @ daily.profit)/(draws @ daily.budget)
        result[arm] = {"winner_nll_difference": float(daily.nll.sum()/daily.races.sum()),
                       "winner_nll_ci95": policies._interval(nll, .05),
                       "winner_nll_bonferroni95_ci": policies._interval(nll, .025),
                       "profit_per_budget_difference": float(daily.profit.sum()/daily.budget.sum()),
                       "profit_per_budget_ci95": policies._interval(profit, .05),
                       "profit_per_budget_bonferroni95_ci": policies._interval(profit, .025)}
    return {"vs": "baseline", "family_size_per_metric": 2, "repetitions": repetitions,
            "seed": seed, "unit": "common eligible race date", "results": result}


def main() -> None:
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--out", type=Path, default=research.DEFAULT_OUT)
    args = ap.parse_args()
    protocol = research.verify(args.out)
    dest = args.out / "model_comparison"
    dest.mkdir(exist_ok=False)
    summaries, tables, receipts = {}, {}, []
    for arm in ARMS:
        frame, details = load_ensemble(args.out, arm, protocol)
        summaries[arm], tables[arm] = score_arm(frame)
        frame.to_parquet(dest / f"{arm}_ensemble.parquet", index=False)
        tables[arm].to_parquet(dest / f"{arm}_races.parquet", index=False)
        receipts += details
    result = {"arms": summaries, "paired": paired_comparison(tables), "fits": receipts,
              "seeds": protocol["seeds"], "ensemble_rule": "mean probabilities BEFORE normalization and scoring",
              "final_prediction_fit_worker_seconds": sum(r["elapsed_s"] for r in receipts),
              "max_roundtrip_raw_difference": max(r["roundtrip_max_abs_raw_difference"] for r in receipts),
              "settlement": "closing_odds_proxy", "can_adopt": False, "eligible_for_verdict": False,
              "untouched_holdout": False,
              "limitations": protocol["limitations"] + ["Same-budget policy is raw EV>1.2 for all model arms.",
                  "Within-day DD order follows race_id, not verified start times.",
                  "Re-fitted baseline uses train-only category maps; distinct from legacy ens15."]}
    costs = {"screen": 0., "earlystop_selectors": 0., "final_prediction_fits": result["final_prediction_fit_worker_seconds"]}
    for path in (args.out / "screen_seed1").glob("*/receipt.json"):
        costs["screen"] += json.loads(path.read_text())["elapsed_s"]
    for path in (args.out / "models").glob("seed_*/year_*/earlystop_selector/receipt.json"):
        costs["earlystop_selectors"] += json.loads(path.read_text())["elapsed_s"]
    costs["all_fit_worker_seconds"] = sum(costs.values())
    marker = args.out / "models_complete.json"
    if marker.exists():
        costs["outer_execution_wall_seconds"] = json.loads(marker.read_text())["elapsed_s"]
    result["costs"] = costs
    research.verify(args.out)
    research.write_new(dest / "summary.json", result)
    print(json.dumps({"status": "complete", "path": str(dest / "summary.json"),
                      "paired": result["paired"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
