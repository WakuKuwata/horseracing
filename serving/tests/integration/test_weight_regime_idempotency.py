"""Feature 091 (research D6): the regime marker must take part in the idempotency key.

The trap 076's ';calib=' already fell into: a marker that is written but not keyed means a run
made under the OLD condition counts as "already done". Here the cost is concrete — a race
predicted live before the weights were published would permanently block the better-informed
full-info prediction.
"""

from __future__ import annotations

import pytest

from horseracing_serving.pipeline import _has_run_for_model
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded_race(session, tmp_path):
    seed_learnable(session, years=(2008,), races_per_year=1, field_size=6)
    return "200801010101", make_active_model(session, tmp_path)


def _run(session, race_id, model_version, logic_version):
    from horseracing_db.models import PredictionRun

    r = PredictionRun(
        race_id=race_id, model_version=model_version, logic_version=logic_version,
    )
    session.add(r)
    session.flush()
    return r


def test_serving_regime_run_does_not_block_the_full_info_one(session, seeded_race):
    race_id, mv = seeded_race
    _run(session, race_id, mv, "feat=features-021;serve=serve-0.1.0;wregime=serving")

    assert _has_run_for_model(session, race_id, mv, wregime="serving") is True
    # the point: once the weights arrive, the full-info prediction is still owed
    assert _has_run_for_model(session, race_id, mv, wregime="full_info") is False

    _run(session, race_id, mv, "feat=features-021;serve=serve-0.1.0;wregime=full_info")
    assert _has_run_for_model(session, race_id, mv, wregime="full_info") is True


def test_markerless_runs_stay_matched_by_markerless_checks(session, seeded_race):
    """Models without prev_weight write no marker; their idempotency must be unchanged, or every
    pre-091 run in the database would look missing and be regenerated."""
    race_id, mv = seeded_race
    _run(session, race_id, mv, "feat=features-018;serve=serve-0.1.0")
    assert _has_run_for_model(session, race_id, mv, wregime=None) is True
    # ...and a marked query must not be satisfied by an unmarked run
    assert _has_run_for_model(session, race_id, mv, wregime="serving") is False


def test_a_marked_run_does_not_satisfy_the_markerless_check(session, seeded_race):
    race_id, mv = seeded_race
    _run(session, race_id, mv, "feat=features-021;serve=serve-0.1.0;wregime=full_info")
    assert _has_run_for_model(session, race_id, mv, wregime=None) is False
