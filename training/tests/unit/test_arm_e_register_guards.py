"""085 §5: registration guards. Both defects here let a MISDESCRIBED model reach the registry.

1. `--weight-mask-rate` is an optional argument, so omitting it silently built a pre-091 booster
   and registered it under an arm-E name (`lgbm-093-armE-wmask` could have carried no mask at
   all). The artifact records `weight_mask: null` and nothing downstream objects, because a
   maskless model trains fine and scores fine on the full-information window.

2. The round-trip parity check compared `max(booster_diff, calib_diff)` against 0. `max()` does
   not propagate NaN — `max(0.0, float("nan"))` is `0.0` — so a reloaded calibrator emitting NaN
   PASSED the one guard whose entire job is to fail closed.
"""

from __future__ import annotations

import pickle

import numpy as np
import pytest

from horseracing_training import arm_e_register as mod
from horseracing_training.arm_e_register import ArmERegisterError


def test_registration_refuses_to_infer_an_unmasked_build():
    """Fails before any DB or disk work — session=None proves nothing else was touched."""
    with pytest.raises(ArmERegisterError, match="no weight mask"):
        mod.run(None, model_version="lgbm-x-armE", artifacts_dir="/tmp/x")


def test_an_unmasked_build_is_allowed_when_stated_explicitly(monkeypatch):
    """The opt-out exists so pre-091 reproductions stay possible; it just has to be deliberate.
    Getting past the guard is enough — the next step is refused for an unrelated reason."""
    monkeypatch.setattr(mod, "load_eval_races", lambda session: [])
    with pytest.raises(ArmERegisterError, match="no eligible races"):
        mod.run(None, model_version="lgbm-x-armE", artifacts_dir="/tmp/x", allow_unmasked=True)


class _Calib:
    def __init__(self, out):
        self._out = out

    def transform(self, x):
        return np.full(len(x), self._out, dtype=float)


class _Booster:
    def num_feature(self):
        return 3

    def predict(self, x, raw_score=False):
        return np.zeros(len(x), dtype=float)


class _Servable:
    def __init__(self, calibrator):
        self.calibrator_ = calibrator

        class _WM:
            booster_ = _Booster()

        self.win_model_ = _WM()


def _artifacts(tmp_path, model_version: str, calibrator):
    art = tmp_path / "model_versions" / model_version
    art.mkdir(parents=True)
    (art / "model.txt").write_text("stub")
    with (art / "calibrator.pkl").open("wb") as fh:
        pickle.dump(calibrator, fh)
    return art


def test_a_non_finite_calibrator_fails_parity(tmp_path, monkeypatch):
    """THE regression: with the old `max(...) != 0` test this returned success."""
    monkeypatch.setattr("lightgbm.Booster", lambda model_file: _Booster())
    _artifacts(tmp_path, "mv", _Calib(np.nan))

    with pytest.raises(ArmERegisterError, match="non-finite"):
        mod._parity_check(
            _Servable(_Calib(0.5)), artifacts_dir=str(tmp_path), model_version="mv"
        )


def test_identical_artifacts_pass_parity(tmp_path, monkeypatch):
    """The guard must still accept a faithful round trip, or it would block every registration."""
    monkeypatch.setattr("lightgbm.Booster", lambda model_file: _Booster())
    _artifacts(tmp_path, "mv", _Calib(0.5))

    probes, worst = mod._parity_check(
        _Servable(_Calib(0.5)), artifacts_dir=str(tmp_path), model_version="mv"
    )
    assert worst == 0.0
    assert probes == 1001


def test_a_drifted_calibrator_still_fails_parity(tmp_path, monkeypatch):
    monkeypatch.setattr("lightgbm.Booster", lambda model_file: _Booster())
    _artifacts(tmp_path, "mv", _Calib(0.5000001))

    with pytest.raises(ArmERegisterError, match="parity failed"):
        mod._parity_check(
            _Servable(_Calib(0.5)), artifacts_dir=str(tmp_path), model_version="mv"
        )
