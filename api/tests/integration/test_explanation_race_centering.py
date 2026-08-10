"""Feature 089 US2: v1/v2 explanation JSONB survives the typed API projection."""

from __future__ import annotations

import pytest

from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010101"


def test_v1_explanation_defaults_centered_fields_to_null(client, session):
    explanation = {
        "method": "lgbm_pred_contrib",
        "method_version": 1,
        "k": 1,
        "base_value": -3.0,
        "score": -2.5,
        "other_contribution": 0.0,
        "items": [{"feature": "prev_finish", "value": 3.0, "contribution": 0.5}],
    }
    seed_model(session)
    seed_race(
        session,
        race_id=_RACE,
        horses={
            1: {"win": 0.6, "odds": 2.0, "explanation": explanation},
            2: {"win": 0.4, "odds": 3.0},
        },
    )

    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    response = next(h for h in body["horses"] if h["horse_number"] == 1)["explanation"]

    assert response is not None
    assert response["score_centered"] is None
    assert response["other_contribution_centered"] is None
    assert response["centering_population_size"] is None
    assert response["items"][0]["contribution_centered"] is None


def test_v2_explanation_preserves_centered_fields(client, session):
    explanation = {
        "method": "lgbm_pred_contrib",
        "method_version": 2,
        "k": 1,
        "base_value": -3.0,
        "score": -2.5,
        "other_contribution": 0.0,
        "score_centered": 0.4,
        "other_contribution_centered": 0.15,
        "centering_population_size": 12,
        "items": [
            {
                "feature": "prev_finish",
                "value": 3.0,
                "contribution": 0.5,
                "contribution_centered": 0.25,
            }
        ],
    }
    seed_model(session)
    seed_race(
        session,
        race_id=_RACE,
        horses={
            1: {"win": 0.6, "odds": 2.0, "explanation": explanation},
            2: {"win": 0.4, "odds": 3.0},
        },
    )

    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    response = next(h for h in body["horses"] if h["horse_number"] == 1)["explanation"]

    assert response is not None
    assert response["score_centered"] == 0.4
    assert response["other_contribution_centered"] == 0.15
    assert response["centering_population_size"] == 12
    assert response["items"][0]["contribution_centered"] == 0.25
