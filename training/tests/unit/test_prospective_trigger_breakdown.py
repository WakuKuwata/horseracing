from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest
from horseracing_probability.chaos_eligibility import capture_horizon_buckets

from horseracing_training import chaos_bands
from horseracing_training.chaos_bands import (
    FrozenChaosHorse,
    ProspectiveChaosRace,
    prospective_report,
)

_TRIGGERS = (
    "daily_operational",
    "predict_manual",
    "predict_auto",
    "explicit_command",
    "legacy_unknown",
)


def _artifact(*, minimum: int = 600, maximum: int = 86_400) -> SimpleNamespace:
    return SimpleNamespace(
        version="chaosbands-v1",
        artifact_digest="f" * 64,
        valid_from=datetime.date(2024, 1, 1),
        fit_through=datetime.date(2023, 12, 31),
        lambda2=0.8303689257547258,
        lambda3=0.7111058148742723,
        quintile_edges=(0.019571, 0.065928, 0.111811, 0.170308),
        preregistration={
            "primary_horizon": {
                "minimum_seconds_to_post": minimum,
                "maximum_seconds_to_post": maximum,
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
        },
    )


def _race(
    index: int,
    *,
    trigger: str,
    seconds_to_post: int = 1_800,
    snapshot_status: str = "active",
    snapshot_void_reason: str | None = None,
    current_started_field: frozenset[tuple[str, int]] | None = None,
) -> ProspectiveChaosRace:
    field = tuple(
        FrozenChaosHorse(
            horse_id=f"h{rank}",
            horse_number=rank,
            popularity=rank,
            odds=float(rank + 1),
        )
        for rank in range(1, 5)
    )
    return ProspectiveChaosRace(
        race_id=f"2024{index:08d}",
        race_date=datetime.date(2024, 1, 1) + datetime.timedelta(days=index),
        readout_id=f"readout-{index}",
        snapshot_id=f"snapshot-{index}",
        snapshot_status=snapshot_status,
        snapshot_void_reason=snapshot_void_reason,
        capture_strength="confirmatory",
        capture_trigger=trigger,
        seconds_to_post=seconds_to_post,
        frozen_field=field,
        current_started_field=(
            current_started_field
            if current_started_field is not None
            else frozenset(
                (horse.horse_id, horse.horse_number) for horse in field
            )
        ),
        field_size=4,
        p_s_ge_20=0.25,
        p_himo_are=0.20,
        p_total_collapse=0.05,
        first=("h1",),
        second=("h2",),
        third=("h3",),
    )


def _report(monkeypatch, rows, *, artifact=None):
    monkeypatch.setattr(
        chaos_bands,
        "load_prospective_rows",
        lambda *_args, **_kwargs: list(rows),
    )
    monkeypatch.setattr(
        chaos_bands,
        "coverage_report",
        lambda *_args, **_kwargs: {
            "not_captured_characteristics": {"n_races": 0}
        },
    )
    return prospective_report(
        object(),
        artifact=artifact or _artifact(),
        as_of=datetime.date(2024, 12, 31),
        bootstrap_b=2,
    )


def test_all_five_triggers_remain_visible_when_the_cohort_is_empty(
    monkeypatch,
) -> None:
    report = _report(monkeypatch, [])

    assert [row["trigger"] for row in report["by_capture_trigger"]] == list(
        _TRIGGERS
    )
    assert all(row["n"] == 0 for row in report["by_capture_trigger"])
    assert all(
        row["confirmation_eligible"] == 0
        for row in report["by_capture_trigger"]
    )
    assert report["user_selected_share"] is None
    assert report["prospective_selection_bias"] == {
        "policy_primary_source": "daily_operational",
        "observed_primary_source": None,
        "primary_source_claim_violated": False,
        "user_selected_role": "supplementary",
        "removable": False,
        "note": (
            "予測実行由来の観測は利用者が選んだレースに偏る。"
            "日次の中立な捕捉が主たる観測源であり、予測実行由来のみで"
            "観測群が構成された場合は選択バイアスを除去できない。"
        ),
    }


def test_zero_share_is_reported_and_legacy_unknown_is_excluded_from_denominator(
    monkeypatch,
) -> None:
    neutral = _report(
        monkeypatch,
        [_race(1, trigger="daily_operational")],
    )
    assert neutral["user_selected_share"] == 0.0

    unknown_only = _report(
        monkeypatch,
        [_race(2, trigger="legacy_unknown")],
    )
    assert unknown_only["user_selected_share"] is None
    unknown_row = next(
        row
        for row in unknown_only["by_capture_trigger"]
        if row["trigger"] == "legacy_unknown"
    )
    assert unknown_row["confirmation_eligible"] == 0


def test_manual_and_auto_predict_are_not_merged(monkeypatch) -> None:
    report = _report(
        monkeypatch,
        [
            _race(1, trigger="predict_manual"),
            _race(2, trigger="predict_auto"),
            _race(3, trigger="explicit_command"),
        ],
    )
    by_trigger = {
        row["trigger"]: row for row in report["by_capture_trigger"]
    }

    assert by_trigger["predict_manual"]["n"] == 1
    assert by_trigger["predict_auto"]["n"] == 1
    assert by_trigger["predict_manual"]["selection_biased"] is True
    assert by_trigger["predict_auto"]["selection_biased"] is False
    assert report["user_selected_share"] == pytest.approx(2 / 3)


def test_observed_primary_source_uses_lexicographic_tie_break(
    monkeypatch,
) -> None:
    report = _report(
        monkeypatch,
        [
            _race(1, trigger="predict_manual"),
            _race(2, trigger="explicit_command"),
        ],
    )

    bias = report["prospective_selection_bias"]
    assert bias["observed_primary_source"] == "explicit_command"
    assert bias["policy_primary_source"] == "daily_operational"
    assert bias["primary_source_claim_violated"] is True
    assert set(bias) == {
        "policy_primary_source",
        "observed_primary_source",
        "primary_source_claim_violated",
        "user_selected_role",
        "removable",
        "note",
    }
    assert bias["removable"] is False
    assert "除去できない" in bias["note"]


def test_horizon_buckets_follow_both_artifact_bounds(monkeypatch) -> None:
    artifact = _artifact(maximum=20_000)
    report = _report(
        monkeypatch,
        [
            _race(1, trigger="daily_operational", seconds_to_post=600),
            _race(2, trigger="daily_operational", seconds_to_post=1_800),
            _race(3, trigger="daily_operational", seconds_to_post=3_600),
            _race(4, trigger="daily_operational", seconds_to_post=10_800),
        ],
        artifact=artifact,
    )

    assert report["analysis_unit"]["primary_horizon"] == {
        "mode": "artifact_seconds_to_post_window",
        "minimum_seconds_to_post": 600,
        "maximum_seconds_to_post": 20_000,
    }
    assert "primary_horizon" not in {
        key: value
        for key, value in report.items()
        if key != "analysis_unit"
    }
    assert [
        row["horizon"] for row in report["by_capture_horizon"]
    ] == ["10-30m", "30-60m", "1-3h", "3h+"]
    buckets = capture_horizon_buckets(600, 20_000)
    assert [label for label, _lower, _upper in buckets] == [
        "10-30m",
        "30-60m",
        "1-3h",
        "3h+",
    ]
    assert buckets[-1][2] is None
    assert capture_horizon_buckets(300, 20_000)[0][0] == "5-30m"


def test_outside_window_rows_never_reach_horizon_classification(
    monkeypatch,
) -> None:
    classified: list[int] = []
    real_classifier = chaos_bands.capture_horizon_bucket

    def spy_classifier(seconds_to_post, **kwargs):
        classified.append(seconds_to_post)
        return real_classifier(seconds_to_post, **kwargs)

    monkeypatch.setattr(chaos_bands, "capture_horizon_bucket", spy_classifier)
    report = _report(
        monkeypatch,
        [
            _race(1, trigger="daily_operational", seconds_to_post=599),
            _race(2, trigger="daily_operational", seconds_to_post=600),
        ],
    )

    assert report["cohort"]["exclusions"]["outside_primary_horizon"] == 1
    assert classified == [600]


def test_current_field_mismatch_and_void_field_changed_share_one_exclusion(
    monkeypatch,
) -> None:
    report = _report(
        monkeypatch,
        [
            _race(
                1,
                trigger="daily_operational",
                current_started_field=frozenset({("h1", 1), ("h2", 2)}),
            ),
            _race(
                2,
                trigger="predict_manual",
                snapshot_status="void",
                snapshot_void_reason="field_changed",
            ),
        ],
    )

    assert report["cohort"]["analyzed_races"] == 0
    assert report["cohort"]["exclusions"] == {
        "field_changed_after_capture": 2
    }
    by_trigger = {
        row["trigger"]: row for row in report["by_capture_trigger"]
    }
    assert by_trigger["daily_operational"]["confirmation_eligible"] == 0
    assert by_trigger["predict_manual"]["confirmation_eligible"] == 0
