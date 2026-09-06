"""Feature 109 T003/T011/T031/T032/T040: the pure scorer, state machine, injection, verdict."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _synth_buy import add_context, gate_config, make_arrays  # noqa: E402
from horseracing_eval import buy_pattern_gate as g  # noqa: E402
from horseracing_eval import buy_patterns as bp  # noqa: E402
from horseracing_eval.bootstrap import race_block_ratio_bootstrap_ci_v1  # noqa: E402
from horseracing_eval.decision import gate_config_hash  # noqa: E402
from horseracing_eval.hashing import stable_hash  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "src" / "horseracing_eval"


# ---- T003 import boundary ---------------------------------------------------------------------

@pytest.mark.parametrize("module", ["buy_pattern_gate.py", "buy_patterns.py"])
def test_import_boundary(module):
    tree = ast.parse((SRC / module).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert not ({"horseracing_betting", "horseracing_training", "pandas", "pyarrow"} & names)


# ---- fixtures ----------------------------------------------------------------------------------

def _prep(seed=0, **kw):
    rng = np.random.default_rng(seed)
    arr = add_context(make_arrays(rng, **kw))
    arr, rep = g.fix_population(arr)
    return g.derive(arr, warmup_through="2000-01-01"), rep


# ---- T011 population / aggregation / hashes -----------------------------------------------------

def test_fix_population_flow_counts_each_exclusion():
    rng = np.random.default_rng(5)
    arr = add_context(make_arrays(rng, n_races=60, jump_races=3, missing_odds_races=2,
                                  missing_p_races=2, not_in_bundle_races=4, dead_heat_races=2,
                                  no_winner_races=1))
    kept, rep = g.fix_population(arr)
    pre = rep["excluded_pre_result"]
    assert pre["not_in_bundle"] == 4 and pre["jump"] == 3
    assert pre["missing_odds"] == 2 and pre["missing_p"] == 2
    assert rep["excluded_post_result"] == {"no_winner": 1, "dead_heat": 2}
    assert rep["kept_races"] == 60 - 4 - 3 - 2 - 2 - 1 - 2
    assert kept["dead_heat"].sum() == rep["retained_dead_heat_rows"] > 0
    assert rep["population_hash"] == stable_hash(sorted(set(kept["race_id"][kept["primary"]].astype(str))))
    assert sum(rep["races_by_year"].values()) == rep["kept_races"]


def test_independent_aggregate_matches_and_detects_mismatch():
    r = ["a", "a", "b"]
    h = [1, 2, 1]
    pay = [0.0, 5.5, 0.0]
    second = g.independent_aggregate(r, h, pay, [1, 1, 1])
    assert second == {"n_bets": 3, "n_hits": 1, "sum_stake": 3.0, "sum_payout": 5.5}
    g.assert_aggregation_matches({"n_bets": 3, "n_hits": 1, "sum_stake": 3.0, "sum_payout": 5.5}, second)
    with pytest.raises(g.AggregationMismatch):
        g.assert_aggregation_matches({"n_bets": 3, "n_hits": 2, "sum_stake": 3.0, "sum_payout": 5.5}, second)
    with pytest.raises(g.AggregationMismatch):
        g.independent_aggregate(["a", "a"], [1, 1], [0, 0], [1, 1])


def test_selection_hash_is_order_invariant():
    a = g.selection_hash(["r2", "r1", "r1"], [3, 2, 1])
    b = g.selection_hash(["r1", "r1", "r2"], [1, 2, 3])
    assert a == b
    assert a != g.selection_hash(["r1", "r1"], [1, 2])


def test_four_frozen_hashes_fail_closed_individually(tmp_path):
    cfg = gate_config()
    p = tmp_path / "gate-config.json"
    p.write_text(json.dumps(cfg))
    good = gate_config_hash(cfg)
    assert g.load_gate_config(p, good)["feature"] == "109-buy-pattern-gate"
    with pytest.raises(g.GateConfigMismatch):
        g.load_gate_config(p, "0" * 64)
    unfrozen = dict(cfg, patterns_hash="")
    p2 = tmp_path / "g2.json"
    p2.write_text(json.dumps(unfrozen))
    with pytest.raises(g.GateConfigMismatch, match="not frozen"):
        g.load_gate_config(p2, gate_config_hash(unfrozen))
    for kind, payload in (("patterns", {"n": 1}), ("population", ["r1"]), ("survivors", {"ids": []})):
        assert g.verify_frozen(kind, payload, stable_hash(payload))
        with pytest.raises(g.FrozenArtifactMismatch, match=kind):
            g.verify_frozen(kind, payload, "f" * 64)


def test_strict_past_bands_do_not_see_same_day_or_later():
    arr, _ = _prep(11, n_races=200, n_days=40)
    arr2 = dict(arr)
    dates = arr2["race_date"].astype(str)
    last = max(dates)
    arr2["p"] = np.where(dates == last, arr2["p"] * 3.0, arr2["p"])
    d1 = g.derive(arr, warmup_through="2000-01-01")
    d2 = g.derive(arr2, warmup_through="2000-01-01")
    earlier = dates < last
    np.testing.assert_array_equal(d1["p_band_past"][earlier], d2["p_band_past"][earlier])
    np.testing.assert_array_equal(d1["entropy_band_past"][earlier], d2["entropy_band_past"][earlier])
    # warm-up → -1
    d3 = g.derive(arr, warmup_through="2999-12-31")
    assert (d3["p_band_past"] == -1).all()


# ---- T031 p-values / Holm / state machine ------------------------------------------------------

def test_null_centred_pvalues_direction_and_plus_one():
    reps = np.array([1.0, 1.25, 0.9, 1.5])
    point = 1.25
    pp, pf = g.one_sided_pvalues(reps, point, c=1.02)
    # profit: reps - point >= point - 1 (=0.25): {1.5} → (1+1)/5
    assert pp == pytest.approx(2 / 5)
    # futility: point - reps >= c - point (= -0.23): {1.0, 1.25, 0.9} → (1+3)/5
    assert pf == pytest.approx(4 / 5)
    pp2, pf2 = g.one_sided_pvalues(np.array([0.9, 0.95, 0.85, 0.92]), 0.90, c=1.02)
    # profit: reps - 0.90 >= 0.90 - 1.0 = -0.10 → all four → (1+4)/5
    assert pp2 == pytest.approx(5 / 5)
    # futility: 0.90 - reps >= 1.02 - 0.90 = 0.12 → none → 1/5
    assert pf2 == pytest.approx(1 / 5)


def test_holm_counts_demoted_as_p1_without_break():
    p = {"a": 0.001, "b": 1.0, "c": 0.004, "d": 0.5}
    out = g.holm_one_sided(p, alpha=0.025)
    # m=4: a: 0.001 <= 0.025/4 ✓ ; c: 0.004 <= 0.025/3 ✓ ; d: 0.5 > 0.0125 ✗ ; b ✗
    assert out == {"a": True, "c": True, "d": False, "b": False}
    # a demoted entry does not stop others from being rejected
    p2 = {"demoted": 1.0, "x": 0.001}
    assert g.holm_one_sided(p2, alpha=0.025) == {"demoted": False, "x": True}


def _score(pid, pp, pf, demoted=None):
    return g.PatternScore(pid, 100, 30, 50, 1.05, 1.0, 1.1, pp, pf, 0.02, 0.05, 0.1, 1.0,
                          demoted, 0, {}, "h", True, "race_day")


def test_state_priorities_and_reachability():
    v = {"race_day": [_score("a", 0.001, 0.001)], "iso_week": [_score("a", 0.002, 0.002)],
         "calendar_month": [_score("a", 0.003, 0.001)]}
    assert g.decide(v)["a"]["state"] == "ADOPT_CLOSE"  # both tests reject → ADOPT wins
    v = {k: [_score("a", 0.5, 0.001)] for k in ("race_day", "iso_week", "calendar_month")}
    assert g.decide(v)["a"]["state"] == "RULED_OUT"
    v = {k: [_score("a", 0.5, 0.5)] for k in ("race_day", "iso_week", "calendar_month")}
    assert g.decide(v)["a"]["state"] == "NOT_ADOPTED"
    v = {"race_day": [_score("a", 0.001, 0.5)], "iso_week": [_score("a", 0.5, 0.5)],
         "calendar_month": [_score("a", 0.001, 0.5)]}
    d = g.decide(v)["a"]
    assert d["state"] == "NO_DECISION" and d["reason"].startswith("sensitivity_split")
    v = {"race_day": [_score("a", 0.001, 0.001, demoted="n_hits 5 < 20")], "iso_week": [_score("a", 0.001, 0.001)],
         "calendar_month": [_score("a", 0.001, 0.001)]}
    assert g.decide(v)["a"]["state"] == "NO_DECISION"


def test_zero_denominator_replicate_demotes_and_keeps_m():
    cfg = gate_config()
    days = ["2010-01-01", "2010-01-02", "2010-01-03", "2010-01-04", "2010-01-05", "2010-01-06"]
    pay = np.array([[0, 0, 0, 0, 0, 9.0], [1, 2, 1, 2, 1, 2.0]])
    stk = np.array([[0, 0, 0, 0, 0, 1.0], [1, 1, 1, 1, 1, 1.0]])
    dm = g.DayMatrix(["sparse", "dense"], days, pay, stk, np.array([1, 6]), np.array([1, 6]),
                     np.array([9.0, 2.0]), np.array([1, 6]), ["h1", "h2"], [{}, {}], [True, True])
    cfg2 = dict(cfg, demotion={"min_hits": 1, "max_single_hit_share": 1.0, "min_days": 1})
    scores, _ = g.score_patterns(dm, cfg2, m=2)
    sparse = next(s for s in scores if s.pattern_id == "sparse")
    assert sparse.demoted_reason == "zero_denominator_replicate" and sparse.n_zero_den > 0
    v = {k: scores for k in ("race_day",)}
    dec = g.decide(v)
    assert dec["sparse"]["state"] == "NO_DECISION"
    # the dense pattern is still judged with m=2 (its p must clear alpha/2)
    assert dec["dense"]["state"] in ("ADOPT_CLOSE", "RULED_OUT", "NOT_ADOPTED")


def test_mde_formula():
    from scipy.stats import norm
    sd = 0.022
    assert g.mde_80(sd, alpha=0.025, m=5) == pytest.approx((norm.ppf(1 - 0.005) + norm.ppf(0.8)) * sd)
    assert np.isnan(g.mde_80(0.0, alpha=0.025, m=1))


def test_score_patterns_demotion_rules_and_loho():
    cfg = gate_config()
    arr, _ = _prep(21, n_races=150, n_days=30)
    days = g.day_universe(arr)
    fam = bp.enumerate_family()
    masks = [bp.mask_for(p, arr) for p in fam.controls]
    dm = g.apply_masks(arr, masks, [c.pattern_id for c in fam.controls], days)
    assert dm.n_bets[0] == 0 and all(dm.second_agg_match)
    scores, bs = g.score_patterns(dm, cfg, m=1)
    by = {s.pattern_id: s for s in scores}
    assert by["no_bet"].demoted_reason == "not_fired"
    cap = by["cap21_all"]
    assert cap.demoted_reason is None
    assert cap.leave_one_hit_out_roi < cap.roi
    assert 0 < cap.max_single_hit_share < 1
    assert len(bs.replicates[3]) == cfg["bootstrap"]["b"]


# ---- T032 synthetic injection -------------------------------------------------------------------

def test_injection_one_winner_per_race_and_marginal_rho():
    arr, _ = _prep(31, n_races=400, n_days=40)
    days = g.day_universe(arr)
    rs, order = g.race_structure(arr, days)
    mask = np.asarray(arr["odds"] < 21.0)[order]
    spec = g.TiltSpec(mask, g.tilt_weights(rs, mask, "klmin_roi_tilt", np.random.default_rng(0)), 1.10)
    fit = g.fit_tilts(rs, [spec], tau=0.0, target_idx=0)
    assert fit["feasible"]
    rng = np.random.default_rng(1)
    rois = []
    for _ in range(300):
        won = g.synth_outcomes(rs, [spec], 0, 0.0, rng)
        per_race = np.bincount(rs.inv, weights=won.astype(float), minlength=rs.nr)
        assert (per_race == 1).all()
        rois.append((mask * rs.d * won).sum() / mask.sum())
    assert abs(np.mean(rois) - 1.10) < 0.03
    # with a day effect the marginal is re-solved
    spec2 = g.TiltSpec(mask, spec.s, 1.10)
    fit2 = g.fit_tilts(rs, [spec2], tau=0.3, target_idx=0)
    assert fit2["feasible"]
    rois2 = [(mask * rs.d * g.synth_outcomes(rs, [spec2], 0, 0.3, rng)).sum() / mask.sum() for _ in range(300)]
    assert abs(np.mean(rois2) - 1.10) < 0.04


def test_edge_shapes_leave_unselected_probabilities_at_q_and_odds_neutral_can_be_infeasible():
    arr, _ = _prep(32, n_races=200, n_days=30)
    days = g.day_universe(arr)
    rs, order = g.race_structure(arr, days)
    mask = np.asarray(arr["odds"] < 11.0)[order]
    rng = np.random.default_rng(3)
    for shape in ("klmin_roi_tilt", "long_odds_top20pct", "days_10pct"):
        s = g.tilt_weights(rs, mask, shape, rng)
        assert (s[~mask] == 0).all()
        if shape == "long_odds_top20pct":
            assert (s > 0).sum() < mask.sum()
    # odds-neutral tilt with an absurd target is infeasible
    s = g.tilt_weights(rs, mask, "odds_neutral", rng)
    spec = g.TiltSpec(mask, s, 50.0)
    fit = g.fit_tilts(rs, [spec], tau=0.0, target_idx=0)
    assert not fit["feasible"]


def test_boundary_null_constrains_overlapping_pattern():
    arr, _ = _prep(33, n_races=300, n_days=40)
    days = g.day_universe(arr)
    rs, order = g.race_structure(arr, days)
    m_t = np.asarray(arr["odds"] < 6.0)[order]
    m_o = np.asarray(arr["odds"] < 11.0)[order]   # overlaps the target
    rng = np.random.default_rng(4)
    specs = [g.TiltSpec(m_t, g.tilt_weights(rs, m_t, "klmin_roi_tilt", rng), 1.00),
             g.TiltSpec(m_o, g.tilt_weights(rs, m_o, "klmin_roi_tilt", rng), None)]
    fit = g.fit_tilts(rs, specs, tau=0.0, target_idx=0)
    assert fit["feasible"]
    pi = g.race_probs(rs, specs[0].lam * specs[0].s + specs[1].lam * specs[1].s)
    assert abs(g.expected_roi(pi, m_t, rs.d) - 1.0) < 5e-3
    assert g.expected_roi(pi, m_o, rs.d) <= 1.0 + 5e-3


def test_selftest_smoke_runs_and_reports_size_and_power():
    cfg = gate_config()
    arr, _ = _prep(34, n_races=300, n_days=40)
    days = g.day_universe(arr)
    fam = bp.enumerate_family()
    ids = [p.pattern_id for p in fam.patterns[:40]]
    masks = [bp.mask_for(p, arr) for p in fam.patterns[:40]]
    rep = g.selftest(arr, masks, ids, days, cfg, tau=0.0, rng=np.random.default_rng(5),
                     reps_size=4, reps_power=2)
    assert len(rep["representative"]) >= 1
    assert rep["size"] and rep["power"]
    assert "size_passed" in rep
    for k, res in rep["size"].items():
        if not res.get("infeasible"):
            assert 0 <= res["lower_95"] <= res["rate"] <= res["upper_95"] <= 1


def test_estimate_day_effect_returns_nonnegative_tau():
    arr, _ = _prep(35, n_races=200, n_days=30)
    days = g.day_universe(arr)
    rs, order = g.race_structure(arr, days)
    mask = np.asarray(arr["odds"] < 21.0)[order]
    won = np.asarray(arr["won"], dtype=bool)[order]
    out = g.estimate_day_effect(rs, mask, won, np.random.default_rng(6), sims=2, it=3)
    assert out["tau"] >= 0.0 and out["observed_var"] >= 0.0


# ---- T040 verdict --------------------------------------------------------------------------------

def test_conclusion_forbids_phrases_and_verdict_validation():
    cfg = gate_config()
    text = g.conclusion_ja({"SCREENED_OUT": 393}, cfg, None, confirmatory_end="2026-09-05")
    for ph in g.FORBIDDEN_PHRASES:
        assert ph not in text
    assert "確認できなかった" in text and "SCREENED_OUT 393" in text
    v = g.build_verdict(cfg=cfg, gate_config_hash="a", patterns_hash="b", population_hash="c",
                        survivors_hash="d", bundle_digest="e", run_code_sha="f", run_tree_dirty=False,
                        windows=cfg["windows"], states={"SCREENED_OUT": 393}, per_survivor=[],
                        controls={"no_bet": {"accounting_sentinel": 1.0}}, evidence_refs=[],
                        selftest_report=None, screened_out_reasons={"discovery": 393})
    g.validate_verdict(v)
    assert v["limitations"] == list(g.LIMITATIONS) and len(v["limitations"]) == 5
    v["conclusion_ja"] = "全パターン REJECT"
    with pytest.raises(ValueError):
        g.validate_verdict(v)
    v2 = dict(v, conclusion_ja=text, per_survivor=[{"pattern_id": "x", "state": "NOT_ADOPTED"}])
    with pytest.raises(ValueError, match="per_survivor"):
        g.validate_verdict(v2)


def test_maxt_upper_bound_is_above_points():
    reps = np.random.default_rng(0).normal(1.0, 0.02, size=(3, 500))
    pts = np.array([1.0, 1.01, 0.99])
    u = g.maxt_upper(reps, pts, alpha=0.025)
    assert (u > pts).all()


def test_recompute_bit_identity_through_same_function():
    cfg = gate_config()
    arr, _ = _prep(41, n_races=150, n_days=30)
    days = g.day_universe(arr)
    fam = bp.enumerate_family()
    masks = [bp.mask_for(p, arr) for p in fam.controls]
    dm = g.apply_masks(arr, masks, [c.pattern_id for c in fam.controls], days)
    s1, bs1 = g.score_patterns(dm, cfg, m=1)
    bs2 = race_block_ratio_bootstrap_ci_v1(dm.payout, dm.stake, dm.days, block="race_day",
                                           b=cfg["bootstrap"]["b"], seed=cfg["bootstrap"]["seed"])
    s2, _ = g.score_patterns(dm, cfg, m=1, bs=bs2)
    dump = lambda xs: json.dumps([s.to_dict() for s in xs], sort_keys=True, default=str)  # noqa: E731
    assert dump(s1) == dump(s2)
