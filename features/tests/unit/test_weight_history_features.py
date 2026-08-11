"""Hand-computed checks for the strictly-before previous body-weight feature."""

from __future__ import annotations

import pandas as pd

from horseracing_features.weight_history_features import build_weight_history_features
from tests._frames import make_frames


def _frames():
    return make_frames(
        [
            # H: ordinary history, then two appearances on one day.
            {
                "race_id": "200801010101",
                "race_date": "2008-01-01",
                "horses": [{"horse_id": "H", "weight": 450}],
            },
            {
                "race_id": "200802010101",
                "race_date": "2008-02-01",
                "horses": [{"horse_id": "H", "weight": 455}],
            },
            {
                "race_id": "200803010101",
                "race_date": "2008-03-01",
                "horses": [{"horse_id": "H", "weight": 460}],
            },
            {
                "race_id": "200804010101",
                "race_date": "2008-04-01",
                "horses": [{"horse_id": "H", "weight": 470}],
            },
            {
                "race_id": "200804010102",
                "race_date": "2008-04-01",
                "horses": [{"horse_id": "H", "weight": 471}],
            },
            {
                "race_id": "200805010101",
                "race_date": "2008-05-01",
                "horses": [{"horse_id": "H", "weight": 472}],
            },
            # C: the most recent entry was cancelled, so use the earlier started race.
            {
                "race_id": "200801020101",
                "race_date": "2008-01-02",
                "horses": [{"horse_id": "C", "weight": 410}],
            },
            {
                "race_id": "200802020101",
                "race_date": "2008-02-02",
                "horses": [{"horse_id": "C", "weight": 420, "entry_status": "cancelled"}],
            },
            {
                "race_id": "200803020101",
                "race_date": "2008-03-02",
                "horses": [{"horse_id": "C", "weight": 430}],
            },
            # N: a started race with missing weight is not a source.
            {
                "race_id": "200801030101",
                "race_date": "2008-01-03",
                "horses": [{"horse_id": "N", "weight": 430}],
            },
            {
                "race_id": "200802030101",
                "race_date": "2008-02-03",
                "horses": [{"horse_id": "N", "weight": None}],
            },
            {
                "race_id": "200803030101",
                "race_date": "2008-03-03",
                "horses": [{"horse_id": "N", "weight": 440}],
            },
            # O: both lower and upper outliers are skipped.
            {
                "race_id": "200801040101",
                "race_date": "2008-01-04",
                "horses": [{"horse_id": "O", "weight": 440}],
            },
            {
                "race_id": "200802040101",
                "race_date": "2008-02-04",
                "horses": [{"horse_id": "O", "weight": 150}],
            },
            {
                "race_id": "200803040101",
                "race_date": "2008-03-04",
                "horses": [{"horse_id": "O", "weight": 900}],
            },
            {
                "race_id": "200804040101",
                "race_date": "2008-04-04",
                "horses": [{"horse_id": "O", "weight": 450}],
            },
            # D: two eligible candidates on the most recent source date are ambiguous.
            {
                "race_id": "200801050101",
                "race_date": "2008-01-05",
                "horses": [{"horse_id": "D", "weight": 470}],
            },
            {
                "race_id": "200802050101",
                "race_date": "2008-02-05",
                "horses": [{"horse_id": "D", "weight": 480}],
            },
            {
                "race_id": "200802050102",
                "race_date": "2008-02-05",
                "horses": [{"horse_id": "D", "weight": 481}],
            },
            {
                "race_id": "200803050101",
                "race_date": "2008-03-05",
                "horses": [{"horse_id": "D", "weight": 490}],
            },
            # E: debut, with no source row.
            {
                "race_id": "200803060101",
                "race_date": "2008-03-06",
                "horses": [{"horse_id": "E", "weight": 400}],
            },
        ]
    )


def _value(race_id: str, horse_id: str) -> float:
    out = build_weight_history_features(_frames())
    return out.loc[(out["race_id"] == race_id) & (out["horse_id"] == horse_id), "prev_weight"].iloc[
        0
    ]


def test_uses_most_recent_valid_started_weight():
    assert _value("200803010101", "H") == 455.0


def test_excludes_all_same_day_runs():
    assert _value("200804010102", "H") == 460.0


def test_cancelled_previous_entry_falls_back_to_earlier_started_race():
    assert _value("200803020101", "C") == 410.0


def test_null_previous_weight_falls_back_to_earlier_valid_weight():
    assert _value("200803030101", "N") == 430.0


def test_out_of_range_weights_are_not_sources():
    assert _value("200804040101", "O") == 440.0


def test_multiple_candidates_for_same_horse_and_date_are_ambiguous():
    assert pd.isna(_value("200803050101", "D"))
    assert pd.isna(_value("200805010101", "H"))


def test_debut_has_nan_not_an_imputed_value():
    assert pd.isna(_value("200803060101", "E"))


def test_target_filter_only_limits_output_not_history_source():
    target = "200803010101"
    out = build_weight_history_features(_frames(), target_race_ids=frozenset({target}))
    assert out[["race_id", "horse_id"]].to_dict("records") == [{"race_id": target, "horse_id": "H"}]
    assert out.loc[0, "prev_weight"] == 455.0


def test_output_schema_and_prev_weight_dtype():
    out = build_weight_history_features(_frames())
    assert out.columns.tolist() == ["race_id", "horse_id", "prev_weight"]
    assert out["prev_weight"].dtype == "float64"
