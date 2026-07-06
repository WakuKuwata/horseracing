"""Feature 057: model-switching — /models purpose fields (US1) + predictions ?model_version=
selection, typed 404 no-fallback, available_models (US2)."""

from __future__ import annotations

import pytest
from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion

from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010101"
_HORSES = {
    1: {"win": 0.45, "odds": 2.0}, 2: {"win": 0.25, "odds": 3.5},
    3: {"win": 0.18, "odds": 6.0}, 4: {"win": 0.12, "odds": 9.0},
}
# Different per-horse win for the second model, so we can tell the two runs apart.
_HORSES_B = {
    1: {"win": 0.30, "odds": 2.0}, 2: {"win": 0.30, "odds": 3.5},
    3: {"win": 0.22, "odds": 6.0}, 4: {"win": 0.18, "odds": 9.0},
}


def _two_models_one_race(session):
    """active m-active + candidate m-other, each with a run for the same race."""
    seed_model(session, model_version="m-active", adoption=AdoptionStatus.ACTIVE)
    seed_model(session, model_version="m-other", adoption=AdoptionStatus.CANDIDATE)
    run_a = seed_race(session, race_id=_RACE, horses=_HORSES, model_version="m-active")
    run_b = seed_race(session, race_id=_RACE, horses=_HORSES_B, model_version="m-other")
    return run_a, run_b


# ---------------- US1: purpose metadata on /models ----------------

def test_models_endpoint_exposes_display_name_and_purpose(client, session):
    seed_model(session, model_version="m-active", adoption=AdoptionStatus.ACTIVE)
    mv = session.get(ModelVersion, "m-active")
    mv.display_name = "意思決定支援モデル"
    mv.purpose = "市場から独立した予測"
    session.commit()
    seed_model(session, model_version="m-bare", adoption=AdoptionStatus.CANDIDATE)  # unset → null

    items = {i["model_version"]: i for i in client.get("/api/v1/models").json()["items"]}
    assert items["m-active"]["display_name"] == "意思決定支援モデル"
    assert items["m-active"]["purpose"] == "市場から独立した予測"
    assert items["m-bare"]["display_name"] is None
    assert items["m-bare"]["purpose"] is None


# ---------------- US2: model_version selection ----------------

def test_unspecified_returns_active_model_backward_compat(client, session):
    run_a, _ = _two_models_one_race(session)
    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    assert body["run"]["model_version"] == "m-active"
    assert body["run"]["prediction_run_id"] == str(run_a)
    fav = next(h for h in body["horses"] if h["horse_number"] == 1)
    assert abs(fav["win"] - 0.45) < 1e-6  # m-active's value


def test_specified_model_returns_that_run(client, session):
    _, run_b = _two_models_one_race(session)
    body = client.get(
        f"/api/v1/races/{_RACE}/predictions", params={"model_version": "m-other"}
    ).json()
    assert body["run"]["model_version"] == "m-other"
    assert body["run"]["prediction_run_id"] == str(run_b)
    fav = next(h for h in body["horses"] if h["horse_number"] == 1)
    assert abs(fav["win"] - 0.30) < 1e-6  # m-other's value (not 0.45)


def test_specified_model_without_run_is_404_no_fallback(client, session):
    # m-active has a run; m-ghost is a real model but has NO run for this race → 404 (not m-active).
    seed_model(session, model_version="m-active", adoption=AdoptionStatus.ACTIVE)
    seed_model(session, model_version="m-ghost", adoption=AdoptionStatus.CANDIDATE)
    seed_race(session, race_id=_RACE, horses=_HORSES, model_version="m-active")
    r = client.get(f"/api/v1/races/{_RACE}/predictions", params={"model_version": "m-ghost"})
    assert r.status_code == 404
    assert r.json()["code"] == "prediction_unavailable"


def test_nonexistent_model_is_404_not_500(client, session):
    seed_model(session, model_version="m-active", adoption=AdoptionStatus.ACTIVE)
    seed_race(session, race_id=_RACE, horses=_HORSES, model_version="m-active")
    r = client.get(f"/api/v1/races/{_RACE}/predictions", params={"model_version": "does-not-exist"})
    assert r.status_code == 404
    assert r.json()["code"] == "prediction_unavailable"


def test_available_models_content_order_and_is_selected(client, session):
    _two_models_one_race(session)
    body = client.get(f"/api/v1/races/{_RACE}/predictions").json()
    avail = body["available_models"]
    assert [m["model_version"] for m in avail] == ["m-active", "m-other"]  # active-first
    selected = [m["model_version"] for m in avail if m["is_selected"]]
    assert selected == ["m-active"]  # unspecified → active is selected
    # selecting m-other flips is_selected
    body2 = client.get(
        f"/api/v1/races/{_RACE}/predictions", params={"model_version": "m-other"}
    ).json()
    assert [m["model_version"] for m in body2["available_models"] if m["is_selected"]] == ["m-other"]


def test_selection_is_deterministic_for_specified_model(client, session):
    # Two runs for the SAME model on the same race → repeated calls return the same run (FR-004).
    seed_model(session, model_version="m-active", adoption=AdoptionStatus.ACTIVE)
    seed_race(session, race_id=_RACE, horses=_HORSES, model_version="m-active")
    seed_race(session, race_id=_RACE, horses=_HORSES, model_version="m-active")  # second run
    first = client.get(
        f"/api/v1/races/{_RACE}/predictions", params={"model_version": "m-active"}
    ).json()["run"]["prediction_run_id"]
    second = client.get(
        f"/api/v1/races/{_RACE}/predictions", params={"model_version": "m-active"}
    ).json()["run"]["prediction_run_id"]
    assert first == second


def test_no_active_model_unspecified_still_returns_a_run(client, session):
    # Edge: no active model but runs exist → existing behavior (deterministic latest), NOT typed-empty.
    seed_model(session, model_version="m-cand", adoption=AdoptionStatus.CANDIDATE)
    seed_race(session, race_id=_RACE, horses=_HORSES, model_version="m-cand")
    r = client.get(f"/api/v1/races/{_RACE}/predictions")
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["model_version"] == "m-cand"
    assert len(body["horses"]) == 4
