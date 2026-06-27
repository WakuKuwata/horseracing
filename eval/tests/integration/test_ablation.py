"""Feature 020 US2 (SC-007/SC-004): group ablation is a separable DIAGNOSTIC, not a selector.

Each group is dropped in turn and its OOS LogLoss contribution measured separately. The fakes are
rigged so human_form matters more than recent_form, proving the two (which share race history) are
distinguishable. Ablation does NOT pick the adopted feature set — the candidate stays fixed a priori.
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_eval.ablation import evaluate_group_ablation
from tests._fakepredictor import ablation_predictor_factory
from tests._synth import insert_race, make_informative_field

pytestmark = pytest.mark.integration

# group -> its feature columns (mirror of the inverted registry FEATURE_GROUPS)
_GROUPS = {
    "recent_form": ["avg_last3_finish", "recent_win_rate"],
    "aptitude": ["dist_band_win_rate", "dist_band_avg_finish", "surface_win_rate"],
    "race_condition": ["class_transition", "field_size"],
    "human_form": ["jockey_win_rate", "trainer_win_rate"],
}


def _seed(session):
    for year in (2007, 2008, 2009):
        for r in range(1, 6):
            rid = f"{year}06{r:02d}{r:02d}01"
            insert_race(
                session, race_id=rid, race_date=datetime.date(year, 6, r),
                horses=make_informative_field(8, winner=0),
            )


def test_group_contributions_are_separated(session):
    _seed(session)
    report = evaluate_group_ablation(
        session, make_predictor=ablation_predictor_factory(), groups=_GROUPS,
    )
    assert set(report.group_contribution) == set(_GROUPS)         # every group reported separately
    # dropping a group worsens LogLoss -> positive contribution (the group helps)
    assert all(c > 0 for c in report.group_contribution.values())
    # SC-007: human_form and recent_form are distinguishable (human_form weighted heavier)
    assert report.group_contribution["human_form"] > report.group_contribution["recent_form"]


def test_ablation_is_diagnostic_only(session):
    """SC-004: ablation reports contributions but selects nothing — subsetting groups is allowed
    and does not change which features the candidate uses (that stays fixed a priori)."""
    _seed(session)
    subset = evaluate_group_ablation(
        session, make_predictor=ablation_predictor_factory(),
        groups={k: _GROUPS[k] for k in ("human_form", "recent_form")},
    )
    assert set(subset.group_contribution) == {"human_form", "recent_form"}
    assert subset.full_logloss > 0  # full candidate still measured on the fixed set
