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
    ChaosFitHorse,
    ChaosFitRace,
    InvalidValidityWindowError,
    NumericStabilityError,
    OperationalLambdaError,
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
        "lambda2": 0.8312,
        "lambda3": 0.7101,
        "quintile_edges": [0.02, 0.06, 0.11, 0.17],
        "fit_through": "2023-12-31",
        "valid_from": "2024-01-01",
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
        ]
    )

    assert result == 0
    assert captured["chaos_bands_command"] == "fit"
    assert captured["fit_from"] == datetime.date(2020, 1, 1)
    assert captured["fit_to"] == datetime.date(2023, 12, 31)
    assert captured["valid_from"] == datetime.date(2024, 1, 1)
    assert captured["out_dir"] == str(tmp_path)


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
    )

    assert training_cli._chaos_bands_fit(object(), args) == 0
    output = capsys.readouterr().out
    assert "partial_market_odds=3" in output
    assert "artifact_digest=" in output
    assert "承認 manifest にこの digest を追記してください" in output
