from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace

import pytest

from horseracing_training import chaos_bands
from horseracing_training import cli as training_cli
from horseracing_training.chaos_bands import (
    FrozenChaosHorse,
    ProspectiveChaosRace,
    prospective_report,
)


def _artifact(
    *,
    calibration_tolerance: dict | None = None,
    multiplicity_policy: str | None = None,
) -> SimpleNamespace:
    preregistration = {
        "primary_horizon": {
            "minimum_seconds_to_post": 600,
            "maximum_seconds_to_post": 86400,
            "basis": "test_window",
            "measured_coverage_of_pre_race_predict_clicks": 0.956,
        },
        "minimum_positives": 100,
        "minimum_race_days": 60,
        "final_decision_date": "2027-12-31",
        "promotion_rule": {
            "controlling_event": "s_ge_20",
            "secondary_event": "himo_are",
            "not_eligible": ["total_collapse"],
            "diagnostic_only": ["s_ge_30"],
            "insufficient_evidence_decision": (
                "NO_DECISION_and_remove_primary_panel"
            ),
        },
    }
    if calibration_tolerance is not None:
        preregistration["calibration_tolerance"] = calibration_tolerance
    if multiplicity_policy is not None:
        preregistration["multiplicity_policy"] = multiplicity_policy
    return SimpleNamespace(
        version="chaosbands-v1",
        artifact_digest="f" * 64,
        valid_from=datetime.date(2024, 1, 1),
        fit_through=datetime.date(2023, 12, 31),
        lambda2=0.8303689257547258,
        lambda3=0.7111058148742723,
        quintile_edges=(0.019571, 0.065928, 0.111811, 0.170308),
        preregistration=preregistration,
    )


def _field() -> tuple[FrozenChaosHorse, ...]:
    return tuple(
        FrozenChaosHorse(
            horse_id=f"h{rank:02d}",
            horse_number=rank,
            popularity=rank,
            odds=1.5 + rank,
        )
        for rank in range(1, 19)
    )


def _race(
    race_id: str,
    race_date: datetime.date,
    *,
    capture_strength: str = "confirmatory",
    snapshot_status: str = "active",
    snapshot_void_reason: str | None = None,
    capture_trigger: str = "daily_operational",
    seconds_to_post: int | None = 1800,
    current_started_field: frozenset[tuple[str, int]] | None = None,
    p_s_ge_20: float = 0.25,
    p_himo_are: float = 0.2,
    p_total_collapse: float = 0.05,
    first: tuple[str, ...] = ("h01",),
    second: tuple[str, ...] = ("h17",),
    third: tuple[str, ...] = ("h18",),
    readout_suffix: str = "a",
) -> ProspectiveChaosRace:
    field = _field()
    return ProspectiveChaosRace(
        race_id=race_id,
        race_date=race_date,
        readout_id=f"readout-{race_id}-{readout_suffix}",
        snapshot_id=f"snapshot-{race_id}-{readout_suffix}",
        snapshot_status=snapshot_status,
        snapshot_void_reason=snapshot_void_reason,
        capture_strength=capture_strength,
        capture_trigger=capture_trigger,
        seconds_to_post=seconds_to_post,
        frozen_field=field,
        current_started_field=(
            current_started_field
            if current_started_field is not None
            else frozenset(
                (horse.horse_id, horse.horse_number) for horse in field
            )
        ),
        field_size=18,
        p_s_ge_20=p_s_ge_20,
        p_himo_are=p_himo_are,
        p_total_collapse=p_total_collapse,
        first=first,
        second=second,
        third=third,
    )


def _coverage() -> dict:
    return {
        "schema_version": "chaos-coverage-v1",
        "capture_rate": {"numerator": 1, "denominator": 1, "rate": 1.0},
        "not_captured_characteristics": {"n_races": 0},
        "post_time_coverage": {"rate": 1.0},
    }


def _install_rows(monkeypatch, rows) -> None:
    monkeypatch.setattr(
        chaos_bands,
        "load_prospective_rows",
        lambda *_args, **_kwargs: list(rows),
    )
    monkeypatch.setattr(
        chaos_bands,
        "coverage_report",
        lambda *_args, **_kwargs: _coverage(),
    )


def test_outcomes_use_frozen_snapshot_ranks_never_current_race_horses(
    monkeypatch,
) -> None:
    frozen = _race("202401010101", datetime.date(2024, 1, 6))
    _install_rows(monkeypatch, [frozen])

    # A live/current popularity mapping would turn the same finishers into S=6,
    # not the frozen S=36. The loader may read RaceHorse identity/status for
    # FR-002b, but it must never read current popularity for outcome labels.
    current_popularity = {"h01": 1, "h17": 2, "h18": 3}
    assert sum(current_popularity[horse_id] for horse_id in ("h01", "h17", "h18")) == 6
    assert "RaceHorse.popularity" not in inspect.getsource(
        chaos_bands.load_prospective_rows
    )

    class _NoLiveQueries:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("prospective analysis must not query live race_horses")

    report = prospective_report(
        _NoLiveQueries(),
        artifact=_artifact(),
        as_of=datetime.date(2024, 12, 31),
        bootstrap_b=5,
    )

    assert report["cohort"]["analyzed_races"] == 1
    assert report["overall"]["events"]["s_ge_20"]["positives"] == 1
    assert report["overall"]["events"]["himo_are"]["positives"] == 1
    assert report["overall"]["events"]["total_collapse"]["positives"] == 0
    assert report["outcome_audit"]["rank_basis"] == "frozen_snapshot_popularity"
    assert report["outcome_audit"]["live_race_horses_popularity_used"] is False


def test_confirmation_cohort_filters_strength_validity_and_duplicate_races(
    monkeypatch,
) -> None:
    rows = [
        _race("202401010101", datetime.date(2024, 1, 6)),
        _race(
            "202401010102",
            datetime.date(2024, 1, 6),
            capture_strength="weak",
        ),
        _race("202301010101", datetime.date(2023, 12, 30)),
        _race("202401010103", datetime.date(2024, 1, 7), readout_suffix="a"),
        _race("202401010103", datetime.date(2024, 1, 7), readout_suffix="b"),
    ]
    _install_rows(monkeypatch, rows)

    report = prospective_report(
        object(),
        artifact=_artifact(),
        as_of=datetime.date(2024, 12, 31),
        bootstrap_b=5,
    )

    assert report["cohort"]["loaded_readouts"] == 5
    assert report["cohort"]["analyzed_races"] == 1
    assert report["cohort"]["exclusions"] == {
        "before_valid_from": 1,
        "non_confirmatory_capture": 1,
        "not_one_row_per_race": 1,
    }
    assert report["analysis_unit"]["one_row_per_race"] is True
    assert report["analysis_unit"]["latest_row_selection_forbidden"] is True


def test_primary_horizon_is_strict_and_capture_buckets_follow_both_bounds(
    monkeypatch,
) -> None:
    seconds = (599, 600, 1799, 1800, 3600, 10800, 21600, 43200, 86400, 86401)
    rows = [
        _race(
            f"2024{index:08d}",
            datetime.date(2024, 1, 1) + datetime.timedelta(days=index),
            seconds_to_post=value,
        )
        for index, value in enumerate(seconds)
    ]
    _install_rows(monkeypatch, rows)

    report = prospective_report(
        object(),
        artifact=_artifact(),
        as_of=datetime.date(2024, 12, 31),
        bootstrap_b=5,
    )

    assert report["analysis_unit"]["primary_horizon"] == {
        "mode": "artifact_seconds_to_post_window",
        "minimum_seconds_to_post": 600,
        "maximum_seconds_to_post": 86400,
    }
    assert report["cohort"]["analyzed_races"] == 8
    assert report["cohort"]["exclusions"]["outside_primary_horizon"] == 2
    assert [
        row["horizon"] for row in report["by_capture_horizon"]
    ] == [
        "10-30m",
        "30-60m",
        "1-3h",
        "3-6h",
        "6-12h",
        "12h+",
    ]


def test_empty_confirmation_cohort_is_no_decision_with_sample_estimates(
    monkeypatch,
) -> None:
    _install_rows(monkeypatch, [])

    report = prospective_report(
        object(),
        artifact=_artifact(),
        as_of=datetime.date(2026, 7, 26),
        bootstrap_b=5,
    )

    assert report["cohort"]["analyzed_races"] == 0
    assert report["promotion"]["decision"] == "NO_DECISION"
    assert report["promotion"]["panel_action"] == "REMOVE_FROM_MAIN_PANEL"
    assert "主枠から撤去" in report["promotion"]["panel_action_ja"]
    assert report["required_sample_estimates"]["s_ge_20"]["years"] == [0.5, 0.8]
    assert report["required_sample_estimates"]["s_ge_30"]["years"] == [6.9, 11.5]
    assert report["capture_coverage"] == _coverage()
    assert report["excluded_race_characteristics"] == {
        "n_races": 0
    }


def test_only_primary_s_ge_20_controls_promotion(monkeypatch) -> None:
    start = datetime.date(2024, 1, 1)
    rows = [
        _race(
            f"2024{index:08d}",
            start + datetime.timedelta(days=index),
            p_s_ge_20=1.0,
            p_himo_are=0.0,
            p_total_collapse=1.0,
        )
        for index in range(100)
    ]
    _install_rows(monkeypatch, rows)
    artifact = _artifact(
        calibration_tolerance={"absolute_calibration_error_max": 0.01},
        multiplicity_policy="single preregistered primary endpoint; no adjustment",
    )

    report = prospective_report(
        object(),
        artifact=artifact,
        as_of=datetime.date(2024, 12, 31),
        bootstrap_b=5,
    )

    assert report["promotion"]["decision"] == "PROMOTE"
    assert report["promotion"]["controlling_event"] == "s_ge_20"
    assert report["promotion"]["secondary_event"] == "himo_are"
    assert report["promotion"]["not_eligible"] == ["total_collapse"]
    assert report["promotion"]["diagnostic_only"] == ["s_ge_30"]
    assert report["promotion"]["auc_used_for_decision"] is False
    assert report["overall"]["events"]["himo_are"]["realized_rate"] == pytest.approx(1.0)
    assert report["overall"]["events"]["himo_are"]["predicted_rate"] == pytest.approx(0.0)


def test_past_final_decision_date_is_no_decision_and_removes_panel(
    monkeypatch,
) -> None:
    start = datetime.date(2024, 1, 1)
    rows = [
        _race(
            f"2024{index:08d}",
            start + datetime.timedelta(days=index),
            p_s_ge_20=1.0,
        )
        for index in range(100)
    ]
    _install_rows(monkeypatch, rows)

    report = prospective_report(
        object(),
        artifact=_artifact(
            calibration_tolerance={"absolute_calibration_error_max": 0.01},
            multiplicity_policy="single primary endpoint",
        ),
        as_of=datetime.date(2028, 1, 1),
        bootstrap_b=5,
    )

    assert report["promotion"]["decision"] == "NO_DECISION"
    assert report["promotion"]["past_final_decision_date"] is True
    assert report["promotion"]["panel_action"] == "REMOVE_FROM_MAIN_PANEL"


def test_cli_registers_chaos_bands_prospective_report(monkeypatch) -> None:
    captured = {}

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(training_cli, "create_db_engine", lambda url: f"engine:{url}")
    monkeypatch.setattr(training_cli, "Session", lambda _engine: _SessionContext())

    def _fake_report(_session, args):
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(
        training_cli,
        "_chaos_bands_prospective_report",
        _fake_report,
    )
    result = training_cli.main(
        [
            "chaos-bands",
            "prospective-report",
            "--artifact",
            "f" * 64,
        ]
    )

    assert result == 0
    assert captured["chaos_bands_command"] == "prospective-report"
    assert captured["artifact"] == "f" * 64
