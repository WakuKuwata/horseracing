"""Feature 074 US3: OOF calibration evidence is deterministic and DB-read-only."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import PredictionRun, Recommendation
from sqlalchemy import func, select

from horseracing_probability.oof_calibration import calibrate_oof
from tests._synth import seed_predicted_race

pytestmark = pytest.mark.integration

_BASE_MODEL_VERSION = "lgbm-063"
_WIN_PROBS = {"A": 0.40, "B": 0.30, "C": 0.20, "D": 0.10}
_OOF_PROBS = {
    "A": {"win": 0.40, "top2": 0.75, "top3": 0.95},
    "B": {"win": 0.30, "top2": 0.65, "top3": 0.85},
    "C": {"win": 0.20, "top2": 0.40, "top3": 0.70},
    "D": {"win": 0.10, "top2": 0.20, "top3": 0.50},
}
_FINISH_BY_WINNER = {
    "A": {"A": 1, "B": 2, "C": 3, "D": 4},
    "B": {"A": 2, "B": 1, "C": 3, "D": 4},
    "C": {"A": 2, "B": 3, "C": 1, "D": 4},
}


def _row_counts(session) -> tuple[int, int]:
    prediction_runs = session.scalar(select(func.count()).select_from(PredictionRun))
    recommendations = session.scalar(select(func.count()).select_from(Recommendation))
    return int(prediction_runs or 0), int(recommendations or 0)


def test_calibrate_oof_end_to_end_is_deterministic_and_read_only(session):
    predictions = {}
    race_counts_by_day = {
        2020: (13, 13, 12, 12),
        2021: (4, 3, 3),
        2022: (4, 3, 3),
    }
    winner_cycle = ("A", "A", "A", "A", "A", "A", "A", "B", "B", "C")

    race_index = 0
    for year, daily_counts in race_counts_by_day.items():
        year_race_index = 0
        for day, daily_count in enumerate(daily_counts, start=1):
            for _ in range(daily_count):
                race_index += 1
                year_race_index += 1
                race_id = f"{year}05{day:02d}{year_race_index:02d}01"
                winner = winner_cycle[(race_index - 1) % len(winner_cycle)]
                seed_predicted_race(
                    session,
                    race_id=race_id,
                    win_probs=_WIN_PROBS,
                    finish=_FINISH_BY_WINNER[winner],
                    race_date=datetime.date(year, 5, day),
                    model_version=_BASE_MODEL_VERSION,
                )
                predictions[race_id] = {
                    horse_id: dict(probabilities)
                    for horse_id, probabilities in _OOF_PROBS.items()
                }

    bundle = {"bundle_digest": "test", "predictions": predictions}
    gate_config = {
        "verdict": {
            "non_inferior_margin_ece": 0.001,
            "no_decision_min_days": 10,
        },
        "transfer_check": {"ks_distance_max": 0.10},
    }
    assert len({race_id[:8] for race_id in predictions}) >= gate_config["verdict"][
        "no_decision_min_days"
    ]
    assert len({race_id[:4] for race_id in predictions}) >= 3

    counts_before = _row_counts(session)
    first = calibrate_oof(
        session,
        bundle,
        gate_config=gate_config,
        base_model_version=_BASE_MODEL_VERSION,
    )
    second = calibrate_oof(
        session,
        bundle,
        gate_config=gate_config,
        base_model_version=_BASE_MODEL_VERSION,
    )
    counts_after = _row_counts(session)

    assert first["evaluation_contract_version"] == "v2"
    assert first["stage"] == "two_gamma_win"
    assert first["verdict"] in {"ADOPT", "REJECT", "NO_DECISION"}
    assert set(first["ece"]) == {"raw", "calibrated", "delta"}
    assert "ks" in first["transfer_check"]
    assert first["n_eval_days"] >= gate_config["verdict"]["no_decision_min_days"]
    assert first == second
    assert counts_after == counts_before
