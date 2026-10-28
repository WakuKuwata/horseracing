"""B8 (2026-10-28): GET /api/v1/market-ev/prospective — the 期待回収率 columns' prospective record.

Covers the empty record, that an eligible race (genuine quote, computed before post with the
result pending, one snapshot, consistent official payout) is tallied with 100 yen on every horse
strictly above each threshold and settled at the official payout, that the funnel classifies each
failing race once under its first failing rule and sums to the candidates, the mixed column's
start date and unavailable-mix accounting, and that the by-day table folds to the column totals.
"""

from __future__ import annotations

import datetime
import math
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import OfficialWinPayout, Race, RaceHorse
from horseracing_eval import attention_rules as ar
from horseracing_eval import market_mix
from sqlalchemy import update as sa_update

from horseracing_api.market_ev_prospective import COLUMN_START_DATES, QUOTE_TOKEN
from tests._synth import seed_market_ev, seed_model, seed_race

pytestmark = pytest.mark.integration

_URL = "/api/v1/market-ev/prospective"
_UTC = datetime.UTC
_DAY = datetime.date(2026, 10, 25)
_LV = f"mev-v1;features=test;{QUOTE_TOKEN}"
_FIELD = {1: {"odds": 2.5}, 2: {"odds": 4.0}, 3: {"odds": 8.0}, 4: {"odds": 15.0}}
# 0.36×2.5=0.9 / 0.25×4.0=1.0 / 0.16×8.0=1.28 / 0.09×15.0=1.35 → horses 3 and 4 exceed 1.2
_EV = {
    1: {"win_prob": 0.36, "odds_used": 2.5},
    2: {"win_prob": 0.25, "odds_used": 4.0},
    3: {"win_prob": 0.16, "odds_used": 8.0},
    4: {"win_prob": 0.09, "odds_used": 15.0},
}
_FUNNEL_CLASSES = (
    "jump_race", "not_quote", "result_known_at_compute", "not_single_snapshot",
    "post_time_unknown", "observed_after_compute", "computed_after_post", "field_mismatch",
    "invalid_values", "pending_result", "payout_race_missing", "payout_inconsistent", "eligible",
)


def _post(day: datetime.date, hour: int = 6) -> datetime.datetime:
    return datetime.datetime(day.year, day.month, day.day, hour, 0, tzinfo=_UTC)


def _race(session, race_id, *, day=_DAY, number=1, post_time="auto", field=None,
          winner: int | None = 4, payouts: dict[int, int] | None = "auto", track_type="芝"):
    """A started race with (by default) horse 4 winning at an official payout of 1,500 yen."""
    horses = {n: dict(h) for n, h in (field or _FIELD).items()}
    if winner is not None:
        for n in horses:
            horses[n]["finish"] = 1 if n == winner else (2 if n == 1 else n)
    seed_race(session, race_id=race_id, race_date=day, venue_code="05", race_number=number,
              horses=horses)
    session.execute(sa_update(Race).where(Race.race_id == race_id).values(
        post_time=(_post(day) if post_time == "auto" else post_time), track_type=track_type))
    if payouts == "auto":
        payouts = {winner: 1500} if winner is not None else None
    for n, yen in (payouts or {}).items():
        session.add(OfficialWinPayout(race_id=race_id, horse_number=n, payout_yen=yen,
                                      observed_at=_post(day) + datetime.timedelta(hours=1)))
    session.commit()


def _ev(session, race_id, *, day=_DAY, ev=None, computed=None, observed=None, booster="ensemble_2026.json",
        logic_version=_LV, **kw):
    computed = computed or (_post(day) - datetime.timedelta(minutes=20))
    observed = observed or (computed - datetime.timedelta(minutes=2))
    return seed_market_ev(session, race_id=race_id, horses=ev or _EV, logic_version=logic_version,
                          computed_at=computed, odds_observed_at=observed, booster=booster, **kw)


def _funnel_sum(funnel: dict) -> int:
    return sum(funnel[k] for k in _FUNNEL_CLASSES)


def test_empty_record_has_zero_counts_and_no_rates(client):
    resp = client.get(_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"model_version", "quote_token", "stake_yen_per_bet", "settlement",
                         "funnel", "columns", "by_day", "last_eligible_race_date"}
    assert body["quote_token"] == "oddsobs=quote"
    assert body["stake_yen_per_bet"] == 100 and body["settlement"] == "official_win_payout"
    assert body["funnel"]["n_candidate_races"] == 0 and _funnel_sum(body["funnel"]) == 0
    assert body["funnel"]["since"] == min(COLUMN_START_DATES.values()).isoformat()
    assert [c["column"] for c in body["columns"]] == ["expected_return", "expected_return_mix"]
    er, mix = body["columns"]
    assert (er["threshold"], mix["threshold"]) == (1.2, 1.0)
    assert er["start_date"] == ar.PROSPECTIVE_START_DATE.isoformat()
    assert mix["start_date"] == market_mix.MIX_PROSPECTIVE_START_DATE.isoformat()
    for c in (er, mix):
        assert c["n_bets"] == 0 and c["recovery_rate"] is None and c["ci"] is None
        assert c["min_bets"] == ar.CHECKPOINTS[0] and c["remaining_to_min"] == c["min_bets"]
        assert c["valuation_basis"] == "official_win_payout" and c["is_pseudo_selection"] is True
        assert c["bootstrap"]["seed"] == ar.BOOTSTRAP["seed"]
    assert body["by_day"] == [] and body["last_eligible_race_date"] is None


def test_eligible_race_is_tallied_at_the_official_payout(client, session):
    seed_model(session)
    _race(session, "202610250501")
    _ev(session, "202610250501")

    body = client.get(_URL).json()
    assert body["funnel"]["eligible"] == 1 and body["funnel"]["n_candidate_races"] == 1
    er, mix = body["columns"]
    # horses 3 and 4 are strictly above 1.2 → 2 bets × 100 yen; horse 4 won at 1,500 yen
    assert (er["n_races"], er["n_races_with_bets"], er["n_bets"], er["n_hits"]) == (1, 1, 2, 1)
    assert (er["stake_yen"], er["return_yen"]) == (200, 1500)
    assert er["recovery_rate"] == pytest.approx(7.5)
    assert er["ci"] is None and er["n_days"] == 1  # one race day: no interval
    assert er["remaining_to_min"] == er["min_bets"] - 2
    # the mixed column folds the race page's own flags
    page = client.get("/api/v1/races/202610250501/market-ev").json()
    assert page["mix"]["status"] == "available"
    flagged = [h for h in page["horses"] if h["exceeds_mix_threshold"]]
    assert mix["n_races"] == 1 and mix["n_races_mix_unavailable"] == 0
    assert mix["n_bets"] == len(flagged)
    assert mix["return_yen"] == sum(1500 for h in flagged if h["horse_number"] == 4)
    assert mix["stake_yen"] == 100 * len(flagged)
    assert body["last_eligible_race_date"] == "2026-10-25"
    assert body["by_day"] == [{
        "race_date": "2026-10-25", "n_eligible": 1, "n_pending_result": 0,
        "expected_return": {"n_races": 1, "n_bets": 2, "n_hits": 1, "stake_yen": 200,
                            "return_yen": 1500},
        "expected_return_mix": {"n_races": 1, "n_bets": len(flagged),
                                "n_hits": sum(1 for h in flagged if h["horse_number"] == 4),
                                "stake_yen": 100 * len(flagged), "return_yen": mix["return_yen"]},
    }]
    assert "win_prob" not in client.get(_URL).text


def test_funnel_counts_each_failing_race_once_under_its_first_failing_rule(client, session):
    seed_model(session)
    day = _DAY
    # eligible
    _race(session, "202610250501", number=1)
    _ev(session, "202610250501")
    # before the earliest start date (quote etc. all fine): not a candidate at all
    early = min(COLUMN_START_DATES.values()) - datetime.timedelta(days=1)
    _race(session, "202610040501", day=early, number=1)
    _ev(session, "202610040501", day=early)
    # proxy rows (140 fallback) → not_quote
    _race(session, "202610250502", number=2)
    _ev(session, "202610250502", logic_version="mev-v1;features=test;oddsobs=proxy")
    # computed after post time (the usual overwrite by a later recompute)
    _race(session, "202610250503", number=3)
    _ev(session, "202610250503", computed=_post(day) + datetime.timedelta(minutes=30))
    # computed at a known result → result_known_at_compute
    _race(session, "202610250504", number=4)
    _ev(session, "202610250504", result_pending=False)
    # no result yet → pending_result (精算待ち)
    _race(session, "202610250505", number=5, winner=None, payouts=None)
    _ev(session, "202610250505")
    # result but no payout row → payout_race_missing
    _race(session, "202610250506", number=6, payouts=None)
    _ev(session, "202610250506")
    # payout names a non-winner → payout_inconsistent
    _race(session, "202610250507", number=7, payouts={4: 1500, 2: 300})
    _ev(session, "202610250507")
    # a horse scratched after the compute → field_mismatch
    _race(session, "202610250508", number=8)
    _ev(session, "202610250508")
    session.execute(sa_update(RaceHorse).where(RaceHorse.race_id == "202610250508",
                                               RaceHorse.horse_id == "H2")
                    .values(entry_status=EntryStatus.CANCELLED))
    # jump race
    _race(session, "202610250509", number=9, track_type="障")
    _ev(session, "202610250509")
    # observation after the compute
    _race(session, "202610250510", number=10)
    _ev(session, "202610250510", observed=_post(day) - datetime.timedelta(minutes=10))
    session.commit()

    body = client.get(_URL).json()
    f = body["funnel"]
    assert f["n_candidate_races"] == 10 and _funnel_sum(f) == 10
    assert f["eligible"] == 1
    assert f["not_quote"] == 1 and f["computed_after_post"] == 1
    assert f["result_known_at_compute"] == 1 and f["pending_result"] == 1
    assert f["payout_race_missing"] == 1 and f["payout_inconsistent"] == 1
    assert f["field_mismatch"] == 1 and f["jump_race"] == 1 and f["observed_after_compute"] == 1
    assert body["columns"][0]["n_races"] == 1
    day_row = next(d for d in body["by_day"] if d["race_date"] == "2026-10-25")
    assert day_row["n_eligible"] == 1 and day_row["n_pending_result"] == 1
    assert all(d["race_date"] != early.isoformat() for d in body["by_day"])


def test_mixed_column_start_date_and_unavailable_mix(client, session):
    seed_model(session)
    # between the two start dates: counts for expected_return only
    mid = COLUMN_START_DATES["expected_return_mix"] - datetime.timedelta(days=1)
    assert mid >= COLUMN_START_DATES["expected_return"]
    _race(session, "202610180501", day=mid, number=1)
    _ev(session, "202610180501", day=mid)
    # after both start dates but with a booster the mixture was not fitted on
    _race(session, "202610250501", number=1)
    _ev(session, "202610250501", booster="model_2026.txt")

    body = client.get(_URL).json()
    assert body["funnel"]["eligible"] == 2
    er, mix = body["columns"]
    assert er["n_races"] == 2 and er["n_bets"] == 4 and er["n_hits"] == 2
    assert er["recovery_rate"] == pytest.approx(3000 / 400)
    assert er["n_days"] == 2 and er["ci"] is not None and er["p_one_sided"] is not None
    # the race after the mixed start date is an eligible race of the column with no bet
    assert mix["n_races"] == 1 and mix["n_races_mix_unavailable"] == 1
    assert mix["n_races_with_bets"] == 0 and mix["n_bets"] == 0
    assert mix["recovery_rate"] is None
    totals = {c: {"n_bets": 0, "return_yen": 0} for c in ("expected_return", "expected_return_mix")}
    for d in body["by_day"]:
        for c in totals:
            totals[c]["n_bets"] += d[c]["n_bets"]
            totals[c]["return_yen"] += d[c]["return_yen"]
    assert totals["expected_return"] == {"n_bets": 4, "return_yen": 3000}
    assert totals["expected_return_mix"] == {"n_bets": 0, "return_yen": 0}


def test_dead_heat_pays_each_winner_its_official_amount(client, session):
    seed_model(session)
    field = {n: dict(h) for n, h in _FIELD.items()}
    seed_race(session, race_id="202610250501", race_date=_DAY, venue_code="05", race_number=1,
              horses={1: {**field[1], "finish": 2}, 2: {**field[2], "finish": 3},
                      3: {**field[3], "finish": 1}, 4: {**field[4], "finish": 1}})
    session.execute(sa_update(Race).where(Race.race_id == "202610250501")
                    .values(post_time=_post(_DAY), track_type="ダ"))
    for n, yen in {3: 800, 4: 1400}.items():
        session.add(OfficialWinPayout(race_id="202610250501", horse_number=n, payout_yen=yen,
                                      observed_at=_post(_DAY)))
    session.commit()
    _ev(session, "202610250501")

    er = client.get(_URL).json()["columns"][0]
    assert (er["n_bets"], er["n_hits"], er["return_yen"]) == (2, 2, 2200)
    assert math.isclose(er["recovery_rate"], 11.0)
    assert Decimal(str(er["recovery_rate"])) == Decimal("11")
