"""Zero-exposure semantics and fixed-choice coverage for the new-pattern settlement audit."""
import importlib.util
from pathlib import Path

import pandas as pd

PATH = Path(__file__).resolve().parents[1] / "roi_explore/new_patterns_settlement_20260930.py"
SPEC = importlib.util.spec_from_file_location("new_patterns_settlement", PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def bet(rid, date, won=False, odds=2.):
    return {"race_id": rid, "horse_id": f"h{rid}", "horse_number": 1,
            "race_date": date, "odds": odds, "won": won, "ret": odds * 100. - 100. if won else -100.}


def lookup():
    return pd.DataFrame([{"race_id": "r1", "horse_id": "hr1", "horse_number": 1,
                          "saved_payout": 200., "actual_payout": 250.}])


def test_no_target_period_purchases_have_no_roi_or_ci():
    bets = pd.DataFrame([bet("old", "2025-01-05")])
    stats, evidence = audit.settle_one(bets, lookup(), {"r1"}, ["2026-08-15", "2026-08-16"])
    assert evidence.empty and stats["n_bets"] == stats["n_hits"] == stats["stake_yen"] == 0
    assert stats["saved_roi"] is None and stats["archived_roi"] is None
    assert stats["bootstrap"]["saved_ci95"] is None
    assert stats["bootstrap"]["archived_ci95"] is None
    assert stats["bootstrap"]["roi_difference_ci95"] is None
    assert stats["bootstrap"]["zero_stake_replicates_excluded"] == 4000
    assert stats["coverage"]["all_period_saved_bets"] == 1
    assert stats["coverage"]["target_period_saved_bets"] == 0
    assert stats["coverage"]["audited_fraction_of_target_period_bets"] is None


def test_partly_uncovered_purchases_are_disclosed_without_reselection():
    bets = pd.DataFrame([bet("r1", "2026-08-15", won=True), bet("uncovered", "2026-08-16"),
                         bet("old", "2025-01-05")])
    original = bets.copy(deep=True)
    stats, evidence = audit.settle_one(bets, lookup(), {"r1"}, ["2026-08-15", "2026-08-16"])
    pd.testing.assert_frame_equal(bets, original)
    assert stats["n_bets"] == stats["n_hits"] == 1
    assert stats["saved_roi"] == 2. and stats["archived_roi"] == 2.5
    assert stats["payout_difference_yen"] == 50.
    assert stats["coverage"]["all_period_saved_bets"] == 3
    assert stats["coverage"]["target_period_saved_bets"] == 2
    assert stats["coverage"]["audited_fraction_of_target_period_bets"] == .5
    assert stats["coverage"]["uncovered_target_period_bets"] == 1
    assert evidence.race_id.tolist() == ["r1"]
