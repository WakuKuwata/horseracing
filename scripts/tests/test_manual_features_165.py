from __future__ import annotations

import copy
import datetime as dt
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from scripts.roi_explore.manual_observation_165 import features as f
from scripts.roi_explore.model_connection_163 import contract as c, models
from scripts.roi_explore.model_connection_163.quotes import make_local_fixture
from scripts.roi_explore.model_connection_163.run import input_rows

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def data():
    spec = importlib.util.spec_from_file_location(
        "raw_market_fixture", ROOT / "training/tests/_market_ev_synth.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = module.raw_frame()
    shifted = {
        a: b
        for a, b in zip(
            module.DATES,
            (dt.date(2026, 9, 1), dt.date(2026, 9, 8), dt.date(2026, 9, 15)),
            strict=True,
        )
    }
    ids = {
        module.race_id(old, n): module.race_id(new, n)
        for old, new in shifted.items()
        for n in (1, 2)
    }
    raw["race_date"] = raw.race_date.map(shifted)
    raw["race_id"] = raw.race_id.map(ids)
    day = shifted[module.D3]
    rid = module.race_id(day, 1)
    # No target outcome exists in a live source snapshot. Other same-day races remain in the
    # scheduled card; their outcomes must not feed today's per-entity aggregates.
    target = raw.race_id.eq(rid)
    raw.loc[target, ["finish_order", "result_status", "last_3f", "margin_sec"]] = None
    rows = [
        dict(horse_id="H" + str(i), horse_number=i, odds=str(o), popularity=i)
        for i, o in enumerate((3, 5, 7), 1)
    ]
    quote = make_local_fixture(
        race_id=rid,
        rows=rows,
        fetch_started_at=f"{day}T00:00:00Z",
        received_at=f"{day}T00:00:01Z",
    )
    old = input_rows(c.make_protocol(), quote)["legacy"]
    history = []
    for r in old:
        r["features"].update(career_starts=2, days_since_last=7, sex="牡")
        for prior in (shifted[module.D1], shifted[module.D2]):
            history.append(
                dict(
                    race_id=module.race_id(prior, 1),
                    horse_id=r["horse_id"],
                    race_date=prior,
                    entry_status="started",
                )
            )
    legacy = pd.DataFrame(
        [
            dict(race_id=rid, horse_id=r["horse_id"], race_date=day, **r["features"])
            for r in old
        ]
    )
    return legacy, raw, pd.DataFrame(history), quote, rid, day.isoformat()


def build(data):
    a, b, h, q, r, d = data
    return f.build_from_snapshot(a, b, h, q, race_id=r, race_date=d)


def test_build_consumes_explicit_quote_and_full_fixed_columns(data):
    result = build(data)
    assert len(result["rows"]["legacy"]) == len(result["rows"]["mev"]) == 3
    assert len(result["legacy_history"]) == 6
    assert all(
        set(r["features"]) == set(models.input_columns(kind))
        for kind in ("legacy", "mev")
        for r in result["rows"][kind]
    )
    assert [r["features"]["odds"] for r in result["rows"]["mev"]] == [3.0, 5.0, 7.0]
    assert all(
        r["features"][name] is None
        for r in result["rows"]["legacy"]
        for name in c.WEIGHTS
    )
    assert result["source_evidence"]["target_outcomes_absent"]
    assert (
        result["source_evidence"]["mev_raw_source_sha256"]
        != result["source_evidence"]["mev_consumed_source_sha256"]
    )


def test_build_does_not_mutate_source_inputs(data):
    originals = [
        x.copy(deep=True) if isinstance(x, pd.DataFrame) else copy.deepcopy(x)
        for x in data
    ]
    build(data)
    for old, new in zip(originals, data, strict=True):
        if isinstance(old, pd.DataFrame):
            pd.testing.assert_frame_equal(old, new)
        else:
            assert old == new


def test_same_day_outcomes_cannot_move_current_model_inputs(data):
    base = build(data)
    changed = list(copy.deepcopy(data))
    raw = changed[1]
    same = raw.race_date.eq(dt.date.fromisoformat(changed[5])) & ~raw.race_id.eq(
        changed[4]
    )
    raw.loc[same, "finish_order"] = raw.loc[same, "finish_order"].to_numpy()[::-1]
    raw.loc[same, "last_3f"] = 999
    raw.loc[same, "margin_sec"] = 999
    result = build(tuple(changed))
    assert result["rows"] == base["rows"]


@pytest.mark.parametrize(
    "column,value",
    [
        ("finish_order", 1),
        ("result_status", "finished"),
        ("last_3f", 34),
        ("margin_sec", 0),
    ],
)
def test_own_result_is_never_erased_to_create_pending(data, column, value):
    changed = list(copy.deepcopy(data))
    raw = changed[1]
    raw.loc[raw.race_id.eq(changed[4]), column] = value
    with pytest.raises(ValueError, match="target_outcome_already_present"):
        build(tuple(changed))


def test_source_later_than_target_cutoff_rejected(data):
    changed = list(copy.deepcopy(data))
    raw = changed[1]
    extra = raw.iloc[0].copy()
    extra["race_date"] = dt.date.fromisoformat(changed[5]) + dt.timedelta(days=1)
    extra["race_id"] = "202609160501"
    changed[1] = pd.concat([raw, pd.DataFrame([extra])], ignore_index=True)
    with pytest.raises(ValueError, match="outside_date_cutoff"):
        build(tuple(changed))


def test_different_field_and_duplicate_source_are_rejected(data):
    changed = list(copy.deepcopy(data))
    changed[0] = changed[0].iloc[:-1].copy()
    with pytest.raises(ValueError, match="field_mismatch"):
        build(tuple(changed))
    changed = list(copy.deepcopy(data))
    changed[1] = pd.concat([changed[1], changed[1].iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate_source_row"):
        build(tuple(changed))


def test_unknown_column_is_not_imputed(data):
    changed = list(copy.deepcopy(data))
    changed[0] = changed[0].drop(columns=["distance"])
    with pytest.raises(ValueError, match="legacy_full_matrix_missing"):
        build(tuple(changed))


def test_history_is_strict_past_and_count_must_agree(data):
    changed = list(copy.deepcopy(data))
    changed[2] = changed[2].iloc[:-1].copy()
    with pytest.raises(ValueError):
        build(tuple(changed))
    changed = list(copy.deepcopy(data))
    extra = changed[2].iloc[0].copy()
    extra["race_date"] = dt.date.fromisoformat(changed[5])
    extra["race_id"] = changed[4]
    changed[2] = pd.concat([changed[2], pd.DataFrame([extra])], ignore_index=True)
    assert build(tuple(changed))["legacy_history"] == build(data)["legacy_history"]


def test_disabled_reader_never_constructs_session():
    def fail():
        raise AssertionError("must not construct session")

    reader = f.DatabaseFeatureReader(session_factory=fail)
    with pytest.raises(ValueError, match="new_collection_disabled"):
        reader.capture("202605040907", "2026-10-27", {}, object())


def test_enabled_reader_requires_explicit_factory():
    with pytest.raises(ValueError, match="feature_session_factory_required"):
        f.DatabaseFeatureReader(enabled=True).capture(
            "202605040907", "2026-10-27", {}, object()
        )


@pytest.mark.parametrize("value", [1.5, True])
def test_fractional_or_bool_horse_number_not_truncated(data, value):
    changed = list(copy.deepcopy(data))
    changed[0]["horse_number"] = changed[0].horse_number.astype(object)
    changed[0].loc[0, "horse_number"] = value
    with pytest.raises(ValueError, match="horse_number_invalid"):
        build(tuple(changed))


def test_pure_identity_map_is_explicit_and_unambiguous(data):
    a, b, h, q, r, d = data
    result = f.build_from_snapshot(
        a,
        b,
        h,
        q,
        race_id=r,
        race_date=d,
        identity_map=[dict(source_id="123", canonical_id="H1")],
    )
    assert result["source_evidence"]["identity_map"] == [
        dict(source_id="123", canonical_id="H1")
    ]
    with pytest.raises(ValueError, match="identity_map_ambiguous"):
        f.build_from_snapshot(
            a,
            b,
            h,
            q,
            race_id=r,
            race_date=d,
            identity_map=[
                dict(source_id="123", canonical_id="H1"),
                dict(source_id="124", canonical_id="H1"),
            ],
        )


def test_capture_readonly_same_snapshot_and_mapping(data, monkeypatch):
    from types import SimpleNamespace

    a, b, h, q, r, d = data
    # All DB calls are fake; production SQL execution is never reached.
    frames = SimpleNamespace(
        races=b[["race_id", "race_date"]].drop_duplicates().reset_index(drop=True),
        race_horses=b[["race_id", "horse_id"]].assign(entry_status="started"),
        race_results=b.loc[
            ~b.race_id.eq(r), ["race_id", "horse_id", "finish_order", "result_status"]
        ],
        horses=pd.DataFrame(columns=["horse_id"]),
    )
    monkeypatch.setattr(f, "load_frames", lambda session, **kwargs: frames)
    monkeypatch.setattr(
        f.market_ev, "load_rows", lambda conn, **kwargs: b.copy(deep=True)
    )
    monkeypatch.setattr(
        f, "assemble_feature_matrix", lambda *args, **kwargs: a.copy(deep=True)
    )

    class Result:
        def __init__(self, value=None, rows=None):
            self.value, self.rows = value, rows

        def scalar_one(self):
            return self.value

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def __init__(self):
            self.calls = []
            self.rolled_back = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connection(self):
            return self

        def execute(self, stmt, params=None):
            sql = str(stmt)
            self.calls.append(sql)
            if "SHOW transaction_read_only" in sql:
                return Result("on")
            if "SHOW transaction_isolation" in sql:
                return Result("repeatable read")
            if "txid_current_snapshot" in sql:
                return Result("1:9:")
            if "count(*)" in sql:
                return Result(0)
            if "FROM id_mappings" in sql:
                return Result(
                    rows=[
                        dict(
                            source_id="123", canonical_id="H1", mapping_status="mapped"
                        )
                    ]
                )
            return Result()

        def rollback(self):
            self.rolled_back = True

    class Clock:
        def __init__(self):
            self.at = dt.datetime(2026, 9, 15, 0, 0, 2, tzinfo=dt.UTC)

        def now(self):
            self.at += dt.timedelta(milliseconds=1)
            return self.at.isoformat()

    session = Session()
    result = f.DatabaseFeatureReader(
        enabled=True, session_factory=lambda: session
    ).capture(r, d, q, Clock())
    assert (
        session.calls[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    assert session.rolled_back
    evidence = result["source_evidence"]
    assert evidence["read_only"] == "on" and evidence["isolation"] == "repeatable read"
    assert evidence["identity_map"] == [dict(source_id="123", canonical_id="H1")]
    assert evidence["identity_mapping_complete"] is False
    assert (
        evidence["identity_mapping_available_at"] == evidence["acquisition_finished_at"]
    )
    assert result["observed_by_at"] == result["generated_at"]
    assert all(
        c.time(stamp) <= c.time(result["generated_at"])
        for kind in result["source_times"].values()
        for stamp in kind.values()
    )


def test_ordinary_legacy_feature_builder_is_connected_without_db(data):
    from horseracing_features.loader import Frames
    from horseracing_features.builder import assemble_feature_matrix

    _, raw, _, quote, rid, day = data
    races = (
        raw[
            [
                "race_id",
                "race_date",
                "venue_code",
                "distance",
                "track_type",
                "going",
                "weather",
                "race_class",
                "race_number",
                "prize_money",
            ]
        ]
        .drop_duplicates("race_id")
        .reset_index(drop=True)
    )
    horses = raw[
        [
            "race_id",
            "horse_id",
            "age",
            "sex",
            "frame",
            "horse_number",
            "jockey_id",
            "trainer_id",
            "weight",
            "weight_diff",
            "jockey_weight",
            "running_style",
            "popularity",
            "odds",
        ]
    ].assign(entry_status="started")
    results = raw.loc[
        ~raw.race_id.eq(rid),
        ["race_id", "horse_id", "finish_order", "last_3f", "result_status"],
    ].copy()
    for name in ["finish_time", "finish_time_diff", "corner_orders", "first_3f"]:
        results[name] = None
    frames = Frames(races=races, race_horses=horses, race_results=results)
    matrix = assemble_feature_matrix(
        frames,
        start_date=dt.date.fromisoformat(day),
        end_date=dt.date.fromisoformat(day),
        wanted=frozenset(models.input_columns("legacy")),
        target_race_ids=frozenset([rid]),
        use_materialized=False,
    )
    history = horses[["race_id", "horse_id", "entry_status"]].merge(
        races[["race_id", "race_date"]], on="race_id", validate="many_to_one"
    )
    result = f.build_from_snapshot(
        matrix, raw, history, quote, race_id=rid, race_date=day
    )
    assert all(
        row["features"]["career_starts"] == 2 for row in result["rows"]["legacy"]
    )
    assert all(
        row["features"]["days_since_last"] == 7 for row in result["rows"]["legacy"]
    )
