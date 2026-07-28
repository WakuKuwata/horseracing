from __future__ import annotations

import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from horseracing_training import chaos_bands
from horseracing_training import cli as training_cli
from horseracing_training.chaos_bands import (
    ChaosFitHorse,
    ChaosFitRace,
    diagnose,
)


def _artifact() -> SimpleNamespace:
    return SimpleNamespace(
        version="chaosbands-v1",
        artifact_digest="f" * 64,
        fit_to="2023-12-31",
        lambda2=0.830369,
        lambda3=0.711106,
        quintile_edges=(0.019571, 0.065928, 0.111811, 0.170308),
    )


def _races() -> list[ChaosFitRace]:
    races = []
    for race_index in range(16):
        field_size = 8 + race_index % 9
        horses = tuple(
            ChaosFitHorse(
                horse_id=f"r{race_index:02d}h{horse_index:02d}",
                odds=1.5 + horse_index * (0.8 + race_index * 0.03),
                popularity=horse_index + 1,
            )
            for horse_index in range(field_size)
        )
        if race_index % 5 == 0 and field_size >= 11:
            finish_indices = (0, 9, 10)  # S>=20 and himo_are, not total_collapse
        elif race_index % 7 == 0 and field_size >= 12:
            finish_indices = (9, 0, 1)  # total_collapse
        else:
            finish_indices = (0, 1, 2)
        races.append(
            ChaosFitRace(
                race_id=f"2024{race_index:08d}",
                race_date=datetime.date(2024, 1, 1)
                + datetime.timedelta(days=race_index // 2),
                horses=horses,
                first=(horses[finish_indices[0]].horse_id,),
                second=(horses[finish_indices[1]].horse_id,),
                third=(horses[finish_indices[2]].horse_id,),
            )
        )
    return races


def test_diagnose_is_secondary_uses_proper_scores_and_fair_baselines(monkeypatch) -> None:
    monkeypatch.setattr(chaos_bands, "load_fit_races", lambda *_args, **_kwargs: _races())

    report = diagnose(
        object(),
        diagnose_from=datetime.date(2024, 1, 1),
        diagnose_to=datetime.date(2024, 12, 31),
        artifact=_artifact(),
        bootstrap_b=20,
    )

    assert report["header"]["role"] == "SECONDARY — not an adoption gate"
    assert report["header"]["data_status"] == "2024+ is discovery data"
    assert report["header"]["can_adopt"] is False
    assert report["header"]["can_recut_band_edges"] is False
    assert report["bootstrap"]["method"] == "race_day_cluster_bootstrap_ci_v1"
    assert report["bootstrap"]["seed"] == chaos_bands.DIAGNOSTIC_BOOTSTRAP_SEED
    assert "multiple comparisons not adjusted" in report["bootstrap"]["ci_note"]

    s20 = report["overall"]["events"]["s_ge_20"]
    assert s20["reliability"]["bins"]
    assert s20["brier"]["proper_score"] is True
    assert s20["log_score"]["proper_score"] is True
    assert s20["auc"]["role"] == "AUXILIARY_ONLY"
    assert s20["decision_status"] == "NO_DECISION"

    baselines = report["baselines"]
    assert baselines["n_only_definition"] == "logistic(N)"
    assert baselines["g_h_n_definition"] == "logistic(normalized_entropy(q), N)"
    assert set(baselines["events"]["s_ge_20"]) == {
        "n_only",
        "g_h_n",
        "paired_deltas",
    }
    assert report["overall"]["events"]["s_ge_30"]["decision_status"] == "NO_DECISION"

    bucket = report["within_field_size_buckets"][0]["events"]["s_ge_20"]
    assert set(bucket["proper_scores"]) == {
        "chaos_probability",
        "n_only",
        "g_h_n",
    }
    assert "expected_s" in bucket["auxiliary_auc_scores"]
    paired_ci = bucket["paired_deltas"]["chaos_minus_g_h_n"]["brier_delta"]
    assert paired_ci["cluster"] == "race_day"


def test_diagnose_asserts_oos_start_before_reading_data(monkeypatch) -> None:
    def _unexpected_load(*_args, **_kwargs):
        raise AssertionError("data loader must not run")

    monkeypatch.setattr(chaos_bands, "load_fit_races", _unexpected_load)

    with pytest.raises(AssertionError, match="strictly after"):
        diagnose(
            object(),
            diagnose_from=datetime.date(2023, 12, 31),
            diagnose_to=datetime.date(2024, 12, 31),
            artifact=_artifact(),
            bootstrap_b=5,
        )


def test_export_fixture_contains_only_frozen_market_vectors_and_top3_ids(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(chaos_bands, "load_fit_races", lambda *_args, **_kwargs: _races())

    def _fake_to_parquet(frame, path, *, index):
        assert index is False
        Path(path).write_bytes(
            ",".join(frame.columns).encode("utf-8") + b"\nfixture"
        )

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _fake_to_parquet)
    fixture_path = tmp_path / "chaos_2024.parquet"
    report = diagnose(
        object(),
        diagnose_from=datetime.date(2024, 1, 1),
        diagnose_to=datetime.date(2024, 12, 31),
        artifact=_artifact(),
        bootstrap_b=5,
        export_fixture=fixture_path,
    )

    fixture = report["fixture_export"]
    assert fixture["n_races"] == len(_races())
    assert fixture["columns"] == [
        "race_id",
        "race_date",
        "horse_ids",
        "popularity",
        "odds",
        "first_horse_id",
        "second_horse_id",
        "third_horse_id",
    ]
    assert fixture_path.is_file()
    digest_path = Path(f"{fixture_path}.sha256")
    assert digest_path.is_file()
    assert fixture["sha256"] in digest_path.read_text(encoding="utf-8")


def test_cli_registers_chaos_bands_diagnose(monkeypatch, tmp_path) -> None:
    captured = {}

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(training_cli, "create_db_engine", lambda url: f"engine:{url}")
    monkeypatch.setattr(training_cli, "Session", lambda _engine: _SessionContext())

    def _fake_diagnose(_session, args):
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(training_cli, "_chaos_bands_diagnose", _fake_diagnose)
    result = training_cli.main(
        [
            "chaos-bands",
            "diagnose",
            "--from",
            "2024-01-01",
            "--to",
            "2026-12-31",
            "--artifact",
            "f" * 64,
            "--export-fixture",
            str(tmp_path / "fixture.parquet"),
            "--persist",
        ]
    )

    assert result == 0
    assert captured["chaos_bands_command"] == "diagnose"
    assert captured["diagnose_from"] == datetime.date(2024, 1, 1)
    assert captured["diagnose_to"] == datetime.date(2026, 12, 31)
    assert captured["artifact"] == "f" * 64
    assert captured["persist"] is True


def test_cli_persist_transcribes_completed_report(monkeypatch) -> None:
    from horseracing_eval import diagnostics_store

    artifact = _artifact()
    report = {"header": {"role": "SECONDARY — not an adoption gate"}}
    captured = {}

    class _Session:
        committed = False

        def commit(self):
            self.committed = True

    session = _Session()
    monkeypatch.setattr(
        training_cli,
        "_load_chaos_diagnostic_artifact",
        lambda *_args, **_kwargs: artifact,
    )
    monkeypatch.setattr(chaos_bands, "diagnose", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(training_cli, "_print_chaos_diagnosis", lambda value: None)

    def _save(_session, payload, **kwargs):
        captured["payload"] = payload
        captured.update(kwargs)
        return SimpleNamespace(diagnostic_run_id="run-084")

    monkeypatch.setattr(diagnostics_store, "save_chaos_bands_run", _save)
    args = SimpleNamespace(
        artifact="f" * 64,
        diagnose_from=datetime.date(2024, 1, 1),
        diagnose_to=datetime.date(2026, 12, 31),
        bootstrap_b=10,
        export_fixture=None,
        persist=True,
    )

    assert training_cli._chaos_bands_diagnose(session, args) == 0
    assert captured["payload"] is report
    assert captured["date_from"] == args.diagnose_from
    assert captured["date_to"] == args.diagnose_to
    assert captured["logic_version"] == artifact.version
    assert session.committed is True
