"""T024 (021): the API is strictly READ-ONLY — every path exposes GET only (no write verbs).

Guards the 014 invariant after 021 adds market q (predictions) and the calibration endpoint: no new
write path may be introduced. Also asserts the new endpoints leave the DB unchanged.
"""

from __future__ import annotations

import pytest

from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010101"
_HORSES = {1: {"win": 0.5, "odds": 2.0, "finish": 1}, 2: {"win": 0.5, "odds": 3.0, "finish": 2}}
_WRITE_VERBS = {"post", "put", "patch", "delete"}


def test_every_path_is_get_only(client):
    spec = client.get("/openapi.json").json()
    for path, ops in spec["paths"].items():
        verbs = set(ops) & (_WRITE_VERBS | {"get"})
        assert verbs == {"get"}, f"{path} exposes non-GET verbs: {verbs}"


def test_new_endpoints_do_not_mutate(client, session):
    seed_model(session)
    seed_race(session, race_id=_RACE, horses=_HORSES)
    from horseracing_db.models import RaceHorse, RacePrediction
    from sqlalchemy import func, select

    def counts():
        return (
            session.scalar(select(func.count()).select_from(RaceHorse)),
            session.scalar(select(func.count()).select_from(RacePrediction)),
        )

    before = counts()
    assert client.get(f"/api/v1/races/{_RACE}/predictions").status_code == 200
    assert client.get("/api/v1/models/m-active/calibration").status_code in (200, 404)
    session.expire_all()
    assert counts() == before  # read-only: no rows written
