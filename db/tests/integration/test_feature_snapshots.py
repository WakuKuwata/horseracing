"""US4 / FR-020: feature_snapshots persist a reproducible feature reference."""

from __future__ import annotations

import pytest

from horseracing_db.models import FeatureSnapshot

from ._prediction_helpers import setup_run

pytestmark = pytest.mark.integration


def test_feature_snapshot_reproduces_input(session):
    run = setup_run(session)
    features = {
        "career_starts": 0,
        "has_past_race": False,
        "past_avg_finish": None,  # Unknown kept explicit
        "frame": 1,
    }
    session.add(FeatureSnapshot(
        prediction_run_id=run.prediction_run_id,
        horse_id="H1",
        feature_version="feat-v1",
        features=features,
    ))
    session.commit()

    snap = session.get(FeatureSnapshot, (run.prediction_run_id, "H1"))
    assert snap.feature_version == "feat-v1"
    # Same input is reconstructable from the snapshot, tied to the prediction run.
    assert snap.features == features
    assert snap.features["past_avg_finish"] is None
    assert snap.prediction_run_id == run.prediction_run_id
