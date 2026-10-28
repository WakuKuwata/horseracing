"""B7 (2026-10-28): GET /api/v1/market-ev/day?date= — the race day's 期待回収率 at a glance.

Covers the typed 422 for a missing date, the empty day, that only races with stored rows of the
DISPLAYED version appear (board order, never value order), that every item is exactly a fold of
the per-race endpoint (state, provenance, counts above both thresholds), the unavailable item
(field changed after the compute → null counts) and that no per-horse value leaks.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import Race, RaceHorse
from horseracing_eval.attention_rules import (
    DISPLAYED_MARKET_EV_MODEL_VERSION,
    SINGLE_SEED_MODEL_VERSION,
)
from sqlalchemy import update as sa_update

from tests._synth import seed_market_ev, seed_model, seed_race

pytestmark = pytest.mark.integration

_DATE = datetime.date(2026, 9, 27)
_URL = "/api/v1/market-ev/day"
_FIELD = {
    1: {"odds": 2.5},
    2: {"odds": 4.0},
    3: {"odds": 8.0},
    4: {"odds": 15.0},
}
# 0.36×2.5=0.9 / 0.25×4.0=1.0 / 0.16×8.0=1.28 / 0.09×15.0=1.35 → two horses above 1.2
_EV = {
    1: {"win_prob": 0.36, "odds_used": 2.5},
    2: {"win_prob": 0.25, "odds_used": 4.0},
    3: {"win_prob": 0.16, "odds_used": 8.0},
    4: {"win_prob": 0.09, "odds_used": 15.0},
}


def _race(session, race_id, *, venue="05", number=1, post_time=None, field=None):
    seed_race(session, race_id=race_id, race_date=_DATE, venue_code=venue, race_number=number,
              horses=field or _FIELD)
    if post_time is not None:
        session.execute(sa_update(Race).where(Race.race_id == race_id).values(post_time=post_time))
        session.commit()


def _keys(body):
    return {
        "race_id", "venue_code", "race_number", "post_time", "has_results", "status", "reason",
        "computed_at", "odds_observed_at", "odds_changed_after_compute",
        "result_pending_at_compute", "n_horses", "n_exceeds_threshold", "mix_status",
        "n_exceeds_mix_threshold",
    }


def test_missing_date_is_typed_422(client):
    resp = client.get(_URL)
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"status", "code", "detail"}
    assert body["code"] == "validation_error"
    assert client.get(_URL, params={"date": "yesterday"}).status_code == 422


def test_day_without_races_is_empty(client):
    resp = client.get(_URL, params={"date": "2026-09-27"})
    assert resp.status_code == 200
    assert resp.json() == {
        "date": "2026-09-27",
        "model_version": DISPLAYED_MARKET_EV_MODEL_VERSION,
        "threshold": 1.2,
        "mix_threshold": 1.0,
        "n_races": 0,
        "n_computed": 0,
        "n_available": 0,
        "items": [],
    }


def test_items_cover_only_computed_races_in_board_order(client, session):
    seed_model(session)
    # three races: 06-3R (rows), 05-11R (no rows), 05-2R (rows) → items = [05-2R, 06-3R]
    _race(session, "202609270603", venue="06", number=3)
    _race(session, "202609270511", venue="05", number=11)
    _race(session, "202609270502", venue="05", number=2,
          post_time=datetime.datetime(2026, 9, 27, 1, 30, tzinfo=datetime.UTC))
    seed_market_ev(session, race_id="202609270603", horses=_EV)
    seed_market_ev(session, race_id="202609270502", horses=_EV,
                   computed_at=datetime.datetime(2026, 9, 27, 0, 40, tzinfo=datetime.UTC))
    # a single-seed-only race never counts as computed (138 D8: the displayed version only)
    _race(session, "202609270801", venue="08", number=1)
    seed_market_ev(session, race_id="202609270801", horses=_EV,
                   model_version=SINGLE_SEED_MODEL_VERSION)

    resp = client.get(_URL, params={"date": "2026-09-27"})
    assert resp.status_code == 200
    body = resp.json()
    assert "win_prob" not in resp.text and "expected_return" not in resp.text
    assert body["n_races"] == 4
    assert body["n_computed"] == 2 == len(body["items"])
    assert body["n_available"] == 2
    assert [i["race_id"] for i in body["items"]] == ["202609270502", "202609270603"]
    first = body["items"][0]
    assert set(first) == _keys(first)
    assert first["venue_code"] == "05" and first["race_number"] == 2
    assert first["post_time"] is not None and first["has_results"] is False
    assert first["status"] == "available" and first["reason"] is None
    assert first["n_horses"] == 4
    assert first["n_exceeds_threshold"] == 2
    # the default seed booster is not the mixture's → mix unavailable, mixed count null
    assert first["mix_status"] == "unavailable" and first["n_exceeds_mix_threshold"] is None
    assert first["computed_at"].startswith("2026-09-27T00:40")
    assert first["odds_changed_after_compute"] is False
    assert first["result_pending_at_compute"] is True


def test_each_item_folds_the_per_race_endpoint(client, session):
    """The listing can never disagree with the race page: same state, same provenance, counts
    equal to the per-horse flags of /races/{id}/market-ev (mix available and unavailable)."""
    seed_model(session)
    _race(session, "202609270501", venue="05", number=1)
    _race(session, "202609270502", venue="05", number=2)
    seed_market_ev(session, race_id="202609270501", horses=_EV, booster="ensemble_2026.json")
    seed_market_ev(session, race_id="202609270502", horses=_EV)
    # a changed price after the compute shows up in the flag, not in the counts
    session.execute(
        sa_update(RaceHorse)
        .where(RaceHorse.race_id == "202609270502", RaceHorse.horse_id == "H1")
        .values(odds=Decimal("2.6"))
    )
    session.commit()

    day = client.get(_URL, params={"date": "2026-09-27"}).json()
    assert [i["race_id"] for i in day["items"]] == ["202609270501", "202609270502"]
    for item in day["items"]:
        page = client.get(f"/api/v1/races/{item['race_id']}/market-ev").json()
        assert page["status"] == "available" == item["status"]
        assert item["computed_at"] == page["computed_at"]
        assert item["odds_observed_at"] == page["odds_observed_at"]
        assert item["odds_changed_after_compute"] is page["odds_changed_after_compute"]
        assert item["result_pending_at_compute"] is page["result_pending_at_compute"]
        assert item["n_horses"] == len(page["horses"])
        assert item["n_exceeds_threshold"] == sum(h["exceeds_threshold"] for h in page["horses"])
        assert item["mix_status"] == page["mix"]["status"]
        if page["mix"]["status"] == "available":
            assert item["n_exceeds_mix_threshold"] == sum(
                bool(h["exceeds_mix_threshold"]) for h in page["horses"]
            )
        else:
            assert item["n_exceeds_mix_threshold"] is None
    mixed, unmixed = day["items"]
    assert mixed["mix_status"] == "available" and mixed["n_exceeds_mix_threshold"] is not None
    assert unmixed["mix_status"] == "unavailable"
    assert unmixed["odds_changed_after_compute"] is True
    assert mixed["odds_changed_after_compute"] is False


def test_field_changed_race_is_unavailable_with_null_counts(client, session):
    seed_model(session)
    _race(session, "202609270501", venue="05", number=1)
    seed_market_ev(session, race_id="202609270501", horses=_EV)
    session.execute(
        sa_update(RaceHorse)
        .where(RaceHorse.race_id == "202609270501", RaceHorse.horse_id == "H4")
        .values(entry_status=EntryStatus.CANCELLED)
    )
    session.commit()

    body = client.get(_URL, params={"date": "2026-09-27"}).json()
    assert body["n_races"] == 1 and body["n_computed"] == 1 and body["n_available"] == 0
    item = body["items"][0]
    assert item["status"] == "unavailable" and item["reason"] == "field_changed"
    for key in ("computed_at", "odds_observed_at", "odds_changed_after_compute",
                "result_pending_at_compute", "n_horses", "n_exceeds_threshold", "mix_status",
                "n_exceeds_mix_threshold"):
        assert item[key] is None, key
    page = client.get("/api/v1/races/202609270501/market-ev").json()
    assert page == {"status": "unavailable", "race_id": "202609270501",
                    "reason": "field_changed", "threshold": 1.2}
