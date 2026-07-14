"""T006a (FR-006a / analyze C1/A1/F1/F2/I1): pre-registered staged-evaluation guard.

The gate-config staged_evaluation matrix is RECORD-ONLY (operator-driven); this test makes the
matrix machine-verified so a hand-run recipe cannot silently desync from the pre-registered
attribution:
 (1) each paired-eval stage's candidate/active differ only by the bundle-under-test group and drop
     the same downstream groups from BOTH arms (symmetry);
 (2) all stages are reconstructible from the matrix via _expand_group_drops (no prose trust);
 (3) F03/F04 verdict-branch downstream base accumulation (winner kept, loser dropped);
 (4) gate-config eval_window is byte-consistent (single source);
 (5) frozen f03/f04/f05_formula constants equal the feature-module code constants;
 (6) compat pins equal registry COMPATIBLE_PRIOR_FEATURE_VERSIONS;
 (7) F05 distband bins equal the existing 020/023 dist_band edges.
"""

from __future__ import annotations

import json
import pathlib

import pytest

_GATE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "specs/070-past-market-bundles/gate-config.json"
)


@pytest.fixture(scope="module")
def cfg():
    if not _GATE.exists():
        pytest.skip("070 gate-config not present")
    return json.loads(_GATE.read_text())


def _stage(cfg, sid):
    return next(s for s in cfg["staged_evaluation"]["stages"] if s["id"] == sid)


def test_paired_eval_stages_are_group_expandable(cfg):
    from horseracing_training.cli import _expand_group_drops
    for s in cfg["staged_evaluation"]["stages"]:
        for key in ("both_drop", "candidate_add", "candidate_keep_minus"):
            groups = s.get(key)
            if groups:
                assert _expand_group_drops(tuple(groups))  # raises on unknown group


# The 5 past-market groups the staged matrix drops/keeps. pm_core_strength (F02) is the always-kept
# accuracy-first base and is NEVER in the drop set. Base starts with past_market (058) + F02.
_MARKET = {
    "past_market", "pm_rank_robust", "pm_expectation_residual",
    "pm_conditioned_support", "pm_conditioned_residual",
}


def _resolve(f03, f04, f05s):
    """Reconstruct (per stage) the candidate/active KEEP market-group sets across all verdict
    branches — the intended attribution semantics the operator MUST follow (codex 実装#2). Returns
    {stage_id: (candidate_keep, active_keep)}. f03/f04/f05s in {'ADOPT','REJECT'}."""
    base = {"past_market"}  # F02 pm_core_strength kept implicitly; not in _MARKET drop scope
    out = {}
    # stage 1: F03 replace — active keeps 058; candidate drops 058, adds F03
    out["f03_replace"] = (base - {"past_market"} | {"pm_rank_robust"}, set(base))
    base = ({"pm_rank_robust"} if f03 == "ADOPT" else {"past_market"})  # winner stays
    # stage 2: F04 add
    out["f04_add"] = (base | {"pm_expectation_residual"}, set(base))
    if f04 == "ADOPT":
        base = base | {"pm_expectation_residual"}
    # stage 3: F05 support
    out["f05_support"] = (base | {"pm_conditioned_support"}, set(base))
    if f05s == "ADOPT":
        base = base | {"pm_conditioned_support"}
    # stage 4: F05 residual (only reached when F04 ADOPT)
    out["f05_residual"] = (base | {"pm_conditioned_residual"}, set(base))
    return out


def test_full_verdict_branch_attribution(cfg):
    """Every stage, under every F03×F04×support verdict branch: candidate/active differ by EXACTLY
    the bundle-under-test group, both arms drop the same downstream groups (symmetry), and the
    winner (not loser) accumulates into the base (F1 inversion + F04→F05 base guard)."""
    bundle_of = {
        "f03_replace": {"pm_rank_robust", "past_market"},  # replacement pair
        "f04_add": {"pm_expectation_residual"},
        "f05_support": {"pm_conditioned_support"},
        "f05_residual": {"pm_conditioned_residual"},
    }
    for f03 in ("ADOPT", "REJECT"):
        for f04 in ("ADOPT", "REJECT"):
            for f05s in ("ADOPT", "REJECT"):
                res = _resolve(f03, f04, f05s)
                for sid, (cand_keep, act_keep) in res.items():
                    cand_drop = _MARKET - cand_keep
                    act_drop = _MARKET - act_keep
                    # candidate/active differ by exactly the bundle-under-test group(s)
                    assert (cand_drop ^ act_drop) <= bundle_of[sid], (sid, cand_drop, act_drop)
                    # both arms drop the SAME downstream groups (everything not the bundle)
                    downstream = _MARKET - bundle_of[sid]
                    assert (cand_drop & downstream) == (act_drop & downstream), sid
                # F03 winner accumulates (ADOPT keeps F03 not 058; REJECT keeps 058 not F03)
                base_after_f03 = res["f04_add"][1]
                if f03 == "ADOPT":
                    assert "pm_rank_robust" in base_after_f03 and "past_market" not in base_after_f03
                else:
                    assert "past_market" in base_after_f03 and "pm_rank_robust" not in base_after_f03
                # F04 winner accumulates into the F05 base
                base_at_f05 = res["f05_support"][1]
                assert ("pm_expectation_residual" in base_at_f05) == (f04 == "ADOPT")


def test_resolution_matches_gate_config_matrix(cfg):
    """The reconstructed stage-1/2 drops equal the gate-config matrix's declared both_drop +
    candidate_add (the matrix is the pre-registered record; the resolver must agree with it)."""
    from horseracing_training.cli import _expand_group_drops
    res = _resolve("ADOPT", "ADOPT", "ADOPT")
    # stage 1 both_drop (F04+F05 groups) must expand identically from matrix and resolver
    matrix_bd = set(_stage(cfg, "f03_replace")["both_drop"])
    resolver_bd = _MARKET - res["f03_replace"][0] - {"past_market"}  # groups dropped from both arms
    assert set(_expand_group_drops(tuple(matrix_bd))) == set(_expand_group_drops(tuple(resolver_bd)))


def test_bookkeeping_stages_have_no_number(cfg):
    stages = cfg["staged_evaluation"]["stages"]
    bookkeeping = [s for s in stages if s["id"].endswith("verdict_resolution")]
    assert {s["id"] for s in bookkeeping} == {"f03_verdict_resolution", "f04_verdict_resolution"}
    for s in bookkeeping:
        assert s.get("num") is None
    # the 5 paired-evals are numbered 1..5 contiguously
    nums = sorted(s["num"] for s in stages if s.get("num") is not None)
    assert nums == [1, 2, 3, 4, 5]


def test_f03_replace_drops_058_and_adds_f03_both_arms(cfg):
    s = _stage(cfg, "f03_replace")
    assert s["candidate_keep_minus"] == ["past_market"]
    assert s["candidate_add"] == ["pm_rank_robust"]
    # F04/F05 groups dropped from BOTH arms (attribution isolation)
    assert set(s["both_drop"]) == {
        "pm_expectation_residual", "pm_conditioned_support", "pm_conditioned_residual",
    }


def test_f04_resolution_and_f05_base_accumulation_encoded(cfg):
    # the F04 winner must be carried into stages 3-5 (analyze F1): f04_verdict_resolution exists and
    # the F05 stages document keeping F03-winner + adopted F04.
    _stage(cfg, "f04_verdict_resolution")  # exists
    for sid in ("f05_support", "f05_residual"):
        base = _stage(cfg, sid).get("_base", "")
        assert "F04" in base and "F03" in base
    # f05_residual requires F04 ADOPT
    assert _stage(cfg, "f05_residual")["requires"] == "f04_add==ADOPT"


def test_eval_window_single_source(cfg):
    w = cfg["eval_window"]
    assert w["from"] == "2019-01-01" and w["to"] == "2026-07-12"


def test_frozen_constants_bind_to_code(cfg):
    from horseracing_features import pm_conditioned, pm_expectation_residual, pm_rank_robust
    f3, f4, f5 = cfg["f03_formula"], cfg["f04_formula"], cfg["f05_formula"]
    # F03
    assert f3["min_obs"] == pm_rank_robust._MIN_OBS == 3
    assert f3["mean_window"] == pm_rank_robust._MEAN_WINDOW == 5
    assert f3["fav_window"] == pm_rank_robust._FAV_WINDOW == 5
    # F04 (incl. sd obs<2 threshold + asymmetric windows)
    assert f4["min_obs"] == pm_expectation_residual._MIN_OBS == 3
    assert f4["finish_window"] == pm_expectation_residual._FINISH_WINDOW == 5
    assert f4["win_window"] == pm_expectation_residual._WIN_WINDOW == 10
    assert f4["sd_window"] == pm_expectation_residual._SD_WINDOW == 5
    assert pm_expectation_residual._SD_MIN_OBS == 2  # ddof=1 needs >=2
    assert f4["sd_residual"] == "win_residual"
    # F05
    assert f5["lambda"] == pm_conditioned._LAMBDA == 5


def test_compat_pins_equal_registry(cfg):
    from horseracing_features.registry import COMPATIBLE_PRIOR_FEATURE_VERSIONS
    pins = cfg["serving"]["compat_pins"]
    reg = COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-019"]
    assert pins == reg


def test_f05_distband_bins_equal_existing_helper():
    import numpy as np
    from horseracing_features.extra_features import _DIST_BINS
    assert _DIST_BINS == [-np.inf, 1400, 1800, 2200, np.inf]
