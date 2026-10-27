"""Re-settle every frozen new-pattern candidate without changing or ranking purchases.

This reads saved primary/near-miss buys only after main scoring has completed.
Outputs are confined to roi_new_patterns_20260930/settlement. Zero purchases have
undefined ROI and CI, represented by null rather than a claimed 100% return.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strict2007_settlement as common

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "artifacts/roi_new_patterns_20260930"
OUT = STUDY / "settlement"
ROWS = ROOT / "artifacts/roi_strict2007_20260930/input/rows.parquet"
DIVIDENDS = ROOT / "artifacts/roi_recheck_20260930/data_audit/archived_win_dividends.jsonl"
FROM, THROUGH = "2026-08-15", "2026-09-22"


def settle_one(bets: pd.DataFrame, lookup: pd.DataFrame,
               eligible_races: set[str], days: list[str]) -> tuple[dict, pd.DataFrame]:
    """Compare identical saved purchases; incomplete archive coverage is disclosed."""
    required = {"race_id", "horse_id", "horse_number", "race_date", "odds", "won", "ret"}
    if not required.issubset(bets.columns):
        raise ValueError(f"Missing saved-bet columns: {sorted(required.difference(bets.columns))}")
    assert not bets[["race_id", "horse_id"]].duplicated().any()
    assert np.isfinite(bets[["odds", "ret"]].to_numpy()).all()
    assert np.allclose(bets.ret + 100., bets.won.astype(float) * bets.odds * 100., atol=1e-6)
    target = bets[bets.race_date.between(FROM, THROUGH)].copy()
    selected = target[target.race_id.isin(eligible_races)].copy()
    selected = selected.merge(lookup, on=["race_id", "horse_id", "horse_number"],
                              how="left", validate="one_to_one")
    assert selected.actual_payout.notna().all()
    assert np.allclose(selected.ret + 100., selected.saved_payout, atol=1e-6)
    result = common.metrics(selected, days)
    result["coverage"] = {
        "all_period_saved_bets": len(bets),
        "all_period_saved_betting_races": int(bets.race_id.nunique()),
        "target_period_saved_bets": len(target),
        "target_period_saved_betting_races": int(target.race_id.nunique()),
        "audited_bets": len(selected),
        "uncovered_target_period_bets": len(target) - len(selected),
        "audited_fraction_of_target_period_bets": len(selected) / len(target) if len(target) else None,
        "settlement_known_fraction_of_audited_bets": 1. if len(selected) else None,
        "eligible_archive_races": len(eligible_races),
    }
    result["zero_target_period_bets"] = target.empty
    result["zero_audited_bets"] = selected.empty
    return result, selected


def main() -> None:
    selection_path = STUDY / "selection_before_later_periods.json"
    summary_path = STUDY / "summary.json"
    if not selection_path.exists() or not summary_path.exists():
        raise SystemExit("Main experiment has not finished; no settlement output written.")
    selection = json.loads(selection_path.read_text())
    primary, near_miss = selection["primary"], selection["near_miss"]
    assert len(set(primary + near_miss)) == len(primary) + len(near_miss)
    candidates = [(pid, "primary") for pid in primary] + [(pid, "near_miss") for pid in near_miss]
    for pid, _ in candidates:
        assert isinstance(pid, str) and Path(pid).name == pid and pid not in (".", "..")
    bet_paths = [STUDY / "bets" / f"{pid}.parquet" for pid, _ in candidates]
    inputs = [selection_path, summary_path, STUDY / "freeze.json", STUDY / "patterns.json",
              *bet_paths, ROWS, DIVIDENDS, Path(__file__), Path(common.__file__)]
    hashes = {str(path.relative_to(ROOT)): common.sha(path) for path in inputs}
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "freeze.json").exists():
        raise SystemExit("Settlement already frozen; refusing to overwrite.")
    common.save(OUT / "freeze.json", {
        "created_at": datetime.now(timezone.utc).isoformat(), "input_sha256": hashes,
        "primary": primary, "near_miss": near_miss,
        "selection_changed": False,
        "policy": "Score all frozen primary and near-miss IDs without additional selection or ranking.",
        "date_from": FROM, "date_to": THROUGH,
        "stake_yen_per_bet": 100,
        "bootstrap": {"repetitions": common.REPETITIONS, "seed": common.SEED,
                      "unit": "all eligible race days including days without purchases"},
        "untouched_holdout": False, "can_adopt": False,
    })
    rows = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "horse_number", "race_date",
                                        "odds", "won", "dead_heat", "race_ok"])
    div = pd.read_json(DIVIDENDS, lines=True, dtype={"race_id": str})
    good = rows[rows.race_ok & ~rows.dead_heat & rows.race_date.between(FROM, THROUGH)]
    eligible_races = set(good.race_id) & set(div.race_id)
    eligible = good[good.race_id.isin(eligible_races)].copy()
    winners = div[div.race_id.isin(eligible_races)]
    assert len(eligible_races) == len(winners) == winners.race_id.nunique() == 394
    assert winners.db_winner_matches.all()
    eligible = eligible.merge(winners[["race_id", "horse_number", "payout_yen_per_100"]],
                              on=["race_id", "horse_number"], how="left", validate="one_to_one")
    eligible["actual_payout"] = eligible.payout_yen_per_100.fillna(0.)
    eligible["saved_payout"] = eligible.won.astype(float) * eligible.odds * 100.
    assert ((eligible.actual_payout > 0) == eligible.won).all()
    days = sorted(eligible.race_date.unique().tolist())
    assert len(days) == 14 and len(eligible) == 5240
    lookup = eligible[["race_id", "horse_id", "horse_number", "actual_payout", "saved_payout"]]
    records = []
    evidence_dir = OUT / "bets"
    evidence_dir.mkdir()
    for (pid, role), path in zip(candidates, bet_paths, strict=True):
        original = pd.read_parquet(path)
        stats, evidence = settle_one(original, lookup, eligible_races, days)
        record = {"id": pid, "role": role, **stats,
                  "saved_selection_sha256": hashes[str(path.relative_to(ROOT))]}
        records.append(record)
        evidence.to_parquet(evidence_dir / f"{pid}.parquet", index=False)
    assert hashes == {name: common.sha(ROOT / name) for name in hashes}
    result = {
        "kind": "new_patterns_fixed_purchase_settlement_sensitivity",
        "n_primary": len(primary), "n_near_miss": len(near_miss), "n_scored": len(records),
        "selected_ids": [pid for pid, _ in candidates], "selection_changed": False,
        "date_from": FROM, "date_to": THROUGH,
        "eligible_races": len(eligible_races), "eligible_horse_rows": len(eligible), "eligible_days": len(days),
        "research_only": True, "untouched_holdout": False, "can_adopt": False,
        "zero_bet_definition": "ROI and confidence intervals are null when no archived-scope bets exist.",
        "records": records, "input_sha256": hashes,
        "limitations": [
            "Selected archive subset covering only 14 days, not an independent test period.",
            "Displayed netkeiba result-page payouts; not independently checked with JRA.",
            "Payout correction preserves original choices and cannot certify decision-time odds.",
            "Saved outcomes used for training and unarchived periods are not repaired.",
            "Confidence intervals condition on these fixed choices and do not adjust for prior searches.",
            "Started-only non-dead-heat scope excludes cancellations and refunds.",
        ],
    }
    common.save(OUT / "summary.json", result)
    csv_rows = []
    for record in records:
        ci = record["bootstrap"]["archived_ci95"]
        csv_rows.append({k: record[k] for k in ("id", "role", "n_bets", "n_hits", "stake_yen",
                         "saved_payout_yen", "archived_payout_yen", "payout_difference_yen", "saved_roi", "archived_roi")}
                        | record["coverage"]
                        | {"archived_ci95_lo": ci["lo"] if ci else None,
                           "archived_ci95_hi": ci["hi"] if ci else None})
    pd.DataFrame(csv_rows).to_csv(OUT / "scores.csv", index=False)
    print(json.dumps({"scored": len(records), "primary": len(primary), "near_miss": len(near_miss),
                      "zero_audited_bets": sum(r["zero_audited_bets"] for r in records),
                      "output": str(OUT / "summary.json")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
