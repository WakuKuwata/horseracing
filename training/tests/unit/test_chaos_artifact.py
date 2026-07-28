from __future__ import annotations

import datetime
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from horseracing_eval.dispersion_bands import fit_quintile_edges
from horseracing_eval.stage_discount import StageDiscount
from horseracing_probability.chaos_artifact import load_chaos_artifact
from horseracing_probability.chaos_distribution import chaos_readout
from horseracing_probability.chaos_events import CHAOS_EVENTS_V1
from horseracing_probability.market_odds import market_implied_win_probs

from horseracing_training import chaos_bands
from horseracing_training import cli as training_cli
from horseracing_training.chaos_bands import (
    ArtifactAlreadyExistsError,
    ArtifactDigestError,
    ArtifactKeyDiff,
    ChaosFitHorse,
    ChaosFitRace,
    HorizonUpgradeResult,
    InvalidValidityWindowError,
    NumericStabilityError,
    OperationalLambdaError,
    add_horizon_artifact,
    build_field_size_reference_quantiles,
    compute_artifact_digest,
    eligibility_exclusion_reason,
    fit_artifact,
    publish_artifact,
    within_field_size_percentile,
)


def _publishable_payload() -> dict:
    return {
        "version": "chaosbands-v1",
        "label_definition": "top3_popularity_composition_proxy_v1",
        "lambda2": 0.8312,
        "lambda3": 0.7101,
        "lambda_fit_objective": {
            "lambda2": "conditional_nll_stage2",
            "lambda3": "conditional_nll_stage3",
        },
        "band_axis": "p_s_ge_20",
        "quintile_edges": [0.02, 0.06, 0.11, 0.17],
        "edges_basis": "closing_history",
        "s_threshold_basis": "fit_window_p90",
        "fit_from": "2020-01-01",
        "fit_to": "2023-12-31",
        "as_of": "2023-12-31",
        "fit_through": "2023-12-31",
        "valid_from": "2024-01-01",
        "n_races_fit": 13747,
        "operational_lambda_envelope": {
            "lambda2": {"min": 0.1, "max": 1.0},
            "lambda3": {"min": 0.1, "max": 1.0},
        },
        "numeric_stability_report": {
            "green": True,
            "status": "green",
            "representative_fields": [{"status": "pass"}],
            "degenerate_fields": [{"status": "pass"}],
        },
        "preregistration": {
            "promotion_rule": {"controlling_event": "s_ge_20"},
            "minimum_positives": 100,
            "minimum_race_days": 60,
        },
        "race_set_hash": "race-set",
        "fit_input_hash": "fit-input",
        "eligibility_predicate": {
            "complete_odds": True,
            "unique_popularity": True,
            "minimum_field_size": 4,
        },
        "field_size_reference_quantiles": {
            "18": [0.05, 0.10, 0.15, 0.20],
        },
        "code_sha": "abc123",
        "calibration_status": "provisional",
    }


def _fit_races() -> list[ChaosFitRace]:
    races: list[ChaosFitRace] = []
    for race_index in range(10):
        power = 0.35 + race_index * 0.18
        horses = tuple(
            ChaosFitHorse(
                horse_id=f"r{race_index}-h{horse_index}",
                odds=float((horse_index + 1) ** power),
                popularity=horse_index + 1,
            )
            for horse_index in range(10)
        )
        races.append(
            ChaosFitRace(
                race_id=f"2020{race_index:08d}",
                race_date=datetime.date(2020, 1, race_index + 1),
                horses=horses,
                first=(horses[race_index % 3].horse_id,),
                second=(horses[(race_index + 2) % 5].horse_id,),
                third=(horses[(race_index + 4) % 7].horse_id,),
            )
        )
    return races


def test_digest_covers_nested_payload_and_excludes_only_self_reference() -> None:
    payload = _publishable_payload()
    digest = compute_artifact_digest(payload)
    with_self_reference = {**payload, "artifact_digest": digest}

    assert compute_artifact_digest(with_self_reference) == digest

    changed = deepcopy(with_self_reference)
    changed["preregistration"]["minimum_positives"] = 101
    assert compute_artifact_digest(changed) != digest


def test_publish_uses_exclusive_create_and_never_overwrites(tmp_path) -> None:
    payload = _publishable_payload()
    published = publish_artifact(payload, out_dir=tmp_path)
    original_bytes = published.path.read_bytes()

    with pytest.raises(ArtifactAlreadyExistsError):
        publish_artifact(payload, out_dir=tmp_path)

    assert published.path.read_bytes() == original_bytes
    on_disk = json.loads(original_bytes)
    assert on_disk["artifact_digest"] == published.artifact_digest
    assert published.path.name == f"{published.artifact_digest}.json"


def test_publish_rejects_a_stale_supplied_digest(tmp_path) -> None:
    payload = {**_publishable_payload(), "artifact_digest": "0" * 64}

    with pytest.raises(ArtifactDigestError):
        publish_artifact(payload, out_dir=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_add_horizon_is_create_only_and_changes_exactly_two_keys(
    monkeypatch,
    tmp_path,
) -> None:
    legacy = _publishable_payload()
    legacy["artifact_digest"] = compute_artifact_digest(legacy)
    legacy_before = deepcopy(legacy)
    source_path = tmp_path / f"{legacy['artifact_digest']}.json"
    source_bytes = json.dumps(legacy, sort_keys=True).encode()
    source_path.write_bytes(source_bytes)
    manifest_path = tmp_path / "approved.json"
    manifest_path.write_text(
        json.dumps(
            {
                "approved": [
                    {
                        "digest": legacy["artifact_digest"],
                        "status": "superseded",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHAOS_BANDS_APPROVED_MANIFEST", str(manifest_path))
    result = add_horizon_artifact(
        source_path=source_path,
        expected_digest=legacy["artifact_digest"],
        minimum_seconds_to_post=600,
        maximum_seconds_to_post=86400,
        basis="schedule_jitter_floor_and_next_day_market_ceiling",
        out_dir=tmp_path,
    )

    assert source_path.read_bytes() == source_bytes
    assert legacy == legacy_before
    assert result.path != source_path
    assert result.path.name == f"{result.artifact_digest}.json"
    assert result.payload["version"] == "chaosbands-v1"
    assert result.payload["preregistration"]["primary_horizon"] == {
        "minimum_seconds_to_post": 600,
        "maximum_seconds_to_post": 86400,
        "basis": "schedule_jitter_floor_and_next_day_market_ceiling",
        "measured_coverage_of_pre_race_predict_clicks": 0.956,
    }
    assert result.artifact_digest == compute_artifact_digest(result.payload)
    assert result.artifact_digest != legacy["artifact_digest"]
    assert [
        entry.path for entry in result.key_diff if entry.status != "unchanged"
    ] == [
        "artifact_digest",
        "preregistration.primary_horizon",
    ]

    unchanged_new = deepcopy(result.payload)
    unchanged_new["preregistration"].pop("primary_horizon")
    unchanged_new["artifact_digest"] = legacy["artifact_digest"]
    assert unchanged_new == legacy
    assert json.loads(result.path.read_bytes()) == result.payload
    loaded = load_chaos_artifact(
        result.path,
        approved_digests={result.artifact_digest},
        target_date=datetime.date(2026, 1, 1),
    )
    assert loaded.artifact_digest == result.artifact_digest
    assert loaded.version == "chaosbands-v1"

    with pytest.raises(ArtifactAlreadyExistsError):
        add_horizon_artifact(
            source_path=source_path,
            expected_digest=legacy["artifact_digest"],
            minimum_seconds_to_post=600,
            maximum_seconds_to_post=86400,
            basis="schedule_jitter_floor_and_next_day_market_ceiling",
            out_dir=tmp_path,
        )


def test_add_horizon_omits_unmeasured_coverage_for_other_windows(
    monkeypatch,
    tmp_path,
) -> None:
    legacy = _publishable_payload()
    legacy["artifact_digest"] = compute_artifact_digest(legacy)

    def _upgrade(
        _path,
        *,
        expected_digest,
        minimum_seconds_to_post,
        maximum_seconds_to_post,
    ):
        assert expected_digest == legacy["artifact_digest"]
        upgraded = deepcopy(legacy)
        upgraded["preregistration"]["primary_horizon"] = {
            "minimum_seconds_to_post": minimum_seconds_to_post,
            "maximum_seconds_to_post": maximum_seconds_to_post,
        }
        digest = compute_artifact_digest(upgraded)
        upgraded["artifact_digest"] = digest
        return upgraded, digest

    monkeypatch.setattr(chaos_bands, "upgrade_legacy_artifact_horizon", _upgrade)
    result = add_horizon_artifact(
        source_path=tmp_path / "source.json",
        expected_digest=legacy["artifact_digest"],
        minimum_seconds_to_post=1800,
        maximum_seconds_to_post=20000,
        basis="alternate_window",
        out_dir=tmp_path,
    )

    assert result.payload["preregistration"]["primary_horizon"] == {
        "minimum_seconds_to_post": 1800,
        "maximum_seconds_to_post": 20000,
        "basis": "alternate_window",
    }


@pytest.mark.parametrize(
    ("minimum", "maximum", "coverage"),
    [
        (1800, 20000, 0.956),
        (600, 86400, 0.955),
        (600, 86400, float("nan")),
    ],
)
def test_add_horizon_rejects_false_measured_coverage_claims(
    monkeypatch,
    tmp_path,
    minimum,
    maximum,
    coverage,
) -> None:
    monkeypatch.setattr(
        chaos_bands,
        "upgrade_legacy_artifact_horizon",
        lambda *_args, **_kwargs: pytest.fail("invalid metadata must fail before reading"),
    )

    with pytest.raises(chaos_bands.ChaosArtifactError):
        add_horizon_artifact(
            source_path=tmp_path / "source.json",
            expected_digest="a" * 64,
            minimum_seconds_to_post=minimum,
            maximum_seconds_to_post=maximum,
            basis="test",
            measured_coverage=coverage,
            out_dir=tmp_path,
        )


@pytest.mark.parametrize("valid_from", ["2023-12-31", "2023-12-30"])
def test_valid_from_must_be_strictly_after_fit_through(tmp_path, valid_from) -> None:
    payload = {**_publishable_payload(), "valid_from": valid_from}

    with pytest.raises(InvalidValidityWindowError):
        publish_artifact(payload, out_dir=tmp_path)


def test_numeric_stability_report_must_be_green(tmp_path) -> None:
    payload = _publishable_payload()
    payload["numeric_stability_report"] = {"green": False, "status": "red"}

    with pytest.raises(NumericStabilityError):
        publish_artifact(payload, out_dir=tmp_path)


def test_operational_lambda_envelope_is_a_publish_gate(tmp_path) -> None:
    payload = _publishable_payload()
    payload["lambda2"] = 1.01

    with pytest.raises(OperationalLambdaError):
        publish_artifact(payload, out_dir=tmp_path)


def test_eligibility_accepts_popularity_gaps_but_rejects_shared_failures() -> None:
    base = ChaosFitRace(
        race_id="race",
        race_date=datetime.date(2020, 1, 1),
        horses=(
            ChaosFitHorse("a", 2.0, 1),
            ChaosFitHorse("b", 3.0, 2),
            ChaosFitHorse("c", 4.0, 4),
            ChaosFitHorse("d", 5.0, 5),
        ),
    )
    assert eligibility_exclusion_reason(base) is None

    duplicate = ChaosFitRace(
        race_id=base.race_id,
        race_date=base.race_date,
        horses=base.horses[:-1] + (ChaosFitHorse("d", 5.0, 4),),
    )
    partial_odds = ChaosFitRace(
        race_id=base.race_id,
        race_date=base.race_date,
        horses=base.horses[:-1] + (ChaosFitHorse("d", None, 5),),
    )
    assert eligibility_exclusion_reason(duplicate) == "invalid_popularity_ranks"
    assert eligibility_exclusion_reason(partial_odds) == "partial_market_odds"


def test_field_size_reference_quantiles_drive_percentile() -> None:
    references = build_field_size_reference_quantiles({8: [0.0, 0.1, 0.2, 0.3, 0.4]})

    assert references["8"]["n_races"] == 5
    assert within_field_size_percentile(0.2, 8, references) == pytest.approx(50.0)
    assert within_field_size_percentile(0.2, 9, references) is None


def test_fit_artifact_uses_p_s_ge_20_for_edges(monkeypatch, tmp_path) -> None:
    races = _fit_races()
    monkeypatch.setattr(chaos_bands, "load_fit_races", lambda *_args, **_kwargs: races)
    monkeypatch.setattr(
        chaos_bands,
        "fit_chaos_lambda",
        lambda *_args, **_kwargs: StageDiscount(
            lambda2=0.8312,
            lambda3=0.7101,
            n_races_l2=len(races),
            n_races_l3=len(races),
        ),
    )

    published = fit_artifact(
        object(),
        fit_from=datetime.date(2020, 1, 1),
        fit_to=datetime.date(2023, 12, 31),
        valid_from=datetime.date(2024, 1, 1),
        out_dir=tmp_path,
        code_sha="test-sha",
        min_races=5,
        primary_horizon={
            "minimum_seconds_to_post": 600,
            "maximum_seconds_to_post": 86400,
            "basis": "test",
        },
    )
    payload = published.payload
    discount = StageDiscount(lambda2=payload["lambda2"], lambda3=payload["lambda3"])
    expected_probabilities = []
    expected_s = []
    for race in races:
        odds = {horse.horse_id: horse.odds for horse in race.horses}
        q = market_implied_win_probs(odds)
        ranks = {horse.horse_id: horse.popularity for horse in race.horses}
        _raw, adjusted, _band = chaos_readout(
            q,
            ranks,
            CHAOS_EVENTS_V1,
            stage_discount=discount,
            edges=(0.2, 0.4, 0.6, 0.8),
        )
        expected_probabilities.append(adjusted.event_mass["s_ge_20"])
        expected_s.append(adjusted.expected_s)

    assert payload["band_axis"] == "p_s_ge_20"
    assert payload["quintile_edges"] == pytest.approx(
        fit_quintile_edges(expected_probabilities)
    )
    assert payload["quintile_edges"] != pytest.approx(fit_quintile_edges(expected_s))
    assert payload["eligibility_predicate"] == chaos_bands.ELIGIBILITY_PREDICATE
    assert payload["numeric_stability_report"]["green"] is True
    assert payload["s_threshold_basis"] == "fit_window_p90"
    assert payload["edges_basis"] == "closing_history"
    assert payload["calibration_status"] == "provisional"
    assert payload["code_sha"] == "test-sha"
    assert payload["n_races_fit"] == len(races)

    loaded = load_chaos_artifact(
        published.path,
        approved_digests={published.artifact_digest},
        target_date=datetime.date(2026, 1, 1),
    )
    assert loaded.artifact_digest == published.artifact_digest


def test_cli_registers_chaos_bands_fit_group(monkeypatch, tmp_path) -> None:
    captured = {}

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(training_cli, "create_db_engine", lambda url: f"engine:{url}")
    monkeypatch.setattr(training_cli, "Session", lambda _engine: _SessionContext())

    def _fake_fit(_session, args):
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(training_cli, "_chaos_bands_fit", _fake_fit)
    result = training_cli.main(
        [
            "chaos-bands",
            "fit",
            "--fit-from",
            "2020-01-01",
            "--fit-to",
            "2023-12-31",
            "--valid-from",
            "2024-01-01",
            "--out-dir",
            str(tmp_path),
            "--primary-horizon-min-seconds-to-post",
            "600",
            "--primary-horizon-max-seconds-to-post",
            "86400",
            "--primary-horizon-basis",
            "test",
        ]
    )

    assert result == 0
    assert captured["chaos_bands_command"] == "fit"
    # The window is mandatory at fit time (FR-005/FR-006): an artifact fitted without one is
    # rejected by the loader, so it would be unreadable from birth.
    assert captured["primary_horizon_min_seconds_to_post"] == 600
    assert captured["primary_horizon_max_seconds_to_post"] == 86400
    assert captured["fit_from"] == datetime.date(2020, 1, 1)
    assert captured["fit_to"] == datetime.date(2023, 12, 31)
    assert captured["valid_from"] == datetime.date(2024, 1, 1)
    assert captured["out_dir"] == str(tmp_path)


def test_cli_registers_add_horizon_without_opening_a_database(monkeypatch) -> None:
    captured = {}

    def _fake_add_horizon(args):
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(training_cli, "_chaos_bands_add_horizon", _fake_add_horizon)
    monkeypatch.setattr(
        training_cli,
        "create_db_engine",
        lambda _url: pytest.fail("add-horizon must not open a database"),
    )
    result = training_cli.main(
        [
            "chaos-bands",
            "add-horizon",
            "--artifact",
            "a" * 64,
            "--primary-horizon-min-seconds-to-post",
            "600",
            "--primary-horizon-max-seconds-to-post",
            "86400",
            "--primary-horizon-basis",
            "schedule_jitter_floor_and_next_day_market_ceiling",
            "--primary-horizon-measured-coverage",
            "0.956",
        ]
    )

    assert result == 0
    assert captured == {
        "command": "chaos-bands",
        "chaos_bands_command": "add-horizon",
        "artifact": "a" * 64,
        "primary_horizon_min_seconds_to_post": 600,
        "primary_horizon_max_seconds_to_post": 86400,
        "primary_horizon_basis": (
            "schedule_jitter_floor_and_next_day_market_ceiling"
        ),
        "primary_horizon_measured_coverage": 0.956,
    }


def test_add_horizon_cli_prints_full_two_point_diff(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    result = HorizonUpgradeResult(
        payload={"artifact_digest": "b" * 64},
        source_path=tmp_path / f"{'a' * 64}.json",
        path=tmp_path / f"{'b' * 64}.json",
        key_diff=(
            ArtifactKeyDiff(
                path="artifact_digest",
                status="changed",
                before="a" * 64,
                after="b" * 64,
            ),
            ArtifactKeyDiff(
                path="lambda2",
                status="unchanged",
                before=0.8312,
                after=0.8312,
            ),
            ArtifactKeyDiff(
                path="preregistration.primary_horizon",
                status="added",
                after={
                    "minimum_seconds_to_post": 600,
                    "maximum_seconds_to_post": 86400,
                    "basis": "test",
                    "measured_coverage_of_pre_race_predict_clicks": 0.956,
                },
            ),
        ),
    )
    monkeypatch.setattr(
        chaos_bands,
        "add_horizon_artifact",
        lambda **_kwargs: result,
    )
    args = SimpleNamespace(
        artifact="a" * 64,
        primary_horizon_min_seconds_to_post=600,
        primary_horizon_max_seconds_to_post=86400,
        primary_horizon_basis="test",
        primary_horizon_measured_coverage=None,
    )

    assert training_cli._chaos_bands_add_horizon(args) == 0
    output = capsys.readouterr().out
    assert "chaos-bands add-horizon: CREATE-ONLY" in output
    assert "CHANGED   artifact_digest" in output
    assert "UNCHANGED lambda2 = 0.8312" in output
    assert "ADDED     preregistration.primary_horizon" in output
    assert "differences=2" in output
    assert (
        "only_differences="
        "artifact_digest,preregistration.primary_horizon"
    ) in output
    assert "source_modified=false" in output
    assert "approval_manifest_modified=false" in output


def test_cli_prints_exclusions_and_manifest_instruction(monkeypatch, capsys, tmp_path) -> None:
    payload = {
        "n_races_fit": 10,
        "fit_from": "2020-01-01",
        "fit_to": "2023-12-31",
        "lambda2": 0.8312,
        "lambda3": 0.7101,
        "quintile_edges": [0.02, 0.06, 0.11, 0.17],
        "numeric_stability_report": {"status": "green"},
    }
    published = SimpleNamespace(
        payload=payload,
        excluded_race_counts={"partial_market_odds": 3},
        artifact_digest="a" * 64,
        path=tmp_path / f"{'a' * 64}.json",
    )
    monkeypatch.setattr(chaos_bands, "fit_artifact", lambda *_args, **_kwargs: published)
    monkeypatch.setattr(training_cli, "_git_sha", lambda: "test-sha")
    args = SimpleNamespace(
        fit_from=datetime.date(2020, 1, 1),
        fit_to=datetime.date(2023, 12, 31),
        valid_from=datetime.date(2024, 1, 1),
        out_dir=str(tmp_path),
        primary_horizon_min_seconds_to_post=600,
        primary_horizon_max_seconds_to_post=86400,
        primary_horizon_basis="test",
    )

    assert training_cli._chaos_bands_fit(object(), args) == 0
    output = capsys.readouterr().out
    assert "partial_market_odds=3" in output
    assert "artifact_digest=" in output
    assert "承認 manifest にこの digest を追記してください" in output


def test_preregistration_includes_calibration_tolerance_and_multiplicity_policy(
    monkeypatch, tmp_path
) -> None:
    """chaosbands-v1 shipped WITHOUT these two, so promotion could never occur — the
    prospective report itself reported them as missing on the real artifact. Pin them
    against the payload `fit_artifact` actually produces (a hand-built fixture would not
    have caught the original gap)."""

    from horseracing_training.chaos_bands import _calibration_tolerance

    races = _fit_races()
    monkeypatch.setattr(chaos_bands, "load_fit_races", lambda *_args, **_kwargs: races)
    monkeypatch.setattr(
        chaos_bands,
        "fit_chaos_lambda",
        lambda *_args, **_kwargs: StageDiscount(
            lambda2=0.8312,
            lambda3=0.7101,
            n_races_l2=len(races),
            n_races_l3=len(races),
        ),
    )
    published = fit_artifact(
        object(),
        fit_from=datetime.date(2020, 1, 1),
        fit_to=datetime.date(2023, 12, 31),
        valid_from=datetime.date(2024, 1, 1),
        out_dir=tmp_path,
        code_sha="test-sha",
        min_races=5,
        primary_horizon={
            "minimum_seconds_to_post": 600,
            "maximum_seconds_to_post": 86400,
            "basis": "test",
        },
    )
    prereg = published.payload["preregistration"]

    tolerance, raw = _calibration_tolerance(prereg)
    assert tolerance == pytest.approx(0.02)
    assert raw == {"absolute_calibration_error_max": 0.02}
    assert (
        prereg["calibration_tolerance"]["basis"]
        == "integer_display_granularity_and_band_width"
    )

    policy = prereg["multiplicity_policy"]
    assert policy["primary"] == "s_ge_20"
    assert policy["adjustment"] == "none_required_single_inferential_test"
    # promotion_rule and multiplicity_policy must agree on which endpoint is inferential
    assert prereg["promotion_rule"]["controlling_event"] == policy["primary"]
