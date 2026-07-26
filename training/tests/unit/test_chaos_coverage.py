from __future__ import annotations

import datetime

import pytest

from horseracing_training import chaos_bands
from horseracing_training import cli as training_cli
from horseracing_training.chaos_bands import ChaosCoverageRace, coverage_report


def _race(
    race_id: str,
    race_date: datetime.date,
    *,
    field_size: int,
    post_time_known: bool,
    capture_strength: str | None = None,
    seconds_to_post: int | None = None,
) -> ChaosCoverageRace:
    return ChaosCoverageRace(
        race_id=race_id,
        race_date=race_date,
        field_size=field_size,
        venue_code="05" if race_date.year == 2026 else "06",
        track_type="turf" if field_size % 2 == 0 else "dirt",
        grade="G3" if field_size >= 16 else None,
        race_class="open" if field_size >= 16 else "maiden",
        distance=1600 if field_size % 2 == 0 else 1800,
        post_time_known=post_time_known,
        active_snapshot_count=1 if capture_strength is not None else 0,
        capture_strength=capture_strength,
        seconds_to_post=seconds_to_post,
    )


def test_coverage_reports_capture_freshness_post_time_and_selection_bias(
    monkeypatch,
) -> None:
    rows = [
        _race(
            "202406010101",
            datetime.date(2024, 1, 6),
            field_size=8,
            post_time_known=False,
        ),
        _race(
            "202506010101",
            datetime.date(2025, 1, 5),
            field_size=10,
            post_time_known=False,
            capture_strength="weak",
        ),
        _race(
            "202506010102",
            datetime.date(2025, 1, 5),
            field_size=12,
            post_time_known=True,
        ),
        _race(
            "202606010101",
            datetime.date(2026, 1, 4),
            field_size=16,
            post_time_known=True,
            capture_strength="confirmatory",
            seconds_to_post=1800,
        ),
        _race(
            "202606010102",
            datetime.date(2026, 1, 4),
            field_size=18,
            post_time_known=True,
            capture_strength="confirmatory",
            seconds_to_post=3600,
        ),
    ]
    monkeypatch.setattr(
        chaos_bands,
        "load_coverage_rows",
        lambda *_args, **_kwargs: rows,
    )

    report = coverage_report(
        object(),
        report_from=datetime.date(2024, 1, 1),
        report_to=datetime.date(2026, 12, 31),
    )

    assert report["capture_rate"] == {
        "numerator": 3,
        "denominator": 5,
        "rate": pytest.approx(0.6),
    }
    assert report["capture_strength"]["counts"]["confirmatory"] == 2
    assert report["capture_strength"]["counts"]["weak"] == 1
    assert report["seconds_to_post"]["n_numeric"] == 2
    assert report["seconds_to_post"]["n_missing"] == 1
    assert report["seconds_to_post"]["median"] == pytest.approx(2700.0)

    post_time = report["post_time_coverage"]
    assert post_time["rate"] == pytest.approx(0.6)
    assert post_time["by_year"]["2024"]["rate"] == pytest.approx(0.0)
    assert post_time["by_year"]["2025"]["rate"] == pytest.approx(0.5)
    assert post_time["by_year"]["2026"]["rate"] == pytest.approx(1.0)
    assert post_time["historical_reference"]["2024"] == pytest.approx(0.0)
    assert post_time["historical_reference"]["2025"] == pytest.approx(0.229)
    assert post_time["historical_reference"]["2026"] == pytest.approx(1.0)
    assert "netkeiba" in post_time["interpretation"]
    assert "not a capture bug" in post_time["interpretation"]

    not_captured = report["not_captured_characteristics"]
    assert not_captured["n_races"] == 2
    assert not_captured["mean_field_size"] == pytest.approx(10.0)
    assert not_captured["post_time_known_rate"] == pytest.approx(0.5)
    assert not_captured["field_size_buckets"]["counts"] == {
        "4-8": 1,
        "12-13": 1,
    }


def test_coverage_empty_window_is_typed_zero_not_an_error(monkeypatch) -> None:
    monkeypatch.setattr(
        chaos_bands,
        "load_coverage_rows",
        lambda *_args, **_kwargs: [],
    )

    report = coverage_report(
        object(),
        report_from=datetime.date(2026, 7, 26),
        report_to=datetime.date(2026, 7, 26),
    )

    assert report["population"]["scheduled_races"] == 0
    assert report["capture_rate"]["rate"] is None
    assert report["post_time_coverage"]["rate"] is None
    assert report["not_captured_characteristics"]["n_races"] == 0


def test_cli_registers_chaos_bands_coverage(monkeypatch) -> None:
    captured = {}

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(training_cli, "create_db_engine", lambda url: f"engine:{url}")
    monkeypatch.setattr(training_cli, "Session", lambda _engine: _SessionContext())

    def _fake_coverage(_session, args):
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(training_cli, "_chaos_bands_coverage", _fake_coverage)
    result = training_cli.main(
        [
            "chaos-bands",
            "coverage",
            "--from",
            "2024-01-01",
            "--to",
            "2026-12-31",
        ]
    )

    assert result == 0
    assert captured["chaos_bands_command"] == "coverage"
    assert captured["coverage_from"] == datetime.date(2024, 1, 1)
    assert captured["coverage_to"] == datetime.date(2026, 12, 31)
