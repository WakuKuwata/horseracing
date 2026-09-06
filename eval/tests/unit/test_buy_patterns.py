"""Feature 109 T015/T016: the frozen pattern family (enumeration + predicates)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _synth_buy import add_context, make_arrays  # noqa: E402
from horseracing_eval import buy_patterns as bp  # noqa: E402
from horseracing_eval.buy_pattern_gate import derive, fix_population, selection_hash  # noqa: E402

EXPECTED = {"C": 13, "C×H": 84, "C×EV": 28, "M": 10, "M×H": 18, "X": 12, "M×X": 192, "F": 36}


def _family():
    return bp.enumerate_family()


def test_family_counts_and_uniqueness():
    fam = _family()
    counts: dict[str, int] = {}
    for p in fam.patterns:
        counts[p.family] = counts.get(p.family, 0) + 1
    assert counts == EXPECTED
    assert len(fam.patterns) == 393
    ids = [p.pattern_id for p in fam.patterns]
    assert len(ids) == len(set(ids))
    assert [c.pattern_id for c in fam.controls] == ["no_bet", "favorite", "cap11_all", "cap21_all"]
    assert fam.control_aliases == {"ev1_all": "X.ev_ge_1"}
    assert "X.ev_ge_1" in ids


def test_folklore_singletons_that_duplicate_context_cells_are_absent():
    ids = {p.pattern_id for p in _family().patterns}
    for single in ("fs", "iv", "pf"):
        for h in bp.HORSE_RULES:
            assert f"F.{single}|H.{h}" not in ids
    for h in bp.HORSE_RULES:
        assert f"F.nt|H.{h}" in ids
        assert f"F.fs+nt+iv+pf|H.{h}" in ids


@pytest.mark.parametrize("bands", [bp.ODDS_BANDS, bp.Q_BANDS, bp.PQ_BANDS, bp.DIST_BANDS,
                                   bp.FIELD_BANDS, bp.INTERVAL_BANDS, bp.FAVQ_BANDS])
def test_bands_are_disjoint_and_cover_the_axis(bands):
    ordered = sorted(bands, key=lambda b: b[1])
    assert ordered[0][1] == 0.0 or ordered[0][1] == 1.0
    assert ordered[-1][2] == bp.INF
    for a, b in zip(ordered, ordered[1:], strict=False):
        assert a[2] == b[1]


def test_race_level_only_patterns_carry_a_horse_rule_and_available_at_is_latest():
    fam = _family()
    for p in fam.patterns:
        if p.race_level_only:
            assert p.horse_rule in bp.HORSE_RULES
        if p.horse_rule is not None or any(c.available_at == "closing" for c in p.conditions):
            assert p.available_at == "closing"
    pre = next(p for p in fam.patterns if p.pattern_id == "C.interval.28-69")
    assert pre.available_at == "pre_entry"
    assert next(p for p in fam.patterns if p.pattern_id == "C.field.le8|H.fav").available_at == "closing"


def test_forbidden_fields_are_rejected_at_definition_time():
    with pytest.raises(ValueError, match="ForbiddenField"):
        bp.Condition("model", "won", "is_true", "closing")
    with pytest.raises(ValueError, match="ForbiddenField"):
        bp.Condition("model", "finish_order", "in_band", "closing", lo=1, hi=2)


def test_enumeration_is_deterministic_json():
    a = json.dumps(_family().to_dict(), sort_keys=True, ensure_ascii=False)
    b = json.dumps(_family().to_dict(), sort_keys=True, ensure_ascii=False)
    assert a == b
    assert _family().hash() == _family().hash()


def _rows(seed=0, **kw):
    rng = np.random.default_rng(seed)
    arr = add_context(make_arrays(rng, **kw))
    arr, _ = fix_population(arr)
    return derive(arr, warmup_through="2000-01-01")


def test_masks_are_invariant_to_outcome_permutation():
    arr = _rows(1)
    fam = _family()
    before = {p.pattern_id: selection_hash(arr["race_id"][bp.mask_for(p, arr)],
                                           arr["horse_number"][bp.mask_for(p, arr)])
              for p in fam.patterns}
    rng = np.random.default_rng(9)
    shuffled = dict(arr)
    shuffled["won"] = rng.permutation(arr["won"])
    after = {p.pattern_id: selection_hash(shuffled["race_id"][bp.mask_for(p, shuffled)],
                                          shuffled["horse_number"][bp.mask_for(p, shuffled)])
             for p in fam.patterns}
    assert before == after


def test_missing_predicate_inputs_select_nothing_and_ties_are_unique():
    arr = _rows(2)
    p_first = next(p for p in _family().patterns if p.pattern_id == "C.interval.first_start")
    m = bp.mask_for(p_first, arr)
    assert np.array_equal(m, ~np.isfinite(arr["interval_days"]))
    p_iv = next(p for p in _family().patterns if p.pattern_id == "C.interval.28-69")
    assert not bp.mask_for(p_iv, arr)[~np.isfinite(arr["interval_days"])].any()
    # exactly one favourite per race even with equal odds
    tie = dict(arr)
    tie["odds"] = np.full(len(arr["odds"]), 5.0)
    tie = derive(tie, warmup_through="2000-01-01")
    fav_per_race = {}
    for rid, f, hn in zip(tie["race_id"], tie["is_fav"], tie["horse_number"], strict=True):
        if f:
            fav_per_race.setdefault(rid, []).append(hn)
    assert all(v == [min(tie["horse_number"][tie["race_id"] == r])] for r, v in fav_per_race.items())
    assert len(fav_per_race) == len(set(tie["race_id"]))


def test_hand_computed_fixture_for_c_ev_and_m_x_patterns():
    arr = _rows(3)
    fam = _family()
    c_ev = next(p for p in fam.patterns if p.pattern_id == "C.track.turf|X.ev_ge_1")
    expect = np.array([t == "芝" for t in arr["track_type"]]) & (arr["ev"] >= 1.0)
    assert np.array_equal(bp.mask_for(c_ev, arr), expect)
    m_x = next(p for p in fam.patterns if p.pattern_id == "M.odds.6-11|X.rank.1")
    expect2 = (arr["odds"] >= 6.0) & (arr["odds"] < 11.0) & (arr["p_rank"] == 1)
    assert np.array_equal(bp.mask_for(m_x, arr), expect2)
    fav_cell = next(p for p in fam.patterns if p.pattern_id == "M.favq.ge0.50|H.fav")
    expect3 = (arr["fav_q"] >= 0.5) & arr["is_fav"]
    assert np.array_equal(bp.mask_for(fav_cell, arr), expect3)


def test_canon_class_handles_split_spellings():
    assert bp.canon_class("１勝") == "C1" and bp.canon_class("500万") == "C1"
    assert bp.canon_class("2勝") == "C2" and bp.canon_class("1000万") == "C2"
    assert bp.canon_class("Ｇ１") == "OP" and bp.canon_class("OP(L)") == "OP"
    assert bp.canon_class("謎") is None


def test_pattern_round_trips_through_dict():
    fam = _family()
    for p in fam.patterns[:20]:
        d = p.to_dict()
        back = bp.pattern_from_dict(d)
        assert back.pattern_id == p.pattern_id and back.horse_rule == p.horse_rule
        assert [c.to_dict() for c in back.conditions] == d["conditions"]
