"""Feature 052: GET /coverage (per-day product coverage, active-model semantics, range guard)
and GET /jobs (ingestion_jobs history, filters, newest-first, limit cap).

B11 (2026-10-28): the ROI columns (stored 期待回収率 rows of the displayed version / genuine-quote
token on every row / 141 mixture computable from the stored rows) and their nesting.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import IngestionJob
from horseracing_eval.attention_rules import (
    DISPLAYED_MARKET_EV_MODEL_VERSION,
    SINGLE_SEED_MODEL_VERSION,
)

from tests._synth import add_recommendation, seed_market_ev, seed_model, seed_race

pytestmark = pytest.mark.integration

_D1 = datetime.date(2008, 6, 1)
_D2 = datetime.date(2008, 6, 2)


def test_coverage_counts_and_active_semantics(client, session):
    seed_model(session)  # m-active (ACTIVE)
    # day1: race with odds + results + prediction(m-active) + recommendation
    run_id = seed_race(session, race_id="200806010101", race_date=_D1, horses={
        1: {"win": 0.4, "odds": 2.0, "finish": 1}, 2: {"win": 0.3, "odds": 3.0, "finish": 2},
    })
    add_recommendation(session, race_id="200806010101", run_id=run_id)
    # day2: race with odds only (no results / prediction by another CANDIDATE model)
    seed_model(session, model_version="m-cand", adoption=AdoptionStatus.CANDIDATE)
    seed_race(session, race_id="200806020101", race_date=_D2, horses={
        1: {"win": 0.4, "odds": 2.0}, 2: {"win": 0.3, "odds": None},
    }, model_version="m-cand")

    body = client.get(
        f"/api/v1/coverage?date_from={_D1}&date_to={_D2}").json()
    assert body["active_model_version"] == "m-active"
    assert body["market_ev_model_version"] == DISPLAYED_MARKET_EV_MODEL_VERSION
    assert body["quote_token"] == "oddsobs=quote"
    d1, d2 = body["days"]
    assert d1 == {"date": str(_D1), "n_races": 1, "n_with_odds": 1, "n_with_results": 1,
                  "n_predicted_active": 1, "n_with_recommendations": 1,
                  "n_market_ev_computed": 0, "n_market_ev_quote": 0,
                  "n_market_ev_mix_available": 0}
    # day2: predicted by a CANDIDATE model only → active coverage is 0 (044 semantics)
    assert d2["n_races"] == 1 and d2["n_predicted_active"] == 0
    assert d2["n_with_odds"] == 1 and d2["n_with_results"] == 0


def test_coverage_no_active_model_is_all_zero_predicted(client, session):
    seed_model(session, model_version="m-cand", adoption=AdoptionStatus.CANDIDATE)
    seed_race(session, race_id="200806010101", race_date=_D1, horses={
        1: {"win": 0.4, "odds": 2.0},
    }, model_version="m-cand")
    body = client.get(f"/api/v1/coverage?date_from={_D1}&date_to={_D1}").json()
    assert body["active_model_version"] is None
    assert body["days"][0]["n_predicted_active"] == 0


def test_coverage_range_guards(client, session):
    r = client.get("/api/v1/coverage?date_from=2008-06-02&date_to=2008-06-01")
    assert r.status_code == 422 and r.json()["code"] == "invalid_range"
    r = client.get("/api/v1/coverage?date_from=2007-01-01&date_to=2009-01-01")
    assert r.status_code == 422 and r.json()["code"] == "range_too_wide"


_ROI_DAY = datetime.date(2026, 9, 27)
_FIELD = {1: {"odds": 2.5}, 2: {"odds": 4.0}, 3: {"odds": 8.0}}
_EV = {1: {"win_prob": 0.4, "odds_used": 2.5}, 2: {"win_prob": 0.3, "odds_used": 4.0},
       3: {"win_prob": 0.15, "odds_used": 8.0}}
_QUOTE_LV = "mev-v1;features=x;oddsobs=quote"
_PROXY_LV = "mev-v1;features=x;oddsobs=proxy"


def test_coverage_roi_columns_count_stored_rows_by_state(client, session):
    """B11: computed / quote / mix columns, nested, from the stored rows of the DISPLAYED version
    only — the race page's current-odds conditions do not enter."""
    seed_model(session)
    for n in range(1, 6):
        seed_race(session, race_id=f"20260927050{n}", race_date=_ROI_DAY, venue_code="05",
                  race_number=n, horses=_FIELD)
    # race 1: quote rows, 2026 booster → computed + quote + mix
    seed_market_ev(session, race_id="202609270501", horses=_EV, logic_version=_QUOTE_LV,
                   booster="ensemble_2026.json")
    # race 2: proxy rows (one row proxy is enough to fail "every row") → computed + mix only
    seed_market_ev(session, race_id="202609270502", horses=_EV, logic_version=_PROXY_LV,
                   booster="ensemble_2026.json")
    # race 3: quote rows but the 2025 booster → mix unavailable (model_mismatch on the race page)
    seed_market_ev(session, race_id="202609270503", horses=_EV, logic_version=_QUOTE_LV,
                   booster="ensemble_2025.json")
    # race 4: rows of the single-seed version only → not counted at all
    seed_market_ev(session, race_id="202609270504", horses=_EV, logic_version=_QUOTE_LV,
                   model_version=SINGLE_SEED_MODEL_VERSION, booster="ensemble_2026.json")
    # race 5: no rows
    body = client.get(f"/api/v1/coverage?date_from={_ROI_DAY}&date_to={_ROI_DAY}").json()
    (day,) = body["days"]
    assert day["n_races"] == 5
    assert day["n_market_ev_computed"] == 3
    assert day["n_market_ev_quote"] == 2
    assert day["n_market_ev_mix_available"] == 2
    # the legacy column is untouched by ROI rows (seed_race wrote an m-active run for each race)
    assert day["n_predicted_active"] == 5


def test_coverage_roi_columns_quote_token_is_whole_token(client, session):
    """``oddsobs=quote`` must be a whole ``;`` token; a substring look-alike does not count."""
    seed_model(session)
    seed_race(session, race_id="202609270501", race_date=_ROI_DAY, horses=_FIELD)
    seed_market_ev(session, race_id="202609270501", horses=_EV,
                   logic_version="mev-v1;oddsobs=quoteless", booster="ensemble_2026.json")
    body = client.get(f"/api/v1/coverage?date_from={_ROI_DAY}&date_to={_ROI_DAY}").json()
    (day,) = body["days"]
    assert day["n_market_ev_computed"] == 1 and day["n_market_ev_quote"] == 0


def _job(session, *, job_type, status, error=None):
    j = IngestionJob(job_type=job_type, status=status, source="netkeiba",
                     error_message=error)
    session.add(j)
    session.commit()
    return j


def test_jobs_list_filters_and_order(client, session):
    _job(session, job_type="refresh_race", status="succeeded")
    _job(session, job_type="predict", status="failed", error="boom")
    _job(session, job_type="predict", status="succeeded")

    items = client.get("/api/v1/jobs").json()["items"]
    assert len(items) == 3
    # newest first (created_at DESC)
    assert items[0]["job_type"] == "predict" and items[0]["status"] == "succeeded"

    failed = client.get("/api/v1/jobs?status=failed").json()["items"]
    assert len(failed) == 1 and failed[0]["error_message"] == "boom"

    predict = client.get("/api/v1/jobs?job_type=predict").json()["items"]
    assert len(predict) == 2

    # unknown filter value → empty, not an error
    r = client.get("/api/v1/jobs?status=nonsense")
    assert r.status_code == 200 and r.json()["items"] == []

    # limit is validated (cap 200)
    assert client.get("/api/v1/jobs?limit=500").status_code == 422
    assert len(client.get("/api/v1/jobs?limit=1").json()["items"]) == 1
