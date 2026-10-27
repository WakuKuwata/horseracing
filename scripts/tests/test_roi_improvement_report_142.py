"""Actual ensemble arithmetic and paired financial comparison contract."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_142/report.py"
SPEC = importlib.util.spec_from_file_location("roi_report142", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def frame():
    return pd.DataFrame({"race_id": ["202001010101"]*3+["202001010102"]*3,
        "horse_id": ["h1", "h2", "h3"]*2, "horse_number": [1, 2, 3]*2,
        "race_date": ["2020-01-01"]*3+["2020-01-02"]*3, "year": [2020]*6,
        "odds": [3., 4., 5.]*2, "won": [True, False, False, False, True, False],
        "field_size": [3]*6, "p": [.5, .3, .2, .4, .4, .2]})


def test_identity_paired_comparison_is_exactly_zero_with_skipped_days():
    _, races = M.score_arm(frame())
    actual = M.paired_comparison({arm: races.copy() for arm in M.ARMS}, repetitions=100)
    for result in actual["results"].values():
        assert result["winner_nll_difference"] == 0
        assert result["profit_per_budget_difference"] == 0
        assert result["profit_per_budget_bonferroni95_ci"] == [0., 0.]


def test_mean_probability_is_scored_not_mean_seed_loss(tmp_path):
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text("fixed")
    for seed, p in [(1, [.8, .1, .1]), (2, [.2, .4, .4])]:
        fit = tmp_path / "models" / f"seed_{seed:02d}" / "year_2020" / "baseline"
        fit.mkdir(parents=True)
        d = frame().iloc[:3].copy()
        d["p"] = p
        d.to_parquet(fit / "predictions.parquet", index=False)
        receipt = {"recipe": {"seed": seed, "identity": {"protocol_sha256": M.research.sha256(protocol_path)}},
                   "hashes": {"predictions.parquet": M.research.sha256(fit / "predictions.parquet")},
                   "best_iteration": 3, "elapsed_s": .1, "roundtrip_max_abs_raw_difference": 0.}
        (fit / "receipt.json").write_text(json.dumps(receipt))
    d, _ = M.load_ensemble(tmp_path, "baseline", {"window": {"model_outer": [2020]}, "seeds": [1, 2]})
    np.testing.assert_allclose(d.p, [.5, .25, .25])
    summary, _ = M.score_arm(d)
    assert summary["quality"]["winner_nll"] == pytest.approx(-np.log(.5))
    assert summary["quality"]["winner_nll"] != pytest.approx((-np.log(.8)-np.log(.2))/2)


def test_same_budget_profit_includes_cash_and_removes_top_payout():
    summary, race = M.score_arm(frame())
    policy = summary["raw_ev_gt_1_2"]
    assert policy["budget_yen"] == 2000
    assert policy["stake_yen"] <= 2000
    assert policy["profit_yen"] == race.profit_yen.sum()
    assert policy["profit_without_top1_payout_yen"] <= policy["profit_yen"]


def test_paired_comparison_rejects_missing_race():
    _, race = M.score_arm(frame())
    with pytest.raises(ValueError, match="populations differ"):
        M.paired_comparison({"baseline": race, "earlystop": race.iloc[:-1], "softmax": race}, repetitions=10)


def test_zero_probability_has_finite_identical_summary_and_paired_loss():
    d = frame().iloc[:3].copy()
    d["p"] = [0., .5, .5]
    summary, race = M.score_arm(d)
    assert np.isfinite(race.winner_nll).all()
    assert race.winner_nll.mean() == pytest.approx(summary["quality"]["winner_nll"])
