"""Feature 058 (案C'): serving feature-version compatibility gate, at the load_serving_model level.

The compat path lets an OLDER, parity-tested feature version serve under the current registry, but
must NOT loosen fail-closed for a same-version model whose hash no longer matches the current schema
(the blocker codex flagged). These tests drive the real loader against a synthetic ACTIVE model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from horseracing_db.models import ModelVersion
from horseracing_features.registry import FEATURE_VERSION

from horseracing_serving.model_loader import ServingError, load_serving_model
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration


def _metadata_path(session, mv: str) -> Path:
    row = session.get(ModelVersion, mv)
    return Path(row.weights_uri).parent / "metadata.json"


def test_exact_hash_model_loads(session, tmp_path):
    # make_active_model saves feature_hash over the CURRENT model_input_features() -> exact path.
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    model = load_serving_model(session, mv)
    assert model.feature_cols  # loads fine on the exact path


def test_same_version_hash_mismatch_fails_closed(session, tmp_path):
    # BLOCKER guard: an artifact CLAIMING the current version but carrying a NON-current hash
    # (e.g. a drop_features ablation build or a corrupted artifact) must fail closed, exactly as it
    # did before Feature 058 — the compat path is ONLY for pinned older versions.
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    meta_path = _metadata_path(session, mv)
    meta = json.loads(meta_path.read_text())
    meta["feature_version"] = FEATURE_VERSION      # claims the current version...
    meta["feature_hash"] = "deadbeef" * 8          # ...but with a hash that is not the current one
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(ServingError):
        load_serving_model(session, mv)


def test_unpinned_prior_version_fails_closed(session, tmp_path):
    # An older version that is NOT in the pinned compatibility map must fail closed.
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    meta_path = _metadata_path(session, mv)
    meta = json.loads(meta_path.read_text())
    meta["feature_version"] = "features-000"       # not a pinned compatible prior
    meta["feature_hash"] = "deadbeef" * 8
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(ServingError):
        load_serving_model(session, mv)
