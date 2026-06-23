"""US3 (SC-006): date-mode inference + active model resolution (0/1/many)."""

from __future__ import annotations

import datetime

import pytest

from horseracing_serving.model_loader import ServingError, resolve_model_version
from horseracing_serving.pipeline import run_serving
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration


def test_resolution_zero_one_many(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)

    with pytest.raises(ServingError):  # 0 active
        resolve_model_version(session)

    make_active_model(session, tmp_path, model_version="m1")
    assert resolve_model_version(session) == "m1"  # exactly 1 active

    make_active_model(session, tmp_path, model_version="m2")
    with pytest.raises(ServingError):  # multiple active -> must specify
        resolve_model_version(session)
    assert resolve_model_version(session, "m1") == "m1"  # explicit works


def test_date_mode_infers_active_and_persists(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    make_active_model(session, tmp_path, model_version="onlyactive")

    # date-mode, model resolved from the single active model (no --model-version)
    results = run_serving(session, date=datetime.date(2008, 1, 2))
    assert len(results) >= 1
    assert all(r.model_version == "onlyactive" for r in results)
    assert all(r.n_horses == 8 for r in results)
