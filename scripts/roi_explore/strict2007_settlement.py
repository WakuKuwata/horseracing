"""Fixed-purchase settlement audit for the strict2007 market-model experiment.

Uses previously extracted netkeiba result HTML dividends. No new selection, fitting,
database mutation, fresh holdout claim, or independent JRA certification.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "artifacts/roi_strict2007_20260930"
OUT = WORK / "settlement"
REPLAY = WORK / "market_models"
DIVIDENDS = ROOT / "artifacts/roi_recheck_20260930/data_audit/archived_win_dividends.jsonl"
ROWS = WORK / "input/rows.parquet"
FROM, THROUGH = "2026-08-15", "2026-09-22"
MODELS = {"seed1_refit", "seed2_saved", "seed3_saved", "rounds600_saved", "avg3"}
REPETITIONS = 4000
SEED = 20260930


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save(path: Path, value: dict) -> None:
    with path.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def paired_bootstrap(frame: pd.DataFrame, days: list[str]) -> dict:
    """Identical day resamples for saved and corrected payouts; keep zero-bet days."""
    daily = frame.groupby("race_date").agg(
        n=("horse_id", "size"), saved=("saved_payout", "sum"), actual=("actual_payout", "sum")
    ).reindex(days, fill_value=0.)
    s = daily.n.to_numpy(dtype=float) * 100.
    p, a = daily.saved.to_numpy(dtype=float), daily.actual.to_numpy(dtype=float)
    rng = np.random.default_rng(SEED)
    ids = rng.integers(0, len(days), size=(REPETITIONS, len(days)))
    stakes = s[ids].sum(axis=1)
    has_stake = stakes > 0
    saved = p[ids].sum(axis=1)[has_stake] / stakes[has_stake]
    actual = a[ids].sum(axis=1)[has_stake] / stakes[has_stake]

    def interval(x):
        if not len(x):
            return None
        return {"lo": float(np.quantile(x, .025)), "hi": float(np.quantile(x, .975))}

    return {
        "method": "paired percentile ratio bootstrap over all eligible race dates",
        "unit": "race_day", "all_days_including_zero_bet_days": len(days),
        "repetitions": REPETITIONS, "seed": SEED,
        "replicates_with_stake": int(has_stake.sum()),
        "zero_stake_replicates_excluded": int((~has_stake).sum()),
        "saved_ci95": interval(saved), "archived_ci95": interval(actual),
        "roi_difference_ci95": interval(actual - saved),
        "selection_adjusted": False,
        "interpretation": "Conditional sensitivity on this small selected archive subset, not a prospective profit test.",
    }


def metrics(frame: pd.DataFrame, days: list[str]) -> dict:
    assert not frame[["race_id", "horse_id"]].duplicated().any()
    assert ((frame.saved_payout > 0) == (frame.actual_payout > 0)).all()
    saved, actual = float(frame.saved_payout.sum()), float(frame.actual_payout.sum())
    # Independent scalar aggregation verifies the arithmetic of the saved joined evidence.
    assert math.isclose(saved, math.fsum(map(float, frame.saved_payout)), abs_tol=1e-6)
    assert math.isclose(actual, math.fsum(map(float, frame.actual_payout)), abs_tol=1e-6)
    n, stakes = len(frame), len(frame) * 100
    largest = float(frame.actual_payout.max()) if n else 0.
    return {
        "n_bets": n, "n_hits": int((frame.actual_payout > 0).sum()),
        "n_betting_races": int(frame.race_id.nunique()), "n_betting_days": int(frame.race_date.nunique()),
        "stake_yen": stakes, "saved_payout_yen": saved, "archived_payout_yen": actual,
        "payout_difference_yen": actual - saved,
        "saved_roi": saved / stakes if stakes else None,
        "archived_roi": actual / stakes if stakes else None,
        "roi_difference": (actual - saved) / stakes if stakes else None,
        "archived_largest_hit_share": largest / actual if actual else None,
        "archived_roi_without_largest_hit": (actual - largest) / stakes if stakes else None,
        "payout_changed_bets": int((abs(frame.actual_payout - frame.saved_payout) > .01).sum()),
        "independent_aggregate_matches": True,
        "bootstrap": paired_bootstrap(frame, days),
    }


def main() -> None:
    summary_path = REPLAY / "summary.json"
    if not summary_path.exists():
        raise SystemExit("Market-model scoring has not completed; no outputs written.")
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "freeze.json").exists():
        raise SystemExit("Settlement run already frozen; refusing to overwrite.")
    summary = json.loads(summary_path.read_text())
    policies = summary["primary"]
    assert len(policies) == 10 and {p["model"] for p in policies} == MODELS
    for p in policies:
        assert p["expected_return_gt"] == 1.2 and p["odds_lt"] in (None, 21)
        assert p["stake_yen"] == 100 and p["selection"] == "all"
    bet_paths = [REPLAY / f"bets_{p['model']}_{p['policy_id']}.parquet" for p in policies]
    inputs = [summary_path, *bet_paths, DIVIDENDS, ROWS,
              WORK / "input/provenance.json", REPLAY / "refit_predictions.parquet",
              REPLAY / "refit_receipt.json", REPLAY / "freeze.json", Path(__file__)]
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in inputs}
    save(OUT / "freeze.json", {
        "created_at": datetime.now(timezone.utc).isoformat(), "input_sha256": hashes,
        "date_from": FROM, "date_to": THROUGH,
        "models": sorted(MODELS), "num_primary_policies": 10,
        "policies": [{k: p[k] for k in ("model", "policy_id", "stake_yen")} for p in policies],
        "controls": ["saved_favorite", "all_started_horses"],
        "selection": "Read existing bet files unchanged; no decisions use new payout data.",
        "bootstrap_repetitions": REPETITIONS, "seed": SEED,
        "research_only": True, "untouched_holdout": False,
    })
    rows = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "horse_number", "race_date",
                                        "odds", "won", "dead_heat", "race_ok", "is_fav"])
    div = pd.read_json(DIVIDENDS, lines=True, dtype={"race_id": str})
    pred_ids = set(pd.read_parquet(REPLAY / "refit_predictions.parquet", columns=["race_id"]).race_id)
    good = rows[rows.race_ok & ~rows.dead_heat & rows.race_date.between(FROM, THROUGH)]
    available = set(div.race_id) & set(good.race_id) & pred_ids
    eligible = good[good.race_id.isin(available)].copy()
    winners = div[div.race_id.isin(available)]
    assert len(available) == len(winners) == winners.race_id.nunique() == 394
    assert winners.db_winner_matches.all()
    eligible = eligible.merge(winners[["race_id", "horse_number", "payout_yen_per_100"]],
                              on=["race_id", "horse_number"], how="left", validate="one_to_one")
    eligible["actual_payout"] = eligible.payout_yen_per_100.fillna(0.)
    eligible["saved_payout"] = eligible.won.astype(float) * eligible.odds * 100.
    assert ((eligible.actual_payout > 0) == eligible.won).all()
    days = sorted(eligible.race_date.unique().tolist())
    assert len(days) == 14 and len(eligible) == 5240
    lookup = eligible[["race_id", "horse_id", "actual_payout", "saved_payout"]]
    results = []
    for policy, path in zip(policies, bet_paths, strict=True):
        original = pd.read_parquet(path)
        selected = original[original.race_id.isin(available)].copy()
        selected = selected.merge(lookup, on=["race_id", "horse_id"], how="left", validate="one_to_one")
        assert selected.actual_payout.notna().all()
        assert np.allclose(selected.ret + 100., selected.saved_payout, atol=1e-6)
        in_dates = original[original.race_date.between(FROM, THROUGH)]
        assert len(selected) == len(in_dates)
        result = {"model": policy["model"], "policy_id": policy["policy_id"], **metrics(selected, days),
                  "coverage": {"eligible_races": len(available), "original_bets_in_date_range": len(in_dates),
                               "audited_fraction_of_original_bets_in_date_range": 1.,
                               "settlement_known_fraction_of_audited_bets": 1.},
                  "selection_source_sha256": hashes[str(path.relative_to(ROOT))]}
        results.append(result)
        selected.to_parquet(OUT / f"bets_{policy['model']}_{policy['policy_id']}.parquet", index=False)
    controls = [
        {"policy_id": name, **metrics(selected, days)}
        for name, selected in (("saved_favorite", eligible[eligible.is_fav]), ("all_started_horses", eligible))
    ]
    assert hashes == {name: sha(ROOT / name) for name in hashes}
    result = {
        "kind": "strict2007_fixed_purchase_settlement_sensitivity",
        "selection_changed": False, "untouched_holdout": False, "can_adopt": False,
        "date_from": FROM, "date_to": THROUGH,
        "eligible_races": len(available), "eligible_horse_rows": len(eligible), "eligible_days": len(days),
        "primary": results, "controls": controls, "input_sha256": hashes,
        "limitations": [
            "Small archive subset selected by data collection, not an independent validation sample.",
            "Result HTML is the netkeiba displayed payout; not an independent JRA verification.",
            "Saved market inputs have no certified decision-time timestamp for this replay.",
            "Changing payouts does not repair model training targets or unarchived settlements.",
            "Started-only non-dead-heat scope excludes refunds and cancellations.",
            "4000 day-cluster percentile CIs are unadjusted for model/policy comparison and prior selection.",
        ],
    }
    save(OUT / "summary.json", result)
    pd.DataFrame([{k: v for k, v in r.items() if k not in ("coverage", "bootstrap")}
                  | {"ci95_lo": r["bootstrap"]["archived_ci95"]["lo"],
                     "ci95_hi": r["bootstrap"]["archived_ci95"]["hi"]}
                  for r in results]).to_csv(OUT / "scores.csv", index=False)
    print(json.dumps({"eligible_races": len(available), "eligible_days": len(days),
                      "results": [{k: r[k] for k in ("model", "policy_id", "n_bets", "n_hits", "saved_roi", "archived_roi")}
                                  | {"archived_ci95": r["bootstrap"]["archived_ci95"]} for r in results]},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
