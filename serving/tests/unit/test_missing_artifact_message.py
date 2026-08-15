"""When a model's artifact is gone, say so — do not let a bare FileNotFoundError surface.

The active model's calibrator was lost with a deleted worktree, and every production prediction
died on `open()` deep inside the loader. The ops job recorded a traceback whose last line was a
path, with no mention of which model, that the file was a model artifact, or that the row could
be repaired. Diagnosing it meant reading the loader.

The registration guard stops new rows from pointing at disposable locations; this is what the
operator sees when an existing row has already gone stale.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import pytest

from horseracing_serving.model_loader import ServingError, load_serving_model


class _MV:
    def __init__(self, weights_uri, calibrator_uri):
        self.weights_uri = str(weights_uri)
        self.calibrator_uri = str(calibrator_uri)


class _Session:
    def __init__(self, mv):
        self._mv = mv

    def get(self, _model, _name):
        return self._mv


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    """A model directory that is complete except for whatever a test removes."""
    art = tmp_path / "artifacts" / "model_versions" / "lgbm-test"
    art.mkdir(parents=True)
    (art / "model.txt").write_text("tree\n")
    with (art / "calibrator.pkl").open("wb") as fh:
        pickle.dump({"identity": True}, fh)
    (art / "metadata.json").write_text(json.dumps({"feature_hash": "h", "feature_version": "v"}))
    monkeypatch.setattr(
        "horseracing_serving.model_loader.resolve_model_version", lambda *a, **k: "lgbm-test"
    )
    return art


def _load(art: Path, *, calibrator: Path | None = None):
    mv = _MV(art / "model.txt", calibrator or art / "calibrator.pkl")
    return load_serving_model(_Session(mv))


def test_a_missing_calibrator_names_the_model_and_the_path(artifacts):
    """The exact production failure: weights fine, calibrator pointing into a removed worktree."""
    gone = artifacts.parent / "removed-worktree" / "calibrator.pkl"

    with pytest.raises(ServingError) as exc:
        _load(artifacts, calibrator=gone)

    message = str(exc.value)
    assert "lgbm-test" in message, "the operator must learn WHICH model is broken"
    assert str(gone) in message, "and which file is missing"
    assert "calibrator" in message.lower()


def test_a_missing_booster_is_reported_the_same_way(artifacts):
    (artifacts / "model.txt").unlink()
    with pytest.raises(ServingError) as exc:
        _load(artifacts)
    assert "lgbm-test" in str(exc.value)
    assert "model.txt" in str(exc.value)


def test_the_message_points_at_the_repair(artifacts):
    """A model row whose files moved is fixable by correcting the URI; the error should say so
    rather than leaving the operator to guess that retraining is required."""
    gone = artifacts.parent / "gone" / "calibrator.pkl"
    with pytest.raises(ServingError, match="uri"):
        _load(artifacts, calibrator=gone)


def test_an_intact_model_is_not_blocked_by_the_check(artifacts):
    """The guard must not turn a working model into a failure; it fails later here only on the
    feature-hash gate, which proves it got past the artifact check."""
    with pytest.raises(ServingError, match="feature_hash"):
        _load(artifacts)
