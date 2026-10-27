"""Keep frozen purchase rows and compare saved settlement with archived win dividends.

This is a retrospective settlement sensitivity on a selected archive subset, not a
new strategy, a fresh holdout, or evidence of executable returns. No database writes.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/roi_recheck_20260930/data_audit"
REPLAY = OUT.parent / "model_replay"
SOURCES = {
    "regression_1986": "armC_regression",
    "binary_1986": "armC_binary",
    "binary_1986_serving": "armC_binary_drop-sameday+weightlive_serving_v1",
    "binary_2007_v2": "armC_binary_drop-sameday+weightlive_serving_v2_2007",
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def summarize(frame: pd.DataFrame) -> dict:
    n = len(frame)
    stakes = n * 100
    saved = float(frame.saved_payout.sum())
    actual = float(frame.actual_payout.sum())
    # Independent scalar sums catch joins, duplicated rows, and aggregate disagreement.
    assert math.isclose(saved, math.fsum(map(float, frame.saved_payout)), abs_tol=1e-6)
    assert math.isclose(actual, math.fsum(map(float, frame.actual_payout)), abs_tol=1e-6)
    assert not frame[["race_id", "horse_id"]].duplicated().any()
    assert ((frame.saved_payout > 0) == (frame.actual_payout > 0)).all()
    return {
        "n_bets": n,
        "n_hits": int((frame.actual_payout > 0).sum()),
        "n_betting_races": int(frame.race_id.nunique()),
        "n_betting_days": int(frame.race_date.nunique()),
        "stake_yen": stakes,
        "saved_payout_yen": saved,
        "archived_payout_yen": actual,
        "payout_difference_yen": actual - saved,
        "saved_roi": saved / stakes if stakes else None,
        "archived_roi": actual / stakes if stakes else None,
        "roi_difference": (actual - saved) / stakes if stakes else None,
        "payout_changed_bets": int((abs(frame.actual_payout - frame.saved_payout) > .01).sum()),
        "independent_aggregate_matches": True,
    }


def main() -> None:
    target = OUT / "settlement_replay.json"
    if target.exists():
        raise SystemExit("Completed settlement audit already exists; refusing to overwrite.")
    summary_path = REPLAY / "summary.json"
    policies = json.loads(summary_path.read_text())["primary"]
    assert len(policies) == 8
    bet_paths = [REPLAY / f"bets_{p['model']}_{p['policy_id']}.parquet" for p in policies]
    dividend_path = OUT / "archived_win_dividends.jsonl"
    rows_path = ROOT / "artifacts/roi_explore/rows.parquet"
    pred_paths = {
        k: ROOT / "artifacts/roi_explore/results" / v / "predictions.parquet"
        for k, v in SOURCES.items()
    }
    input_hashes = {str(p.relative_to(ROOT)): sha(p) for p in [
        summary_path, dividend_path, rows_path, *bet_paths, *pred_paths.values()
    ]}
    write_json(OUT / "settlement_replay_freeze.json", {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": input_hashes,
        "source_sha256": sha(Path(__file__)),
        "policies": [{k: p[k] for k in ("model", "policy_id", "stake_yen")} for p in policies],
        "controls": ["saved_favorite", "all_started_horses"],
        "selection": "Use already selected primary bet files unchanged; controls use saved is_fav/all rows.",
        "population": "Archived complete single-winner flat races with predictions in all four model sources.",
        "research_only": True,
        "untouched_holdout": False,
    })
    rows = pd.read_parquet(rows_path, columns=[
        "race_id", "horse_id", "horse_number", "race_date", "odds", "won",
        "dead_heat", "race_ok", "is_fav",
    ])
    div = pd.read_json(dividend_path, lines=True, dtype={"race_id": str})
    assert div.db_winner_matches.all()
    good = rows[rows.race_ok & ~rows.dead_heat]
    available = set(div.race_id) & set(good.race_id)
    coverage = {}
    for model, path in pred_paths.items():
        ids = pd.read_parquet(path, columns=["race_id"])
        available &= set(ids.race_id)
        coverage[model] = {"prediction_races": int(ids.race_id.nunique())}
    eligible = good[good.race_id.isin(available)].copy()
    # A complete payout table and one winner per included race make missing selections losses.
    winner_rows = div[div.race_id.isin(available)]
    assert winner_rows.race_id.nunique() == len(winner_rows) == len(available)
    eligible = eligible.merge(
        winner_rows[["race_id", "horse_number", "payout_yen_per_100"]],
        on=["race_id", "horse_number"], how="left", validate="one_to_one",
    )
    eligible["actual_payout"] = eligible.payout_yen_per_100.fillna(0.)
    eligible["saved_payout"] = eligible.won.astype(float) * eligible.odds * 100.
    assert ((eligible.actual_payout > 0) == eligible.won).all()
    dates = (eligible.race_date.min(), eligible.race_date.max())
    lookup = eligible[["race_id", "horse_id", "actual_payout", "saved_payout"]]
    results = []
    for policy, path in zip(policies, bet_paths, strict=True):
        original = pd.read_parquet(path)
        selected = original[original.race_id.isin(available)].copy()
        selected["saved_ret_payout"] = selected.ret + 100.
        selected = selected.merge(lookup, on=["race_id", "horse_id"], how="left", validate="one_to_one")
        assert selected.actual_payout.notna().all()
        assert np.allclose(selected.saved_ret_payout, selected.saved_payout, atol=1e-6)
        in_dates = original[original.race_date.between(*dates)]
        result = {
            "model": policy["model"], "policy_id": policy["policy_id"],
            **summarize(selected),
            "coverage": {
                "eligible_archive_races": len(available),
                "settlement_known_fraction_of_audited_bets": 1.,
                "original_bets_within_archive_date_range": len(in_dates),
                "audited_fraction_of_original_bets_in_date_range": len(selected) / len(in_dates) if len(in_dates) else None,
            },
            "selection_source_sha256": input_hashes[str(path.relative_to(ROOT))],
        }
        results.append(result)
        selected.to_parquet(OUT / f"settlement_{policy['model']}_{policy['policy_id']}.parquet", index=False)
    controls = []
    for name, selected in (("saved_favorite", eligible[eligible.is_fav]), ("all_started_horses", eligible)):
        controls.append({"policy_id": name, **summarize(selected), "eligible_archive_races": len(available)})
    assert input_hashes == {str(ROOT.joinpath(p).relative_to(ROOT)): sha(ROOT / p) for p in input_hashes}
    result = {
        "kind": "retrospective_fixed_purchase_settlement_sensitivity",
        "untouched_holdout": False,
        "selection_changed": False,
        "date_from": dates[0], "date_to": dates[1],
        "eligible_races": len(available), "eligible_horse_rows": len(eligible),
        "eligible_days": int(eligible.race_date.nunique()),
        "scope": "Selected local archive subset; settlement corrected only, predictions and choices unchanged.",
        "limitations": [
            "Not a representative sample or a prospective confirmation.",
            "Saved market inputs may be pre-race quotes or final odds; their timing is not certified.",
            "Archived result HTML is netkeiba displayed settlement, not independently JRA-certified.",
            "These corrections do not repair prior model training labels or unarchived races.",
            "Refunds/cancellations are outside the started-only subset.",
        ],
        "primary": results, "controls": controls, "input_sha256": input_hashes,
    }
    write_json(target, result)
    print(json.dumps({k: result[k] for k in ("eligible_races", "eligible_horse_rows", "eligible_days", "primary", "controls")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
