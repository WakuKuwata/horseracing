"""138 T005: the freeze script uses eval's single implementations (CI/p and selection)."""
import ast
import sys
from pathlib import Path

import numpy as np
import pytest
from horseracing_eval.bootstrap import centered_one_sided_p_from_replicates, race_block_ratio_bootstrap_ci_v1

_SCRIPT = Path(__file__).resolve().parents[1] / "roi_explore" / "freeze_rules_S1_S5_20261001.py"
sys.path.insert(0, str(_SCRIPT.parent))
import freeze_rules_S1_S5_20261001 as fz  # noqa: E402


def test_ci_and_p_groups_by_iso_day_ascending_and_matches_eval():
    days = np.array(["2026-03-08", "2026-01-04", "2026-03-08", "2026-02-01", "2026-01-04", "2026-02-01"])
    pay = np.array([0.0, 2500.0, 3000.0, 0.0, 0.0, 400.0])
    sel = np.array([True, True, True, True, False, True])
    got = fz.ci_and_p(pay, days, sel)
    # expected: per-day sums over selected rows, days sorted ascending
    d = ["2026-01-04", "2026-02-01", "2026-03-08"]
    num = np.array([2500.0, 400.0, 3000.0])
    den = np.array([100.0, 200.0, 200.0])
    ref = race_block_ratio_bootstrap_ci_v1(num, den, d, b=fz.BOOT["b"], seed=fz.BOOT["seed"])
    assert got["roi"] == pytest.approx(round(float(ref.point[0]), 6))
    assert got["ci95"] == [round(float(ref.ci_low[0]), 6), round(float(ref.ci_high[0]), 6)]
    assert got["p_one_sided"] == round(centered_one_sided_p_from_replicates(ref.replicates[0], float(ref.point[0])), 6)
    assert got["n_days"] == 3


def test_empty_selection():
    assert fz.ci_and_p(np.zeros(3), np.array(["2026-01-01"] * 3), np.zeros(3, bool))["roi"] is None


def test_script_selects_only_through_match_mask():
    body = _SCRIPT.read_text(encoding="utf-8")
    assert "ar.match_mask(" in body
    tree = ast.parse(body)
    # no comparison against the rule literals (thresholds / band / gap live in the registry only)
    banned = {1.1, 1.2, 1.3, 20, 40, 14, 112}
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for c in [node.left, *node.comparators]:
                assert not (isinstance(c, ast.Constant) and c.value in banned and c.value is not True), ast.dump(node)


def test_bootstrap_constants_are_the_plan_values():
    assert fz.BOOT["b"] == 20000 and fz.BOOT["seed"] == 20260905 and fz.BOOT["block"] == "race_day"
    assert fz.NOISE == {"sigmas": [0.1, 0.2, 0.3], "reps": 10, "seed0": 100}
    assert fz.GATE["tol_ece"] == 0.001
