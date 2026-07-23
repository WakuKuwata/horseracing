"""Feature 079 (step 2): EV-based per-race training weight builder (pure).

Locks the pre-registered formula + the complete-field / cap / normalisation / race-constancy
invariants BEFORE any run. These are validity + leak guards, not accuracy claims.
"""

from __future__ import annotations

import numpy as np
import pytest

from horseracing_training.ev_weight import (
    CENTER,
    ODDS_CAP,
    TAU,
    assert_race_constant,
    build_race_weights,
)


def test_fixed_constants_locked():
    # pre-registration 079 sec 2.2 — must not drift without a recorded amendment
    assert CENTER == 1.0
    assert TAU == 0.10
    assert ODDS_CAP == 21.0


def _race(rid, evs, odds):
    """Helper: rows for one race with given per-horse (ev-driving p via p=ev/odds, odds)."""
    p = [e / o for e, o in zip(evs, odds, strict=True)]
    return [(rid, pi, oi) for pi, oi in zip(p, odds, strict=True)]


def _split(rows):
    rid = np.array([r[0] for r in rows])
    p = np.array([r[1] for r in rows], dtype=float)
    o = np.array([r[2] for r in rows], dtype=float)
    return rid, p, o


def test_output_is_race_constant():
    rows = _race("A", [1.2, 0.8, 0.5], [5.0, 3.0, 10.0]) + _race("B", [0.6, 0.4], [4.0, 8.0])
    rid, p, o = _split(rows)
    w = build_race_weights(rid, p, o)
    assert_race_constant(rid, w)  # does not raise
    assert w[rid == "A"].std() == 0.0
    assert w[rid == "B"].std() == 0.0


def test_higher_ev_race_gets_higher_weight():
    # race A best EV = 1.5 (bettable), race B best EV = 0.6 -> A upweighted vs B
    rows = _race("A", [1.5, 0.3], [6.0, 2.0]) + _race("B", [0.6, 0.5], [4.0, 3.0])
    rid, p, o = _split(rows)
    w = build_race_weights(rid, p, o)
    assert w[rid == "A"][0] > w[rid == "B"][0]


def test_informative_races_normalised_to_mean_one():
    rows = (
        _race("A", [1.8, 0.2], [6.0, 2.0])
        + _race("B", [1.0, 0.4], [5.0, 3.0])
        + _race("C", [0.5, 0.3], [4.0, 2.0])
    )
    rid, p, o = _split(rows)
    w = build_race_weights(rid, p, o)
    # per-race scalar mean over the 3 informative races == 1
    per_race = [w[rid == r][0] for r in ["A", "B", "C"]]
    assert abs(float(np.mean(per_race)) - 1.0) < 1e-12


def test_ev_one_gives_raw_1_5_before_norm():
    # single informative race with ev_r exactly 1.0 -> raw 1.5 -> normalised to itself = 1.0
    rid, p, o = _split(_race("A", [1.0, 0.2], [5.0, 2.0]))
    w = build_race_weights(rid, p, o)
    assert abs(w[0] - 1.0) < 1e-12  # only race -> its own mean


def test_complete_field_rule_missing_makes_race_neutral():
    # A (high EV) & D (low EV) informative; B has a row missing odds -> B neutral (exactly 1.0)
    # and does not participate in the informative-race normalisation.
    rid = np.array(["A", "A", "D", "D", "B", "B"])
    p = np.array([0.30, 0.10, 0.10, 0.10, 0.25, 0.10])
    o = np.array([6.0, 2.0, 4.0, 2.0, 5.0, np.nan])  # B second row missing odds
    w = build_race_weights(rid, p, o)
    assert w[rid == "B"][0] == 1.0
    assert w[rid == "B"][1] == 1.0
    # A informative & highest EV -> above the informative mean; D below.
    assert w[rid == "A"][0] > 1.0
    assert w[rid == "D"][0] < 1.0
    # normalisation is over informative races only (A,D) -> their scalar mean == 1
    assert abs((w[rid == "A"][0] + w[rid == "D"][0]) / 2 - 1.0) < 1e-12


def test_no_partial_field_maximum():
    # A has a huge-EV horse but another horse missing p -> incomplete field -> neutral (1.0),
    # NOT upweighted by the partial max. Complete-field C (high) / E (low) are the ones weighted.
    rid = np.array(["A", "A", "C", "C", "E", "E"])
    p = np.array([0.90, np.nan, 0.90, 0.10, 0.10, 0.10])  # A row2 missing p
    o = np.array([5.0, 3.0, 5.0, 3.0, 4.0, 2.0])
    w = build_race_weights(rid, p, o)
    assert w[rid == "A"][0] == 1.0  # neutral despite the huge EV on row1
    assert w[rid == "C"][0] > 1.0   # complete field, high EV -> upweighted
    assert w[rid == "E"][0] < 1.0


def test_odds_cap_excludes_longshots_from_max():
    # race A's only high-EV horse is a capped longshot (odds >= 21) -> excluded -> ev_r from
    # remaining (low) -> low weight, not upweighted by the un-bettable longshot.
    rid = np.array(["A", "A", "B", "B"])
    p = np.array([0.10, 0.50, 0.30, 0.10])  # A: longshot p=0.5 @ 50.0 => EV 25 but capped
    o = np.array([3.0, 50.0, 5.0, 2.0])     # B best EV = 1.5 bettable
    w = build_race_weights(rid, p, o)
    # A's cap-eligible best EV = 0.10*3 = 0.3; B's = 0.30*5 = 1.5 -> B > A
    assert w[rid == "B"][0] > w[rid == "A"][0]


def test_capped_horse_change_does_not_move_weight():
    # changing a horse whose odds >= cap must not change any weight (excluded from the max)
    rid = np.array(["A", "A", "B", "B"])
    base_p = np.array([0.30, 0.02, 0.25, 0.10])
    base_o = np.array([5.0, 40.0, 4.0, 3.0])
    w0 = build_race_weights(rid, base_p, base_o)
    bumped = base_p.copy()
    bumped[1] = 0.40  # the capped (odds 40) horse -> excluded, weight must be unchanged
    w1 = build_race_weights(rid, bumped, base_o)
    assert np.array_equal(w0, w1)


def test_deterministic_and_order_independent():
    rows = _race("A", [1.4, 0.3], [6.0, 2.0]) + _race("B", [0.7, 0.5], [4.0, 3.0])
    rid, p, o = _split(rows)
    w = build_race_weights(rid, p, o)
    perm = np.array([2, 0, 3, 1])
    wp = build_race_weights(rid[perm], p[perm], o[perm])
    assert np.allclose(wp, w[perm])


def test_all_neutral_when_no_informative_race():
    rid = np.array(["A", "A"])
    p = np.array([np.nan, 0.2])  # incomplete -> neutral
    o = np.array([5.0, 3.0])
    w = build_race_weights(rid, p, o)
    assert np.array_equal(w, np.ones(2))


def test_all_capped_race_is_neutral():
    # A: complete field but EVERY horse odds>=21 -> no cap-eligible -> NEUTRAL 1.0 (codex B4),
    # excluded from normalisation. B (high) / C (low) are the informative pair.
    rid = np.array(["A", "A", "B", "B", "C", "C"])
    p = np.array([0.30, 0.20, 0.30, 0.10, 0.10, 0.10])
    o = np.array([25.0, 30.0, 5.0, 3.0, 4.0, 2.0])
    w = build_race_weights(rid, p, o)
    assert w[rid == "A"][0] == 1.0 and w[rid == "A"][1] == 1.0
    assert w[rid == "B"][0] > 1.0 and w[rid == "C"][0] < 1.0
    assert abs((w[rid == "B"][0] + w[rid == "C"][0]) / 2 - 1.0) < 1e-12


def test_nonpositive_odds_makes_race_neutral():
    # zero/negative odds are invalid -> incomplete field -> neutral (codex M10)
    rid = np.array(["A", "A", "B", "B"])
    p = np.array([0.30, 0.10, 0.30, 0.10])
    o = np.array([5.0, 0.0, 4.0, -1.0])  # A has a 0-odds row; B has a negative-odds row
    w = build_race_weights(rid, p, o)
    assert np.array_equal(w, np.ones(4))


def test_prob_out_of_range_makes_race_neutral():
    rid = np.array(["A", "A"])
    p = np.array([1.5, 0.2])  # p>1 invalid -> incomplete -> neutral
    o = np.array([5.0, 3.0])
    w = build_race_weights(rid, p, o)
    assert np.array_equal(w, np.ones(2))


def test_assert_race_constant_rejects_per_horse_weights():
    rid = np.array(["A", "A", "B"])
    with pytest.raises(ValueError, match="not constant within race"):
        assert_race_constant(rid, np.array([1.0, 1.2, 0.8]))  # A rows differ
    # constant per race -> ok
    assert_race_constant(rid, np.array([1.1, 1.1, 0.9]))


def test_assert_race_constant_rejects_non_finite():
    rid = np.array(["A", "A"])
    for bad in ([np.nan, np.nan], [np.inf, np.inf], [1.0, np.nan]):
        with pytest.raises(ValueError, match="non-finite"):
            assert_race_constant(rid, np.array(bad))
