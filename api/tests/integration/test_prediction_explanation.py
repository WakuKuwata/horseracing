"""Feature 040 T010/T019: predictions expose persisted explanation (read as-is) and a neutral
model-vs-market divergence band (pre-registered FR-011). Endpoint stays read-only.
"""

from __future__ import annotations

import pytest

from horseracing_api.selection import (
    DIVERGENCE_ABS_FLOOR,
    DIVERGENCE_REL_FRAC,
    divergence_band,
)
from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010101"

_EXP = {
    "method": "lgbm_pred_contrib", "method_version": 1, "k": 2,
    "base_value": -3.0, "score": -2.5, "other_contribution": 0.1,
    "items": [
        {"feature": "te_jockey_id", "value": 0.08, "contribution": 0.5},
        {"feature": "venue_code", "value": "05", "contribution": -0.1},
    ],
}


def test_explanation_transparent_and_null(client, session):
    seed_model(session)
    seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 2.0, "explanation": _EXP},   # has explanation
        2: {"win": 0.3, "odds": 3.0},                        # no explanation -> null
    })
    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    hs = {h["horse_number"]: h for h in body["horses"]}
    # H1: explanation passed through as-is (typed), items preserved incl string value
    e = hs[1]["explanation"]
    assert e is not None
    assert e["method"] == "lgbm_pred_contrib" and e["k"] == 2
    assert e["items"][0]["feature"] == "te_jockey_id"
    assert e["items"][1]["value"] == "05"
    # H2: no explanation -> null (未提供), not error/empty
    assert hs[2]["explanation"] is None


def test_divergence_band_present_and_suppressed(client, session):
    seed_model(session)
    # H1 strong favorite by odds (q high) but low p -> market_higher; H2 opposite -> model_higher
    seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.10, "odds": 1.5},
        2: {"win": 0.60, "odds": 5.0},
        3: {"win": 0.20, "odds": 3.0},
    })
    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    assert body["canonical_consistent"] is True
    hs = {h["horse_number"]: h for h in body["horses"]}
    assert hs[1]["divergence"] == "market_higher"
    assert hs[2]["divergence"] == "model_higher"
    assert hs[3]["divergence"] in {"similar", "market_higher", "model_higher"}


def test_divergence_suppressed_when_inconsistent(client, session):
    seed_model(session)
    # one horse lacks odds -> q population != p population -> canonical_consistent False -> all null
    seed_race(session, race_id=_RACE, horses={
        1: {"win": 0.4, "odds": 2.0},
        2: {"win": 0.6},  # no odds
    })
    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    assert body["canonical_consistent"] is False
    assert all(h["divergence"] is None for h in body["horses"])


def test_divergence_band_pure_function_boundaries():
    # FR-011 pre-registered thresholds; equality falls into "similar"
    assert DIVERGENCE_ABS_FLOOR == 0.03 and DIVERGENCE_REL_FRAC == 0.5
    q = 0.20
    margin = max(0.03, 0.5 * q)  # 0.10
    assert divergence_band(q, q) == "similar"
    assert divergence_band(q - margin, q) == "similar"           # boundary -> similar
    assert divergence_band(q - margin - 1e-9, q) == "market_higher"
    assert divergence_band(q + margin + 1e-9, q) == "model_higher"
    assert divergence_band(None, q) is None
    assert divergence_band(0.3, None) is None
    # small q uses the absolute floor 0.03, not 0.5*q
    assert divergence_band(0.02, 0.01) == "similar"              # within 0.03 floor
