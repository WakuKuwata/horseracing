"""Self-test for the shared leak-boundary helpers (Feature 020 T002).

Proves the helpers (a) pass on a known leak-safe feature (020 human_form) and
(b) actually fail when a build function leaks — so they are a real guard, not a
no-op. Exercised on ``build_human_form_features`` (cross-horse jockey/trainer rate,
target-row + same-day excluded).
"""

from __future__ import annotations

import pandas as pd
import pytest
from horseracing_db.enums import ResultStatus

from horseracing_features.human_form import build_human_form_features
from horseracing_features.loader import Frames
from tests._frames import make_frames
from tests._leakcheck import (
    assert_crosshorse_excludes,
    assert_cutoff_invariant,
    assert_invariant,
    first_diff,
    rows_equal,
    target_row,
)

_TARGET = ("200803010101", "H")
_COLS = ["jockey_win_rate", "trainer_win_rate"]


# J1 rides: 2 prior finished mounts (1 win) + a same-day sibling C + the target mount H.
def _specs() -> list[dict]:
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01",
         "horses": [{"horse_id": "A", "jockey_id": "J1", "trainer_id": "T1",
                     "finish_order": 1}]},
        {"race_id": "200802010101", "race_date": "2008-02-01",
         "horses": [{"horse_id": "B", "jockey_id": "J1", "trainer_id": "T1",
                     "finish_order": 5}]},
        {"race_id": "200803010101", "race_date": "2008-03-01",
         "horses": [{"horse_id": "H", "jockey_id": "J1", "trainer_id": "T1",
                     "finish_order": 3}]},
        {"race_id": "200803010102", "race_date": "2008-03-01",
         "horses": [{"horse_id": "C", "jockey_id": "J1", "trainer_id": "T1",
                     "finish_order": 3}]},
    ]


def _set_finish(frames: Frames, race_id: str, horse_id: str, order: int) -> None:
    m = (frames.race_results["race_id"] == race_id) & (
        frames.race_results["horse_id"] == horse_id
    )
    frames.race_results.loc[m, "finish_order"] = order
    frames.race_results.loc[m, "result_status"] = ResultStatus.FINISHED


# --- primitives ---------------------------------------------------------------

def test_rows_equal_nan_safe():
    a = pd.Series({"x": 1.0, "y": float("nan")})
    assert rows_equal(a, a.copy())                                  # NaN == NaN
    assert not rows_equal(a, pd.Series({"x": 2.0, "y": float("nan")}))
    msg = first_diff(a, pd.Series({"x": 2.0, "y": float("nan")}))
    assert msg is not None and msg.startswith("x:")  # names the first differing column


def test_target_row_requires_unique():
    r = target_row(build_human_form_features, make_frames(_specs()), *_TARGET)
    assert r.jockey_win_rate == 0.5   # 1 win / 2 prior mounts (before-only)


# --- the two named T002 checks pass on the leak-safe feature ------------------

def test_cutoff_invariant_passes():
    # mutating the target race's OWN result (dated on the cutoff) must not move features
    assert_cutoff_invariant(
        build_human_form_features, make_frames, _specs(), *_TARGET,
        lambda f: _set_finish(f, "200803010101", "H", 1), cols=_COLS,
    )


def test_crosshorse_excludes_same_day_sibling():
    # mutating a same-day sibling's (C) result must not move the target's cross-horse stats
    assert_crosshorse_excludes(
        build_human_form_features, make_frames, _specs(), *_TARGET,
        lambda f: _set_finish(f, "200803010102", "C", 1), cols=_COLS,
    )


# --- the helper actually FAILS when a build leaks -----------------------------

def _leaky_build(frames: Frames) -> pd.DataFrame:
    """A deliberately leaky feature: echoes the target row's own finish_order."""
    rr = frames.race_results
    out = frames.race_horses[["race_id", "horse_id"]].merge(
        rr[["race_id", "horse_id", "finish_order"]], on=["race_id", "horse_id"], how="left"
    )
    out["leaky_finish"] = out["finish_order"].astype("float64")
    return out


def test_helper_detects_a_real_leak():
    base = make_frames(_specs())
    mut = make_frames(_specs())
    _set_finish(mut, "200803010101", "H", 1)  # change the target's own result
    with pytest.raises(AssertionError, match="leak:"):
        assert_invariant(_leaky_build, base, mut, *_TARGET, cols=["leaky_finish"])
