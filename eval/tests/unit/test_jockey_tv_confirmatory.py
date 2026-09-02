"""Feature 107 の騎手時変切片 confirmatory driver の単体契約テスト。"""

from __future__ import annotations

import copy
import datetime
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from horseracing_eval.decision import _min_eval_days, critical_subgroups, gate_config_hash
from horseracing_eval.evidence import PairedEvidenceRow

REPO_ROOT = Path(__file__).resolve().parents[3]
DRIVER_PATH = REPO_ROOT / "scripts" / "jockey_tv_confirmatory.py"
SPIKE_PATH = REPO_ROOT / "scripts" / "jockey_timevarying_spike.py"
CONFIG_PATH = REPO_ROOT / "specs" / "107-jockey-tv-intercept" / "gate-config.json"
FROZEN_CONFIG_HASH = "c872172a57be8a052a7ecd9e4b6492574fcd852e1f8c2845e4736e3cddac8eaa"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return json.loads(CONFIG_PATH.read_text())


@pytest.fixture(scope="module")
def driver() -> ModuleType:
    class StubBase:
        pass

    def stub_module(name: str, **attributes: object) -> ModuleType:
        module = ModuleType(name)
        vars(module).update(attributes)
        return module

    training_package = stub_module("horseracing_training")
    training_package.__path__ = []
    stubs = {
        "pandas": stub_module("pandas"),
        "horseracing_training": training_package,
        "horseracing_training.calib_split": stub_module(
            "horseracing_training.calib_split",
            DEFAULT_CLIP=1e-15,
            CalibSplitFactory=StubBase,
            OofCalibratedPredictor=StubBase,
            _started_all_outcomes=lambda *_args, **_kwargs: None,
            day_block_partition=lambda *_args, **_kwargs: None,
        ),
        "horseracing_training.calibration": stub_module(
            "horseracing_training.calibration", fit_calibrator=lambda *_args, **_kwargs: None
        ),
        "horseracing_training.predictor": stub_module(
            "horseracing_training.predictor",
            LightGBMPredictor=StubBase,
            assemble_predictions=lambda *_args, **_kwargs: None,
        ),
        "horseracing_training.recipe": stub_module(
            "horseracing_training.recipe", ModelRecipe=StubBase
        ),
    }
    monkeypatch = pytest.MonkeyPatch()
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)

    prior_spike = sys.modules.pop("jockey_timevarying_spike", None)
    spec = importlib.util.spec_from_file_location("test_jockey_tv_confirmatory_driver", DRIVER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        monkeypatch.undo()
        sys.modules.pop("jockey_timevarying_spike", None)
        if prior_spike is not None:
            sys.modules["jockey_timevarying_spike"] = prior_spike
    return module


def test_repo_root_is_resolved_from_test_location() -> None:
    assert REPO_ROOT / "eval" / "tests" / "unit" == Path(__file__).resolve().parent
    assert DRIVER_PATH.is_file()
    assert SPIKE_PATH.is_file()
    assert CONFIG_PATH.is_file()


def test_three_point_constant_identity(driver: ModuleType, cfg: dict) -> None:
    """INV-J4: driver、spike、凍結 config の候補手続き定数を三点固定する。"""
    spike = driver._spike
    candidate = cfg["arms"]["candidate"]

    assert driver.WINDOW_DAYS == spike.WINDOW_DAYS == candidate["window_days"] == 730
    assert driver.MIN_RIDES == spike.MIN_RIDES == candidate["min_rides"] == 30
    assert tuple(candidate["lambda_clamp"]) == driver.LAMBDA_CLAMP == (10.0, 500.0)

    spike_source = SPIKE_PATH.read_text()
    estimate_b_start = spike_source.index("def estimate_b(")
    estimate_b_end = spike_source.index("\n\nclass ", estimate_b_start)
    estimate_b_source = spike_source[estimate_b_start:estimate_b_end]
    assert "10.0" in estimate_b_source
    assert "500.0" in estimate_b_source


def test_frozen_config_uses_real_gate_keys(cfg: dict) -> None:
    assert critical_subgroups(cfg) == []
    assert _min_eval_days(cfg) == 300
    assert gate_config_hash(cfg) == FROZEN_CONFIG_HASH


def test_screening_window_overlap_guard_fires(driver: ModuleType, cfg: dict) -> None:
    """FR-002: screening に使った 2025 年以降の窓を fail-closed にする条件。"""
    modified = copy.deepcopy(cfg)
    modified["eval_window"]["to"] = "2025-06-30"
    eval_to = datetime.date.fromisoformat(modified["eval_window"]["to"])

    assert driver.SCREENING_WINDOW_START == datetime.date(2025, 1, 1)
    assert eval_to >= driver.SCREENING_WINDOW_START
    assert driver.FROZEN_EVAL_FROM == datetime.date(2022, 1, 1)


def test_unmodified_frozen_constants_pass(driver: ModuleType, cfg: dict) -> None:
    driver.assert_frozen_constants(cfg)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("window_days", 365),
        ("min_rides", 10),
        ("lambda_clamp", [1.0, 500.0]),
    ],
)
def test_frozen_constant_mismatch_fails_closed(
    driver: ModuleType, cfg: dict, key: str, value: object
) -> None:
    modified = copy.deepcopy(cfg)
    modified["arms"]["candidate"][key] = value

    with pytest.raises(SystemExit):
        driver.assert_frozen_constants(modified)


def _evidence_row(seq: int, diff: float) -> PairedEvidenceRow:
    active_nll = 1.0
    return PairedEvidenceRow(
        seq=seq,
        race_id=f"race-{seq}",
        race_day=f"2024-01-{seq + 1:02d}",
        candidate_winner_nll=active_nll + diff,
        active_winner_nll=active_nll,
        diff=diff,
    )


def test_degeneracy_abort_predicate() -> None:
    """INV-J3: 全行差分ゼロだけを配線故障として abort する。"""
    all_zero_rows = tuple(_evidence_row(seq, 0.0) for seq in range(3))
    one_changed_row = (*all_zero_rows[:2], _evidence_row(2, -0.001))

    assert all(row.diff == 0.0 for row in all_zero_rows)
    assert not all(row.diff == 0.0 for row in one_changed_row)
