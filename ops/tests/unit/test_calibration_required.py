"""Required calibration never starts a legacy subprocess because its path is missing."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from horseracing_ops import runner


@pytest.mark.parametrize("prefix", ["PREDICT", "RECOMMEND", "REFRESH"])
@pytest.mark.parametrize("manifest", [None, "", "   "])
def test_required_path_missing_rejected(prefix, manifest, monkeypatch):
    monkeypatch.setenv(f"{prefix}_CALIB_MODE", "manifest-required")
    monkeypatch.delenv(f"{prefix}_CALIB_MANIFEST", raising=False)
    if manifest is not None:
        monkeypatch.setenv(f"{prefix}_CALIB_MANIFEST", manifest)
    with pytest.raises(ValueError, match="nonempty manifest path"):
        runner._calib_argv(prefix)


@pytest.mark.parametrize("prefix", ["PREDICT", "RECOMMEND", "REFRESH"])
def test_default_and_explicit_calibration(prefix, monkeypatch):
    monkeypatch.delenv(f"{prefix}_CALIB_MODE", raising=False)
    monkeypatch.delenv(f"{prefix}_CALIB_MANIFEST", raising=False)
    assert runner._calib_argv(prefix) == []
    monkeypatch.setenv(f"{prefix}_CALIB_MANIFEST", "/abs/model-calibration.json")
    assert runner._calib_argv(prefix) == []
    monkeypatch.setenv(f"{prefix}_CALIB_MODE", "manifest-required")
    assert runner._calib_argv(prefix) == [
        "--calib-manifest", "/abs/model-calibration.json", "--calib-mode", "manifest-required",
    ]


@pytest.mark.parametrize("mode", ["", "manifest-requird", "unknown"])
def test_unknown_mode_does_not_fall_back(mode):
    with pytest.raises(ValueError, match="unknown calibration mode"):
        runner._calibration_args(mode=mode, manifest="/abs/calibration.json")


@pytest.mark.parametrize("prefix,launch", [
    ("PREDICT", runner._serving_predict), ("RECOMMEND", runner._betting_recommend),
])
def test_per_race_missing_configuration_does_not_launch(prefix, launch, monkeypatch):
    monkeypatch.setenv(f"{prefix}_CALIB_MODE", "manifest-required")
    monkeypatch.delenv(f"{prefix}_CALIB_MANIFEST", raising=False)
    monkeypatch.setattr(runner, "owner_database_url", lambda: "postgresql://test/test")
    subprocess = Mock()
    monkeypatch.setattr(runner.subprocess, "run", subprocess)
    with pytest.raises(ValueError, match="nonempty manifest path"):
        launch("202606010101")
    subprocess.assert_not_called()


def test_direct_refresh_requires_path_before_launch(monkeypatch):
    monkeypatch.setattr(runner, "owner_database_url", lambda: "postgresql://test/test")
    subprocess = Mock()
    monkeypatch.setattr(runner.subprocess, "run", subprocess)
    with pytest.raises(ValueError, match="nonempty manifest path"):
        runner._live_refresh("2026-08-01", "2026-08-02", calib_mode="manifest-required")
    subprocess.assert_not_called()


def test_refresh_job_does_not_drop_required_mode(monkeypatch):
    monkeypatch.setenv("REFRESH_CALIB_MODE", "manifest-required")
    monkeypatch.delenv("REFRESH_CALIB_MANIFEST", raising=False)
    launch = Mock()
    monkeypatch.setattr(runner, "_live_refresh", launch)
    with pytest.raises(ValueError, match="nonempty manifest path"):
        runner.run_refresh_range(None, SimpleNamespace(scope_value="2026-08-01..2026-08-02"))
    launch.assert_not_called()


@pytest.mark.parametrize("origin", ["manual_ui", "auto_after_refresh"])
def test_predict_configuration_is_checked_before_capture(origin, monkeypatch):
    monkeypatch.setenv("PREDICT_CALIB_MODE", "manifest-required")
    monkeypatch.delenv("PREDICT_CALIB_MANIFEST", raising=False)
    capture, serving, session = Mock(), Mock(), Mock()
    monkeypatch.setattr(runner, "_live_capture_chaos", capture)
    monkeypatch.setattr(runner, "_serving_predict", serving)
    job = SimpleNamespace(scope_value="202606010101", summary={"predict_origin": origin})
    with pytest.raises(runner.CalibrationConfigurationError, match="nonempty manifest path"):
        runner.run_predict(session, job)
    capture.assert_not_called()
    serving.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert job.summary == {"predict_origin": origin}
