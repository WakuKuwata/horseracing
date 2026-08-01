"""009-engine and loader contract tests for the joint-calibration instrument.

The engine is injected from training because this workspace owns both ``horseracing-eval`` and
``horseracing-probability``.  Keeping these tests here prevents eval from acquiring the reverse
dependency that ``joint_fn`` was introduced to avoid.

The loader's top-three dead-heat decision has no pure helper: it is embedded in the Session-bound
``_build`` query/assembly path.  Its exclusion counter therefore needs a DB integration fixture;
the predictor-agnostic input rejection remains covered in eval's unit suite.
"""

from __future__ import annotations

import itertools
import math
from itertools import combinations
from numbers import Real

import pytest
from horseracing_eval.joint_calibration import (
    ARMS,
    BIN_EDGES,
    MARKET_LAMBDA2,
    MARKET_LAMBDA3,
    WIDE_MIN_FIELD,
    JointCalibRace,
    JointCalibrationError,
    bet_type_distributions,
    evaluate,
    realized_keys,
    stage_losses,
)
from horseracing_probability.engine import joint_probabilities

from horseracing_training.joint_calibration_run import _valid_win_odds

_Q_A = (0.40, 0.20, 0.13, 0.10, 0.07, 0.05, 0.03, 0.02)
_Q_B = (0.30, 0.22, 0.16, 0.11, 0.08, 0.06, 0.04, 0.03)


def _race(
    n: int = 8,
    *,
    q=None,
    top3=(1, 2, 3),
    race_id="r1",
    day="2026-01-01",
    grid=None,
):
    numbers = tuple(range(1, n + 1))
    if q is None:
        q = (1.0 / n,) * n
    return JointCalibRace(race_id, day, numbers, tuple(q), tuple(top3), grid)


def _bin_index(p: float) -> int:
    """Reference the frozen half-open partition, with 1.0 included in the last bin."""
    for idx, upper in enumerate(BIN_EDGES):
        if p < upper:
            return idx
    return len(BIN_EDGES) - 1


def _exacta_identity(q):
    """Spec equation for the undiscounted ordered two-place distribution."""
    numbers = tuple(range(1, len(q) + 1))
    return {
        (numbers[i], numbers[j]): q[i] * q[j] / (1.0 - q[i])
        for i in range(len(q))
        for j in range(len(q))
        if i != j
    }


def _bin_stats(distribution, positive_keys):
    rows = [dict(m=0, p=0.0, y=0) for _ in BIN_EDGES]
    for key, prediction in distribution.items():
        row = rows[_bin_index(prediction)]
        row["m"] += 1
        row["p"] += prediction
        row["y"] += int(key in positive_keys)
    return rows


def _top3_for_bin(q, bin_index: int, *, positive_in_bin: bool):
    distribution = _exacta_identity(q)
    pair = next(
        key
        for key, prediction in distribution.items()
        if (_bin_index(prediction) == bin_index) is positive_in_bin
    )
    third = next(number for number in range(1, len(q) + 1) if number not in pair)
    return pair + (third,)


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_dicts(child)


def _direct_numbers(mapping):
    return [
        value
        for value in mapping.values()
        if isinstance(value, Real) and not isinstance(value, bool)
    ]


def _same_number(actual, expected, *, tol=1e-12):
    if math.isnan(expected):
        return isinstance(actual, Real) and math.isnan(float(actual))
    return isinstance(actual, Real) and math.isclose(
        float(actual), float(expected), rel_tol=tol, abs_tol=tol
    )


def _tree_contains_number(value, expected, *, tol=1e-12):
    if isinstance(value, dict):
        return any(_tree_contains_number(child, expected, tol=tol) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_tree_contains_number(child, expected, tol=tol) for child in value)
    return _same_number(value, expected, tol=tol)


def _as_pair(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        if all(isinstance(item, Real) and not isinstance(item, bool) for item in value):
            return float(value[0]), float(value[1])
    if isinstance(value, dict):
        lowered = {str(key).lower(): item for key, item in value.items()}
        low = next((lowered[key] for key in ("low", "lower", "lo") if key in lowered), None)
        high = next((lowered[key] for key in ("high", "upper", "hi") if key in lowered), None)
        if isinstance(low, Real) and isinstance(high, Real):
            return float(low), float(high)
    return None


def _ci_pairs(value, *, context=""):
    pairs = []
    if isinstance(value, dict):
        lowered = {str(key).lower(): item for key, item in value.items()}
        for low_name, high_name in (
            ("ci_low", "ci_high"),
            ("ci_lower", "ci_upper"),
            ("bootstrap_low", "bootstrap_high"),
            ("bootstrap_lower", "bootstrap_upper"),
        ):
            low, high = lowered.get(low_name), lowered.get(high_name)
            if isinstance(low, Real) and isinstance(high, Real):
                pairs.append((float(low), float(high)))
        if "ci" in context or "interval" in context:
            pair = _as_pair(value)
            if pair is not None:
                pairs.append(pair)
        for key, child in value.items():
            key_text = str(key).lower()
            if "ci" in key_text or "interval" in key_text:
                pair = _as_pair(child)
                if pair is not None:
                    pairs.append(pair)
            pairs.extend(_ci_pairs(child, context=key_text))
    elif isinstance(value, (list, tuple)):
        if "ci" in context or "interval" in context:
            pair = _as_pair(value)
            if pair is not None:
                pairs.append(pair)
        for child in value:
            pairs.extend(_ci_pairs(child, context=context))
    return pairs


def _contains_ci(value, expected, *, tol=1e-10):
    expected = tuple(sorted(expected))
    return any(
        _same_number(min(pair), expected[0], tol=tol)
        and _same_number(max(pair), expected[1], tol=tol)
        for pair in _ci_pairs(value)
    )


def _complete_grid(numbers):
    pairs = tuple(combinations(numbers, 2))
    triples = tuple(combinations(numbers, 3))
    return {
        "quinella": {key: 10.0 for key in pairs},
        "wide": {key: 10.0 for key in pairs},
        "trio": {key: 10.0 for key in triples},
    }


def test_n7_wide_engine_and_jra_settlement_disagree():
    """For fields of five to seven the engine's top-three inclusion labels differ from JRA's
    top-two settlement, so the defect must be surfaced separately instead of averaged into wide."""
    race = _race(n=7, top3=(1, 2, 3))
    engine_keys = realized_keys("wide", race.top3)
    jra_settlement_keys = ((1, 2),)

    assert WIDE_MIN_FIELD == 8
    assert "wide" not in bet_type_distributions(
        race, arm="uniform", joint_fn=joint_probabilities
    )
    assert engine_keys == ((1, 2), (1, 3), (2, 3))
    assert engine_keys != jra_settlement_keys
    note = evaluate(
        [race], arms=("uniform",), b=20, seed=7, joint_fn=joint_probabilities
    )["field_size_mismatch_note"]
    assert note


@pytest.mark.parametrize(
    ("arm", "lambda2", "lambda3"),
    (("identity", 1.0, 1.0), ("market_current", 0.75, 0.70)),
)
def test_joint_nll_is_exact_sum_of_stage_losses(arm, lambda2, lambda3):
    """Exacta NLL must be L1+L2 and trifecta NLL L1+L2+L3 under the arm's actual lambdas;
    otherwise the joint score and the primary conditional-loss diagnostic contradict each other."""
    race = _race(q=_Q_A, top3=(2, 5, 1))
    l1, l2, l3 = stage_losses(race, lambda2=lambda2, lambda3=lambda3)
    distributions = bet_type_distributions(race, arm=arm, joint_fn=joint_probabilities)
    exacta_key = realized_keys("exacta", race.top3)[0]
    trifecta_key = realized_keys("trifecta", race.top3)[0]

    assert -math.log(distributions["exacta"][exacta_key]) == pytest.approx(
        l1 + l2, rel=1e-14, abs=1e-14
    )
    assert -math.log(distributions["trifecta"][trifecta_key]) == pytest.approx(
        l1 + l2 + l3, rel=1e-14, abs=1e-14
    )


def test_empty_and_zero_realization_bins_are_nan():
    """Empty bins and populated bins with no realized positive must report NaN, not a reassuring
    zero; zero-filling would manufacture evidence of calibration in sparse tails."""
    race = _race(q=_Q_A, top3=(1, 2, 3))
    reference = _bin_stats(_exacta_identity(_Q_A), {(1, 2)})
    out = evaluate(
        [race], arms=("identity",), b=30, seed=11, joint_fn=joint_probabilities
    )["reliability"]
    rows = list(_walk_dicts(out))

    empty_rows = [row for row in rows if _direct_numbers(row).count(0) and sum(
        isinstance(value, Real) and math.isnan(float(value)) for value in _direct_numbers(row)
    ) >= 2]
    assert empty_rows, "no emitted empty bin had NaN prediction and outcome summaries"

    zero_bin = next(row for row in reference if row["m"] > 0 and row["y"] == 0)
    expected_mean = zero_bin["p"] / zero_bin["m"]
    matching_rows = [
        row
        for row in rows
        if any(_same_number(value, zero_bin["m"]) for value in _direct_numbers(row))
        and any(_same_number(value, expected_mean) for value in _direct_numbers(row))
    ]
    assert matching_rows, "the populated zero-realization reference bin was not emitted"
    assert any(
        any(isinstance(value, Real) and math.isnan(float(value)) for value in _direct_numbers(row))
        for row in matching_rows
    ), "a populated zero-realization bin was filled with 0 instead of NaN"


def test_bootstrap_resamples_days_recomputes_denominators_and_pairs_arms():
    """Bootstrap replicates must resample whole days, recompute ratio denominators inside each
    draw, and reuse the draw across arms; otherwise intervals and arm contrasts are overconfident."""
    target_bin = 6
    race_a = _race(q=_Q_A, top3=_top3_for_bin(_Q_A, target_bin, positive_in_bin=True), race_id="a")
    race_b = _race(
        q=_Q_B,
        top3=_top3_for_bin(_Q_B, target_bin, positive_in_bin=False),
        race_id="b",
        day="2026-01-02",
    )

    # With one cluster, every valid bootstrap interval must collapse despite heterogeneous races.
    same_day_b = _race(
        q=_Q_B,
        top3=race_b.top3,
        race_id="same-day-b",
        day=race_a.day,
    )
    one_cluster = evaluate(
        [race_a, same_day_b],
        arms=("identity", "market_current"),
        b=200,
        seed=13,
        joint_fn=joint_probabilities,
    )
    finite_pairs = [pair for pair in _ci_pairs(one_cluster) if all(math.isfinite(x) for x in pair)]
    assert finite_pairs, "bootstrap intervals were not exposed"
    assert all(low == pytest.approx(high, abs=1e-14) for low, high in finite_pairs)

    stats_a = _bin_stats(_exacta_identity(_Q_A), {race_a.top3[:2]})[target_bin]
    stats_b = _bin_stats(_exacta_identity(_Q_B), {race_b.top3[:2]})[target_bin]
    assert stats_a["m"] != stats_b["m"]
    day_means = (stats_a["p"] / stats_a["m"], stats_b["p"] / stats_b["m"])

    out = evaluate(
        [race_a, race_b],
        arms=("identity", "market_current"),
        b=400,
        seed=17,
        joint_fn=joint_probabilities,
    )
    assert _contains_ci(out["reliability"], day_means), (
        "the reliability CI did not reach the two single-day ratios; this usually means the "
        "full-sample denominator was reused inside replicates"
    )

    identity_diffs = []
    for race in (race_a, race_b):
        _, i2, i3 = stage_losses(race, lambda2=1.0, lambda3=1.0)
        _, m2, m3 = stage_losses(race, lambda2=0.75, lambda3=0.70)
        identity_diffs.append((m2 + m3) - (i2 + i3))
    paired = tuple(identity_diffs)
    assert _contains_ci(out["stage_losses"], paired) or _contains_ci(
        out["stage_losses"], tuple(-value for value in paired)
    ), "the primary arm contrast was not bootstrapped with a shared day draw"


def test_micro_and_race_weighted_reliability_are_distinct():
    """A variable-cell fixture must expose distinct micro and race-normalized gaps; presenting
    one as the other lets large fields silently dominate the stated estimand."""
    target_bin = 6
    race_a = _race(q=_Q_A, top3=_top3_for_bin(_Q_A, target_bin, positive_in_bin=True), race_id="a")
    race_b = _race(
        q=_Q_B,
        top3=_top3_for_bin(_Q_B, target_bin, positive_in_bin=False),
        race_id="b",
        day="2026-01-02",
    )
    stats_a = _bin_stats(_exacta_identity(_Q_A), {race_a.top3[:2]})[target_bin]
    stats_b = _bin_stats(_exacta_identity(_Q_B), {race_b.top3[:2]})[target_bin]

    micro_gap = (
        stats_a["p"] + stats_b["p"] - stats_a["y"] - stats_b["y"]
    ) / (stats_a["m"] + stats_b["m"])
    race_gap = 0.5 * (
        (stats_a["p"] - stats_a["y"]) / stats_a["m"]
        + (stats_b["p"] - stats_b["y"]) / stats_b["m"]
    )
    assert micro_gap != pytest.approx(race_gap, abs=1e-12)

    reliability = evaluate(
        [race_a, race_b],
        arms=("identity",),
        b=30,
        seed=19,
        joint_fn=joint_probabilities,
    )["reliability"]
    assert (
        _tree_contains_number(reliability, micro_gap)
        and _tree_contains_number(reliability, race_gap)
    ) or (
        _tree_contains_number(reliability, -micro_gap)
        and _tree_contains_number(reliability, -race_gap)
    )


def test_result_mutation_cannot_change_predictions_bins_masks_or_inputs():
    """Changing only RESULT may change labels and scores, never q, predictions, bin assignments,
    or selection masks; violating this boundary leaks the answer into the supposed forecast."""
    numbers = tuple(range(1, 9))
    grid = _complete_grid(numbers)
    for quotes in grid.values():
        for key in quotes:
            quotes[key] = 0.1
    grid["quinella"][(1, 2)] = 1_000.0
    grid["wide"][(1, 2)] = 1_000.0
    grid["trio"][(1, 2, 3)] = 1_000.0

    before = _race(q=_Q_A, top3=(1, 2, 3), grid=grid)
    after = _race(q=_Q_A, top3=(4, 5, 6), grid=grid)
    assert before.q == after.q == _Q_A
    assert before.grid == after.grid == grid

    for arm in ARMS:
        before_dist = bet_type_distributions(
            before, arm=arm, joint_fn=joint_probabilities
        )
        after_dist = bet_type_distributions(after, arm=arm, joint_fn=joint_probabilities)
        assert before_dist == after_dist
        assert {
            bet_type: {key: _bin_index(p) for key, p in distribution.items()}
            for bet_type, distribution in before_dist.items()
        } == {
            bet_type: {key: _bin_index(p) for key, p in distribution.items()}
            for bet_type, distribution in after_dist.items()
        }

        before_masks = {
            (bet_type, threshold): {
                key for key, probability in before_dist[bet_type].items()
                if key in quotes and probability * quotes[key] >= threshold
            }
            for bet_type, quotes in grid.items()
            if bet_type in before_dist
            for threshold in (1.0, 1.5)
        }
        after_masks = {
            (bet_type, threshold): {
                key for key, probability in after_dist[bet_type].items()
                if key in quotes and probability * quotes[key] >= threshold
            }
            for bet_type, quotes in grid.items()
            if bet_type in after_dist
            for threshold in (1.0, 1.5)
        }
        assert before_masks == after_masks

    assert realized_keys("trifecta", before.top3) != realized_keys("trifecta", after.top3)
    assert stage_losses(before, lambda2=0.75, lambda3=0.70) != stage_losses(
        after, lambda2=0.75, lambda3=0.70
    )
    before_out = evaluate([before], b=20, seed=29, joint_fn=joint_probabilities)
    after_out = evaluate([after], b=20, seed=29, joint_fn=joint_probabilities)
    assert before_out["stage_losses"] != after_out["stage_losses"]
    assert before_out["bet_type_nll"] != after_out["bet_type_nll"]
    assert before_out["selected_subset"] != after_out["selected_subset"]


def test_market_lambda_arm_is_exact_and_rejects_049_and_084_values():
    """The market arm is exactly 0.75/0.70 and must use those values internally; substituting 049
    or 084's fitted lambdas would silently turn the pre-registered arm into another instrument."""
    lambda_049 = (0.852, 0.707)
    lambda_084 = (0.8303689257547258, 0.7111058148742723)
    assert (MARKET_LAMBDA2, MARKET_LAMBDA3) == (0.75, 0.70)
    assert ARMS == ("identity", "market_current", "indep_normalized", "uniform")
    assert (MARKET_LAMBDA2, MARKET_LAMBDA3) not in (lambda_049, lambda_084)

    race = _race(q=_Q_A, top3=(2, 5, 1))
    key = race.top3
    q = dict(zip(race.numbers, race.q, strict=True))

    def explicit(lambda2, lambda3):
        first, second, third = key
        p1 = q[first]
        p2 = q[second] ** lambda2 / sum(
            probability**lambda2 for number, probability in q.items() if number != first
        )
        p3 = q[third] ** lambda3 / sum(
            probability**lambda3
            for number, probability in q.items()
            if number not in (first, second)
        )
        return p1 * p2 * p3

    market_probability = bet_type_distributions(
        race, arm="market_current", joint_fn=joint_probabilities
    )["trifecta"][key]
    assert market_probability == pytest.approx(explicit(0.75, 0.70), rel=1e-14, abs=1e-15)
    assert market_probability != pytest.approx(explicit(*lambda_049), rel=1e-10, abs=1e-12)
    assert market_probability != pytest.approx(explicit(*lambda_084), rel=1e-10, abs=1e-12)


@pytest.mark.parametrize("bad_odds", (None, 0.0, -1.0, math.nan, math.inf, 999.9))
def test_loader_rejects_race_when_any_win_odds_are_invalid(bad_odds):
    """One invalid started-horse quote makes the whole field invalid; no partial q is allowed."""
    started_field_odds = [2.0, 3.5, bad_odds, 12.0]

    assert not all(_valid_win_odds(odds) for odds in started_field_odds)
    assert _valid_win_odds(1.0)
    assert _valid_win_odds(math.nextafter(999.9, 0.0))


# --- exotic price scale (regression) ------------------------------------------------------------

def test_999_9_is_a_legitimate_exotic_price_not_a_sentinel():
    """999.9 is the WIN-odds sentinel. Exotic prices run to 99,999.9 in the real grids, so a
    combination quoted at exactly 999.9 is an ordinary long shot — 27 exist in the captured data.
    Applying the win-side rule here aborted a whole pre-registered run on one trio price."""
    numbers = tuple(range(1, 9))
    grid = {
        "quinella": {k: 999.9 for k in itertools.combinations(numbers, 2)},
        "wide": {k: 999.9 for k in itertools.combinations(numbers, 2)},
        "trio": {k: 999.9 for k in itertools.combinations(numbers, 3)},
    }
    race = JointCalibRace(
        race_id="r", day="2026-01-01", numbers=numbers,
        q=tuple([1.0 / 8] * 8), top3=(1, 2, 3), grid=grid,
    )
    payload = evaluate([race], arms=("uniform",), b=10, seed=1,
                       joint_fn=joint_probabilities)
    # the grid survived: it reached the selected-subset endpoint instead of aborting the run
    assert payload["provenance"]["real_grid"]
    assert payload["selected_subset"]

    bad = dict(grid, trio={k: 0.0 for k in grid["trio"]})
    with pytest.raises(JointCalibrationError, match="finite and positive"):
        evaluate(
            [JointCalibRace("r2", "2026-01-01", numbers, tuple([1.0 / 8] * 8), (1, 2, 3), bad)],
            arms=("uniform",), b=10, seed=1, joint_fn=joint_probabilities,
        )


# --- prereg §10: the pool's own devig as a third predictor ----------------------------------------

def test_pool_devig_mass_matches_the_engine_contract():
    """Categorical pools devig to mass one; WIDE devigs to three. Normalising wide to one would
    make the pool look three times overconfident against every predictor it is compared with."""
    from horseracing_eval.joint_calibration import pool_devig_distributions

    numbers = tuple(range(1, 9))
    grid = {
        "quinella": {k: 50.0 for k in itertools.combinations(numbers, 2)},
        "wide": {k: 20.0 for k in itertools.combinations(numbers, 2)},
        "trio": {k: 300.0 for k in itertools.combinations(numbers, 3)},
    }
    got = pool_devig_distributions(
        JointCalibRace("r", "2026-01-01", numbers, tuple([1.0 / 8] * 8), (1, 2, 3), grid)
    )
    assert sum(got["quinella"].values()) == pytest.approx(1.0, abs=1e-12)
    assert sum(got["trio"].values()) == pytest.approx(1.0, abs=1e-12)
    assert sum(got["wide"].values()) == pytest.approx(3.0, abs=1e-12)


def test_us2_comparison_scores_every_predictor_on_one_common_mask():
    """A grid can omit the realized combination after a scratch. If each predictor were scored on
    the races IT happens to cover, a coverage difference would read as a skill difference."""
    numbers = tuple(range(1, 9))
    full = {
        "quinella": {k: 50.0 for k in itertools.combinations(numbers, 2)},
        "wide": {k: 20.0 for k in itertools.combinations(numbers, 2)},
        "trio": {k: 300.0 for k in itertools.combinations(numbers, 3)},
    }
    thin = {bt: dict(q) for bt, q in full.items()}
    del thin["quinella"][(1, 2)]          # the realized quinella is unpriced in this race

    races = [
        JointCalibRace("a", "2026-01-01", numbers, tuple([1.0 / 8] * 8), (1, 2, 3), full),
        JointCalibRace("b", "2026-01-02", numbers, tuple([1.0 / 8] * 8), (1, 2, 3), thin),
    ]
    block = evaluate(races, arms=ARMS, b=20, seed=3,
                     joint_fn=joint_probabilities)["us2_predictor_comparison"]

    assert block["available"] is True
    assert block["coverage"]["quinella"]["n_scored"] == 1
    assert block["coverage"]["quinella"]["n_dropped_realized_not_priced"] == 1
    assert block["coverage"]["trio"]["n_scored"] == 2
    scored = {(c["predictor"], c["bet_type"]): c["n_races"] for c in block["cells"]}
    assert {n for (_, bt), n in scored.items() if bt == "quinella"} == {1}
    assert {n for (_, bt), n in scored.items() if bt == "trio"} == {2}
    assert "pool_devig" in {p for p, _ in scored}
    # wide has three winners, so it must never acquire a categorical NLL
    assert not [c for c in block["cells"] if c["bet_type"] == "wide"]
