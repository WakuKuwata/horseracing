"""Feature 074 US1 (T010): validation results do not affect OOF predictions."""

from __future__ import annotations

import pytest

from horseracing_training.oof_generate import generate_oof_bundle
from horseracing_training.recipe import ModelRecipe, RecipeFactory
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration

_FIRST_VALID = 2008


def _fast_factory(session):
    # binary + no target-encoding + identity calibration: fast and robust on tiny synthetic data.
    recipe = ModelRecipe(objective="binary", calibration="none", target_encode_cols=())
    return RecipeFactory(session=session, recipe=recipe)


def test_oof_predictions_are_invariant_to_validation_result(session, tmp_path):
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=10, field_size=8)
    _path_before, payload_before = generate_oof_bundle(
        session, factory=_fast_factory(session), out_root=str(tmp_path / "before"),
        first_valid_year=_FIRST_VALID, num_threads=1,
    )

    from horseracing_db.models import Race, RaceResult
    from sqlalchemy import select

    # Use the latest race in the last valid year: unlike 2008, a 2009 race is never in a
    # later fold's train set, and the latest race cannot affect later races' as-of features.
    races = session.scalars(
        select(Race).where(Race.race_id.in_(payload_before["predictions"]))
    ).all()
    target_race = max(
        (race for race in races if race.race_date.year == 2009),
        key=lambda race: (race.race_date, race.race_id),
    )
    results = list(
        session.scalars(
            select(RaceResult)
            .where(RaceResult.race_id == target_race.race_id)
            .order_by(RaceResult.finish_order, RaceResult.horse_id)
        )
    )
    assert len(results) >= 2, "expected at least two finishers to swap"
    assert results[0].finish_order == 1
    assert results[1].finish_order == 2
    results[0].finish_order, results[1].finish_order = (
        results[1].finish_order,
        results[0].finish_order,
    )
    session.commit()

    _path_after, payload_after = generate_oof_bundle(
        session, factory=_fast_factory(session), out_root=str(tmp_path / "after"),
        first_valid_year=_FIRST_VALID, num_threads=1,
    )

    assert payload_after["predictions"] == payload_before["predictions"]
