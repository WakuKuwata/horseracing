from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

spec = spec_from_file_location("policies142", Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_142/policies.py")
m = module_from_spec(spec)
spec.loader.exec_module(m)


def frame():
    return pd.DataFrame({
        "race_id": ["a", "a", "b", "b", "c", "c"],
        "horse_id": ["2", "1", "3", "4", "5", "6"],
        "horse_number": [2, 1, 1, 2, 1, 2],
        "race_date": ["2025-01-01"] * 4 + ["2025-01-02"] * 2,
        "year": [2025] * 6, "odds": [3., 3., 3., 3., 3., 3.],
        "p_raw": [.5] * 6, "p_mix": [.5] * 6, "won": [1, 0, 0, 1, 1, 0]})


def test_equal_budget_remainder_and_future_race_independence():
    d = frame().iloc[:2].copy()
    many = pd.concat([d.assign(horse_id=str(i), horse_number=i, p_mix=1 / 3).iloc[:1] for i in range(1, 4)], ignore_index=True)
    many["odds"] = 5.
    a = m.allocate(many, m.POLICIES[0])
    assert a.stake_yen.tolist() == [300, 300, 300]
    future = frame().loc[lambda x: x.race_id != "a"]
    b = m.allocate(pd.concat([many, future]), m.POLICIES[0])
    pd.testing.assert_frame_equal(a, b.iloc[:3].reset_index(drop=True))
    assert a.stake_yen.sum() == 900


def test_cap_ten_candidates_and_fixed_key_ties():
    d = pd.DataFrame({"race_id": ["a"] * 12, "horse_id": [str(i) for i in range(12)],
                      "horse_number": np.arange(12, 0, -1), "odds": [20.] * 12,
                      "p_raw": [.1] * 12, "p_mix": [1 / 12] * 12})
    a = m.allocate(d, m.POLICIES[0])
    assert set(a.loc[a.stake_yen > 0, "horse_number"]) == set(range(1, 11))
    assert a.stake_yen.sum() == 1000
    favorite = m.allocate(frame(), m.POLICIES[-1])
    assert favorite.loc[favorite.race_id == "a", "stake_yen"].tolist() == [0, 1000]


def test_labels_are_never_selection_inputs():
    d = frame()
    before = m.allocate(d, m.POLICIES[0])
    d["won"] = "do not read this"
    d["payout_yen_per_100"] = object()
    pd.testing.assert_frame_equal(before, m.allocate(d, m.POLICIES[0]))
    assert "won" not in before and "payout_yen_per_100" not in before


def test_strict_threshold_and_all_skipped():
    d = frame().assign(odds=2., p_raw=.6)
    assert m.allocate(d, m.POLICIES[0]).stake_yen.sum() == 0
    result = m.evaluate(d, bootstrap_repetitions=40)
    raw = result["summary"]["policies"][m.POLICIES[0]]
    assert raw["budget_yen"] == 3000 and raw["unspent_cash_yen"] == 3000
    assert raw["profit_yen"] == 0 and raw["deployed_roi"] is None
    assert raw["budget_return"] == 0 and raw["skip_rate"] == 1
    pair = result["summary"]["bootstrap"]["comparisons"][f"{m.POLICIES[0]}_minus_{m.POLICIES[1]}"]
    assert pair["deployed_roi"]["valid_replicates"] == 0
    assert pair["profit_per_budget"]["ci95"] == [0., 0.]


def test_same_day_bootstrap_identity_and_common_race_budget():
    r = m.evaluate(frame(), bootstrap_repetitions=50)
    for p in m.POLICIES:
        assert r["summary"]["policies"][p]["budget_yen"] == 3000
    pair = r["summary"]["bootstrap"]["comparisons"][f"{m.POLICIES[0]}_minus_{m.POLICIES[1]}"]
    for metric in pair.values():
        assert metric["difference"] == 0
        assert metric["ci95"] == [0., 0.]
        assert metric["bonferroni_familywise95_ci"] == [0., 0.]


def test_official_payout_subset_excludes_entire_missing_race():
    d = frame()
    d["payout_yen_per_100"] = [400., 0., np.nan, np.nan, 200., 0.]
    r = m.evaluate(d, bootstrap_repetitions=20)
    coverage = r["summary"]["official_payout_coverage"]
    assert coverage["eligible_races"] == 2 and coverage["excluded_missing_races"] == 1
    official = r["official_subset"]["summary"]
    assert official["n_races"] == 2
    assert official["policies"][m.POLICIES[0]]["payout_yen"] == 3000
    assert r["summary"]["policies"][m.POLICIES[0]]["payout_yen"] == 4500
    d.loc[1, "payout_yen_per_100"] = 400
    with pytest.raises(ValueError, match="winner identity"):
        m.evaluate(d, 20)


def test_race_drawdown_sees_losses_within_same_day_and_skips_do_not_reset_streak():
    d = frame()
    # Buy the favorite only: same-day loss -1000 then win +2000.
    d["won"] = [1, 0, 1, 0, 0, 1]
    r = m.evaluate(d, 20)
    point = r["summary"]["policies"][m.POLICIES[-1]]
    assert point["max_race_settlement_drawdown_yen"] == 1000
    assert point["max_consecutive_losing_races"] == 1
    races = pd.DataFrame({"race_date": ["2025-01-01"] * 3,
                          "stake_yen": [1000., 0., 1000.], "payout_yen": [0., 0., 0.],
                          "profit_yen": [-1000., 0., -1000.], "budget_yen": [1000.] * 3})
    bets = pd.DataFrame({"payout_yen": [0., 0.], "won": [0, 0]})
    point = m._point(races, bets)
    assert point["max_consecutive_losing_races"] == 2
    assert point["max_race_settlement_drawdown_yen"] == 2000


@pytest.mark.parametrize("change", ["duplicate", "nan_p", "bad_sum", "two_winners", "future", "mixed_date"])
def test_fail_closed_inputs(change):
    d = frame()
    if change == "duplicate":
        d = pd.concat([d, d.iloc[:1]])
    elif change == "nan_p":
        d.loc[0, "p_raw"] = np.nan
    elif change == "bad_sum":
        d.loc[0, "p_mix"] = .4
    elif change == "two_winners":
        d.loc[1, "won"] = 1
    elif change == "future":
        d.loc[0, "race_date"] = "2026-06-27"
        d.loc[0, "year"] = 2026
    else:
        d.loc[1, "race_date"] = "2025-01-02"
    with pytest.raises(ValueError):
        m.evaluate(d, 20)


def test_cli_is_append_only(tmp_path):
    src, out = tmp_path / "input.parquet", tmp_path / "result"
    frame().to_parquet(src)
    assert m.main(["--input", str(src), "--out", str(out), "--bootstrap-repetitions", "20"]) == 0
    assert (out / "summary.json").exists() and (out / "races.parquet").exists()
    with pytest.raises(SystemExit, match="overwrite"):
        m.main(["--input", str(src), "--out", str(out)])
