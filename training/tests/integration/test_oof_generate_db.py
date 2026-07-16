"""Feature 074 US1 (T008/T011): OOF generation mechanism, end-to-end on a testcontainer DB.

Validates the OOF *mechanism* — strict-past fold fitting and byte determinism — with a fast
injected recipe (binary, no TE) on small synthetic data, independent of any specific model's
feature version. The recipe-faithful lgbm-063 path (features-017) is exercised by the operator
smoke (T014); here we prove the fold machinery itself is leak-free and reproducible.
"""

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


def test_oof_strict_past_and_folds(session, tmp_path):
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=10, field_size=8)
    _path, payload = generate_oof_bundle(
        session, factory=_fast_factory(session), out_root=str(tmp_path),
        first_valid_year=_FIRST_VALID, num_threads=1,
    )
    # every OOF race is a validation-year race (>= first valid year); 2007 is train-only.
    from horseracing_db.models import Race
    from sqlalchemy import select
    race_years = {
        rid: session.scalar(select(Race.race_date).where(Race.race_id == rid)).year
        for rid in payload["predictions"]
    }
    assert race_years, "expected OOF predictions"
    assert all(y >= _FIRST_VALID for y in race_years.values())
    assert 2007 not in race_years.values()  # 2007 never appears as a valid (OOF) race

    # per-fold strict-past: the fold's train window ends strictly before its valid year.
    for fold in payload["per_fold"]:
        assert fold["train_through"][:4] < str(fold["valid_year"])
    assert payload["fold_boundaries"] == sorted(payload["fold_boundaries"])


def test_oof_generation_is_byte_deterministic(session, tmp_path):
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=8, field_size=6)
    _p1, payload1 = generate_oof_bundle(
        session, factory=_fast_factory(session), out_root=str(tmp_path / "a"),
        first_valid_year=_FIRST_VALID, num_threads=1,
    )
    _p2, payload2 = generate_oof_bundle(
        session, factory=_fast_factory(session), out_root=str(tmp_path / "b"),
        first_valid_year=_FIRST_VALID, num_threads=1,
    )
    from horseracing_probability.oof_bundle import compute_bundle_digest
    # bundle_digest is stamped on write; recompute it from each returned content payload.
    assert compute_bundle_digest(payload1) == compute_bundle_digest(payload2)
    assert payload1["prediction_checksum"] == payload2["prediction_checksum"]
    assert payload1["oof_race_set_hash"] == payload2["oof_race_set_hash"]
