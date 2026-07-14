"""Feature 062: as-of Elo rating — correctness, leak boundary, pool-end independence (INV-R1..R6).

Ratings are the repo's first STATEFUL as-of feature (sequential 1-pass), so the materialize-safety
tests (pool-end independence, determinism, whole-day freeze) are the load-bearing ones — a subtle
ordering bug would silently make adopted-model predictions non-deterministic.
"""

from __future__ import annotations

import pandas as pd
import pytest
from horseracing_db.enums import ResultStatus

from horseracing_features.rating_features import (
    INIT_RATING,
    K_FACTOR,
    RATING_COLUMNS,
    SCALE,
    build_base_rating_features,
)
from tests._frames import make_frames


def _race(rid, date, horses, **kw):
    return {"race_id": rid, "race_date": date, "horses": horses, **kw}


def _h(hid, fo, **kw):
    return {"horse_id": hid, "finish_order": fo, **kw}


def _rows(frames):
    return build_base_rating_features(frames).set_index(["race_id", "horse_id"]).sort_index()


# --- INV-R6: correctness (hand-computed Elo) ------------------------------------------


def test_debut_is_init_rating_and_zero_starts():
    specs = [_race("200801010101", "2008-01-01", [_h("A", 1), _h("B", 2), _h("C", 3)])]
    r = _rows(make_frames(specs))
    for h in ("A", "B", "C"):
        row = r.loc[("200801010101", h)]
        assert row["asof_rating"] == INIT_RATING
        assert row["asof_rating_max"] == INIT_RATING
        assert row["asof_rating_starts"] == 0.0
        assert pd.isna(row["asof_rating_recent_delta"])  # debut = no momentum


def test_hand_computed_elo_delta_three_horse_race():
    # race1: A>B>C (all 1500). K=24, m=3. A:+12, B:0, C:−12. Verify at each horse's NEXT start.
    specs = [
        _race("200801010101", "2008-01-01", [_h("A", 1), _h("B", 2), _h("C", 3)]),
        _race("200802010101", "2008-02-01", [_h("A", 1), _h("B", 2)]),
        _race("200802020101", "2008-02-02", [_h("C", 1), _h("X", 2)]),
    ]
    r = _rows(make_frames(specs))
    assert r.loc[("200802010101", "A"), "asof_rating"] == pytest.approx(1512.0)
    assert r.loc[("200802010101", "B"), "asof_rating"] == pytest.approx(1500.0)
    assert r.loc[("200802020101", "C"), "asof_rating"] == pytest.approx(1488.0)


def test_consistent_winner_rating_rises():
    specs = [
        _race(f"20080{i}010101", f"2008-0{i}-01", [_h("A", 1), _h("B", 2)])
        for i in range(1, 7)
    ]
    r = _rows(make_frames(specs))
    a_ratings = [r.loc[(f"20080{i}010101", "A"), "asof_rating"] for i in range(1, 7)]
    assert a_ratings == sorted(a_ratings)  # monotonically non-decreasing (A always wins)
    assert a_ratings[-1] > a_ratings[0]


def test_tie_is_half_point():
    # A and B dead-heat for 1st (both finish_order 1); no rating change (E=0.5, S=0.5).
    specs = [_race("200801010101", "2008-01-01", [_h("A", 1), _h("B", 1)]),
             _race("200802010101", "2008-02-01", [_h("A", 1), _h("B", 2)])]
    r = _rows(make_frames(specs))
    assert r.loc[("200802010101", "A"), "asof_rating"] == pytest.approx(1500.0)


def test_dnf_excluded_from_update():
    # C is a DNF (stopped, no finish_order): m=2 (A,B only), C's rating unchanged at its next start.
    specs = [
        _race("200801010101", "2008-01-01",
              [_h("A", 1), _h("B", 2),
               {"horse_id": "C", "finish_order": None, "result_status": ResultStatus.STOPPED}]),
        _race("200802010101", "2008-02-01", [_h("C", 1), _h("D", 2)]),
        _race("200802020101", "2008-02-02", [_h("A", 1), _h("B", 2)]),
    ]
    r = _rows(make_frames(specs))
    assert r.loc[("200802010101", "C"), "asof_rating"] == INIT_RATING  # DNF -> no change
    # A/B updated as a 2-horse race: A +12, B −12
    assert r.loc[("200802020101", "A"), "asof_rating"] == pytest.approx(1512.0)


def test_effective_field_size_denominator():
    # 4 starters but 1 DNF -> m=3 for K/(m−1). Winner A beats 2 ranked rivals.
    specs = [
        _race("200801010101", "2008-01-01",
              [_h("A", 1), _h("B", 2), _h("C", 3),
               {"horse_id": "D", "finish_order": None, "result_status": ResultStatus.STOPPED}]),
        _race("200802010101", "2008-02-01", [_h("A", 1), _h("Z", 2)]),
    ]
    r = _rows(make_frames(specs))
    # m=3 (A,B,C): A gains K/(m−1)·Σ(1−0.5) = 24/2·1.0 = +12
    assert r.loc[("200802010101", "A"), "asof_rating"] == pytest.approx(1512.0)


# --- INV-R1: leak boundary (same-day freeze, own/future invariance) -------------------


def _base_specs():
    return [
        _race("200801010101", "2008-01-01", [_h("A", 1), _h("B", 2)]),
        _race("200802010101", "2008-02-01", [_h("A", 1), _h("B", 2)]),  # target-ish
    ]


def test_invariant_to_own_and_future_results():
    r0 = _rows(make_frames(_base_specs()))
    tgt = ("200802010101", "A")
    base = r0.loc[tgt, "asof_rating"]
    # (i) change the TARGET race's own result -> its as-of rating (morning) unchanged
    specs = _base_specs()
    specs[1]["horses"][0]["finish_order"] = 2
    specs[1]["horses"][1]["finish_order"] = 1
    assert _rows(make_frames(specs)).loc[tgt, "asof_rating"] == base
    # (ii) add a FUTURE race -> past row unchanged (pool-end independence, INV-R2)
    specs = _base_specs() + [_race("200812010101", "2008-12-01", [_h("A", 1), _h("Q", 2)])]
    assert _rows(make_frames(specs)).loc[tgt, "asof_rating"] == base


def test_same_day_other_race_frozen():
    # A's rating entering race on 2008-02-01 must not see a same-day earlier race's result.
    r0 = _rows(make_frames(_base_specs()))
    base = r0.loc[("200802010101", "A"), "asof_rating"]
    specs = _base_specs() + [
        _race("200802010100", "2008-02-01", [_h("A", 1), _h("W", 2)]),  # same day, earlier race_id
    ]
    # A's rating in race ...0101 uses the MORNING snapshot -> unaffected by the same-day ...0100
    assert _rows(make_frames(specs)).loc[("200802010101", "A"), "asof_rating"] == base


def test_same_day_double_runner_sees_identical_morning():
    # A races twice on the same day: both starts see the SAME morning rating (codex #3).
    specs = [
        _race("200801010101", "2008-01-01", [_h("A", 1), _h("B", 2)]),
        _race("200802010101", "2008-02-01", [_h("A", 1), _h("C", 2)]),
        _race("200802010102", "2008-02-01", [_h("A", 1), _h("D", 2)]),
    ]
    r = _rows(make_frames(specs))
    r1 = r.loc[("200802010101", "A"), "asof_rating"]
    r2 = r.loc[("200802010102", "A"), "asof_rating"]
    assert r1 == r2  # identical morning snapshot in both same-day starts


def test_positive_control_past_result_changes_rating():
    base = _rows(make_frames(_base_specs())).loc[("200802010101", "A"), "asof_rating"]
    specs = _base_specs()
    specs[0]["horses"][0]["finish_order"] = 2  # A LOSES race 1 now
    specs[0]["horses"][1]["finish_order"] = 1
    assert _rows(make_frames(specs)).loc[("200802010101", "A"), "asof_rating"] != base


# --- INV-R3: determinism -------------------------------------------------------------


def test_deterministic_repeat_build():
    specs = [
        _race(f"20080{i}010101", f"2008-0{i}-01", [_h("A", 1), _h("B", 2), _h("C", 3)])
        for i in range(1, 7)
    ]
    frames = make_frames(specs)
    a = build_base_rating_features(frames)
    b = build_base_rating_features(frames)
    pd.testing.assert_frame_equal(a, b, check_exact=True)


def test_invariant_to_input_row_order():
    """The internal chronological sort must make the output independent of input row order —
    a positional-vs-sorted index misalignment (scrambled values) would only surface when the
    input isn't already sorted (the real DB is not)."""
    specs = [
        _race(f"20080{i}0{r}0101", f"2008-0{i}-0{r}", [_h("A", 1), _h("B", 2), _h("C", 3)])
        for i in range(1, 5) for r in range(1, 4)
    ]
    frames = make_frames(specs)
    base = build_base_rating_features(frames)
    # shuffle every source frame's row order deterministically (reverse) — output must match
    shuffled = type(frames)(
        races=frames.races.iloc[::-1].reset_index(drop=True),
        race_horses=frames.race_horses.iloc[::-1].reset_index(drop=True),
        race_results=frames.race_results.iloc[::-1].reset_index(drop=True),
        horses=frames.horses,
    )
    out = build_base_rating_features(shuffled)
    pd.testing.assert_frame_equal(base, out, check_exact=True)


def test_constants_are_fixed():
    assert (K_FACTOR, INIT_RATING, SCALE) == (24.0, 1500.0, 400.0)
    assert set(RATING_COLUMNS) == {
        "asof_rating", "asof_rating_recent_delta", "asof_rating_max", "asof_rating_starts",
    }


# --- additive + registry integrity (058/061 同型) ------------------------------------


def test_rating_is_not_in_the_default_feature_set():
    """062 was REJECTED at the pre-registered gate (redundant with existing ability features under
    pl_topk). The rating columns must therefore NOT be registered / model inputs — the module and
    these tests are preserved as the documented negative result, not wired into production."""
    from horseracing_features.registry import (
        FEATURE_GROUPS,
        FEATURE_VERSION,
        model_input_features,
    )

    assert FEATURE_VERSION == "features-018"  # 062 rating still rejected; 070 reverted
    rating_cols = {
        "asof_rating", "asof_rating_recent_delta", "asof_rating_max",
        "asof_rating_starts", "asof_rating_vs_field",
    }
    assert not (rating_cols & set(model_input_features()))
    assert "rating" not in set(FEATURE_GROUPS.values())


def test_rating_columns_are_self_consistent_and_unique():
    frames = make_frames(_base_specs())
    out = build_base_rating_features(frames)
    assert not out.duplicated(subset=["race_id", "horse_id"]).any()
    assert list(out.columns) == ["race_id", "horse_id", *RATING_COLUMNS]


def test_no_odds_or_result_leakage_in_source():
    """grep-style guard: the rating module reads finish_order/race_date only, never odds/payout."""
    import pathlib

    src = pathlib.Path(
        "src/horseracing_features/rating_features.py"
    ).read_text()
    for tok in ("odds", "payout", "popularity", "dividend"):
        assert tok not in src, tok
