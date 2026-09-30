"""Feature 137: GET /api/v1/races/{race_id}/market-ev — read-only market-aware expected return.

Covers the typed states (422 / 404 / not_computed / field_changed / available), that available values
equal the persisted rows (and win_prob is never exposed), the STRICT threshold at exactly 1.2, the
odds-changed and result-pending provenance flags, and independence from the win-model selection.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import AdoptionStatus, EntryStatus
from horseracing_db.models import Horse, MarketEvPrediction, RaceHorse
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from horseracing_api.market_ev import MARKET_EV_THRESHOLD
from tests._synth import seed_market_ev, seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "202609270511"
_DATE = datetime.date(2026, 9, 27)
_FIELD = {  # horse_number -> current started entry with real win odds
    1: {"win": 0.40, "odds": 2.5},
    2: {"win": 0.30, "odds": 4.0},
    3: {"win": 0.20, "odds": 8.0},
    4: {"win": 0.10, "odds": 15.0},
}
_EV = {  # horse_number -> stored market-aware p and the odds it used
    1: {"win_prob": 0.36, "odds_used": 2.5},
    2: {"win_prob": 0.25, "odds_used": 4.0},
    3: {"win_prob": 0.16, "odds_used": 8.0},
    4: {"win_prob": 0.09, "odds_used": 15.0},
}


def _url(race_id: str = _RACE) -> str:
    return f"/api/v1/races/{race_id}/market-ev"


def _seed(session, *, race_id: str = _RACE, field=None, ev=None, **ev_kwargs):
    seed_model(session)
    seed_race(session, race_id=race_id, race_date=_DATE, horses=field or _FIELD)
    if ev is not False:
        seed_market_ev(session, race_id=race_id, horses=ev or _EV, **ev_kwargs)


def _parse_ts(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_invalid_race_id_is_typed_422(client):
    resp = client.get(_url("bad"))
    assert resp.status_code == 422
    assert resp.json() == {
        "status": 422, "code": "invalid_race_id", "detail": "race_id must be 12 digits",
    }


def test_unknown_race_is_typed_404(client):
    resp = client.get(_url("202609279999"))
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "code", "detail"}
    assert body["code"] == "race_not_found"


def test_race_without_rows_is_not_computed(client, session):
    _seed(session, ev=False)
    resp = client.get(_url())
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "unavailable", "race_id": _RACE, "reason": "not_computed", "threshold": 1.2,
    }


def test_available_values_equal_persisted_rows(client, session):
    observed = {
        1: datetime.datetime(2026, 9, 27, 2, 40, tzinfo=datetime.UTC),
        3: datetime.datetime(2026, 9, 27, 2, 55, tzinfo=datetime.UTC),
    }
    ev = {n: ({**h, "odds_observed_at": observed[n]} if n in observed else h)
          for n, h in _EV.items()}
    _seed(session, ev=ev, logic_version="mev-v1;features=test")

    resp = client.get(_url())
    assert resp.status_code == 200
    body = resp.json()
    assert "win_prob" not in resp.text  # constitution IV: never exposed as a 1着率

    persisted = session.scalars(
        select(MarketEvPrediction).where(MarketEvPrediction.race_id == _RACE)
        .order_by(MarketEvPrediction.horse_number)
    ).all()
    assert set(body) == {
        "status", "race_id", "model_version", "logic_version", "computed_at", "odds_observed_at",
        "odds_changed_after_compute", "result_pending_at_compute", "threshold", "is_pseudo",
        "horses",
    }
    assert body["status"] == "available"
    assert body["race_id"] == _RACE
    assert body["model_version"] == "mev-binary-v2"
    assert body["logic_version"] == "mev-v1;features=test"
    assert body["threshold"] == MARKET_EV_THRESHOLD == 1.2
    assert body["is_pseudo"] is True
    assert body["odds_changed_after_compute"] is False
    assert body["result_pending_at_compute"] is True
    assert _parse_ts(body["computed_at"]) == persisted[0].computed_at
    assert _parse_ts(body["odds_observed_at"]) == max(r.odds_observed_at for r in persisted)

    assert [h["horse_number"] for h in body["horses"]] == [1, 2, 3, 4]
    for h, row in zip(body["horses"], persisted, strict=True):
        assert set(h) == {
            "horse_id", "horse_number", "expected_return", "odds_used", "exceeds_threshold",
        }
        assert h["horse_id"] == row.horse_id
        assert h["horse_number"] == row.horse_number
        assert h["expected_return"] == float(row.expected_return)
        assert h["odds_used"] == float(row.odds_used)
        assert h["exceeds_threshold"] is (float(row.expected_return) > 1.2)
    # 0.36×2.5=0.9 / 0.25×4.0=1.0 / 0.16×8.0=1.28 / 0.09×15.0=1.35
    assert [h["exceeds_threshold"] for h in body["horses"]] == [False, False, True, True]


def test_threshold_is_strict_at_exactly_one_point_two(client, session):
    ev = {
        1: {"win_prob": 0.36, "odds_used": 2.5},                                # 0.9
        2: {"win_prob": 0.3, "odds_used": 4.0, "expected_return": "1.2"},       # == threshold
        3: {"win_prob": 0.150125, "odds_used": 8.0, "expected_return": "1.2001"},
        4: {"win_prob": 0.09, "odds_used": 15.0},                               # 1.35
    }
    _seed(session, ev=ev)
    horses = {h["horse_number"]: h for h in client.get(_url()).json()["horses"]}
    assert horses[2]["expected_return"] == 1.2
    assert horses[2]["exceeds_threshold"] is False
    assert horses[3]["expected_return"] == 1.2001
    assert horses[3]["exceeds_threshold"] is True
    assert horses[1]["exceeds_threshold"] is False
    assert horses[4]["exceeds_threshold"] is True


def test_cancelled_started_horse_turns_into_field_changed(client, session):
    _seed(session)
    assert client.get(_url()).json()["status"] == "available"

    session.execute(
        sa_update(RaceHorse)
        .where(RaceHorse.race_id == _RACE, RaceHorse.horse_id == "H3")
        .values(entry_status=EntryStatus.CANCELLED)
    )
    session.commit()
    resp = client.get(_url())
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "unavailable", "race_id": _RACE, "reason": "field_changed", "threshold": 1.2,
    }


def test_newly_started_horse_turns_into_field_changed(client, session):
    _seed(session)
    session.merge(Horse(horse_id="H5", horse_name="H5"))
    session.flush()
    session.add(RaceHorse(race_id=_RACE, horse_id="H5", horse_number=5,
                          odds=Decimal("30.0"), entry_status=EntryStatus.STARTED))
    session.commit()
    assert client.get(_url()).json()["reason"] == "field_changed"


def test_non_starter_without_row_keeps_available(client, session):
    # a horse cancelled BEFORE the compute has no row and is not in the started field
    field = {**_FIELD, 5: {"odds": 50.0, "status": EntryStatus.CANCELLED}}
    _seed(session, field=field)
    body = client.get(_url()).json()
    assert body["status"] == "available"
    assert [h["horse_number"] for h in body["horses"]] == [1, 2, 3, 4]


def test_odds_changed_after_compute_follows_current_odds_value(client, session):
    _seed(session)
    before = client.get(_url()).json()
    assert before["odds_changed_after_compute"] is False

    session.execute(
        sa_update(RaceHorse)
        .where(RaceHorse.race_id == _RACE, RaceHorse.horse_id == "H1")
        .values(odds=Decimal("3.1"))
    )
    session.commit()
    after = client.get(_url()).json()
    assert after["status"] == "available"
    assert after["odds_changed_after_compute"] is True
    # the display stays on the compute-time basis (stored values are not re-derived)
    assert after["horses"] == before["horses"]
    assert after["horses"][0]["odds_used"] == 2.5


def test_started_horse_whose_odds_disappear_is_odds_unavailable(client, session):
    _seed(session)
    assert client.get(_url()).json()["status"] == "available"
    session.execute(
        sa_update(RaceHorse)
        .where(RaceHorse.race_id == _RACE, RaceHorse.horse_id == "H1")
        .values(odds=None)
    )
    session.commit()
    resp = client.get(_url())
    assert resp.status_code == 200
    assert resp.json() == {"status": "unavailable", "race_id": _RACE, "reason": "odds_unavailable",
                           "threshold": 1.2}


def test_equal_odds_with_other_scale_is_not_a_change(client, session):
    # race_horses.odds 2.5 vs stored odds_used 2.50 are the same value
    ev = {**_EV, 1: {"win_prob": 0.36, "odds_used": "2.50"}}
    _seed(session, ev=ev)
    assert client.get(_url()).json()["odds_changed_after_compute"] is False


def test_result_pending_is_true_only_when_every_row_says_so(client, session):
    _seed(session)
    assert client.get(_url()).json()["result_pending_at_compute"] is True

    other = "202609270512"
    ev = {**_EV, 2: {**_EV[2], "result_pending": False}}
    _seed(session, race_id=other, ev=ev)
    assert client.get(_url(other)).json()["result_pending_at_compute"] is False

    post = "202609270501"
    _seed(session, race_id=post, result_pending=False)
    assert client.get(_url(post)).json()["result_pending_at_compute"] is False


def test_independent_of_win_model_selection(client, session):
    _seed(session)
    seed_model(session, model_version="m-other", adoption=AdoptionStatus.CANDIDATE)
    seed_race(session, race_id=_RACE, race_date=_DATE, horses=_FIELD, model_version="m-other")

    plain = client.get(_url())
    with_param = client.get(_url(), params={"model_version": "m-other"})
    assert plain.status_code == with_param.status_code == 200
    assert with_param.json() == plain.json()
    assert plain.json()["model_version"] == "mev-binary-v2"

    for mv in (None, "m-active", "m-other"):
        params = {"model_version": mv} if mv else {}
        pred = client.get(f"/api/v1/races/{_RACE}/predictions", params=params)
        assert pred.status_code == 200
        assert "expected_return" not in pred.text
        assert "market_ev" not in pred.json()


def test_prediction_run_without_rows_is_still_not_computed(client, session):
    _seed(session, ev=False)
    assert client.get(f"/api/v1/races/{_RACE}/predictions").json()["horses"]
    assert client.get(_url()).json()["reason"] == "not_computed"


def test_newest_market_model_version_is_shown(client, session):
    _seed(session, model_version="mev-binary-v1",
          computed_at=datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.UTC))
    newer = {n: {**h, "win_prob": 0.05} for n, h in _EV.items()}
    seed_market_ev(session, race_id=_RACE, horses=newer, model_version="mev-binary-v2",
                   computed_at=datetime.datetime(2026, 9, 27, 3, 0, tzinfo=datetime.UTC))
    body = client.get(_url()).json()
    assert body["model_version"] == "mev-binary-v2"
    assert [h["expected_return"] for h in body["horses"]] == [0.125, 0.2, 0.4, 0.75]


def test_market_ev_read_writes_nothing(client, session):
    _seed(session)

    def counts():
        return (
            session.scalar(select(func.count()).select_from(MarketEvPrediction)),
            session.scalar(select(func.count()).select_from(RaceHorse)),
        )

    before = counts()
    assert client.get(_url()).status_code == 200
    session.expire_all()
    assert counts() == before
