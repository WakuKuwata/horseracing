"""Feature 106 T011/T012: GET /purchase-records and /purchase-comparison.

Value-level assertions on purpose — the contract forbids shape-only checks because a splat-null
regression (075) passes those. Settlement, fold, policy counterfactual and coverage are all
computed at read time from seeded append-only rows.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, PurchaseRecord, Race, RaceHorse, RaceResult

pytestmark = pytest.mark.integration

_D = datetime.date
_TS = datetime.datetime(2026, 6, 1, 10, 0, tzinfo=datetime.UTC)

R1, R2, R3, R4 = "202606010101", "202606010102", "202606010103", "202606010104"


def _seed_race(session, race_id, day, *, results, odds_by_number):
    session.merge(Race(race_id=race_id, race_number=int(race_id[-2:]),
                       race_date=_D(2026, 6, day), venue_code="05"))
    for n, odds in odds_by_number.items():
        hid = f"H{race_id[-4:]}{n:02d}"
        session.merge(Horse(horse_id=hid, horse_name=hid))
        session.flush()
        session.merge(RaceHorse(race_id=race_id, horse_id=hid, horse_number=n,
                                entry_status=EntryStatus.STARTED, odds=odds))
        if results and n in results:
            session.merge(RaceResult(race_id=race_id, horse_id=hid, finish_order=results[n],
                                     result_status=ResultStatus.FINISHED))


def _record(session, race_id, *, kind, bets, snapshot=None, pending=True,
            corrects=None, note=None, ts=_TS):
    rid = uuid.uuid4()
    session.add(PurchaseRecord(
        purchase_record_id=rid, race_id=race_id, kind=kind, bets=bets,
        presented_snapshot=snapshot, corrects_record_id=corrects,
        result_pending_at_record=pending, pending_basis_at=ts,
        client_request_id=str(uuid.uuid4()), payload_hash="x", note=note, recorded_at=ts))
    return rid


def _seed_all(session):
    odds = {1: 12.0, 2: 8.0, 3: 6.0, 4: 30.0, 5: 4.5, 6: 50.0, 7: 9.0, 8: 20.0}
    # R1: settled race — win #3 wins at 6.0
    _seed_race(session, R1, 1, results={3: 1, 5: 2, 7: 3, 1: 4, 2: 5, 4: 6, 6: 7, 8: 8},
               odds_by_number=odds)
    # R2: pending (no results yet)
    _seed_race(session, R2, 2, results=None, odds_by_number=odds)
    # R3: settled — trio {3,5,7} hits, no exotic dividend row -> estimated settlement
    _seed_race(session, R3, 3, results={3: 1, 5: 2, 7: 3, 1: 4, 2: 5, 4: 6, 6: 7, 8: 8},
               odds_by_number=odds)
    # R4: held but never recorded (coverage denominator)
    _seed_race(session, R4, 4, results={1: 1, 2: 2, 3: 3, 5: 4, 4: 5, 6: 6, 7: 7, 8: 8},
               odds_by_number=odds)

    snap = {"bets": [{"bet_type": "win", "selection": [3], "amount_yen": 200,
                      "odds_used": 5.8}],
            "win_policy": "odds_cap_21", "prediction_run_id": None,
            "snapshot_schema_version": 1}
    base1 = _record(session, R1, kind="as_presented", snapshot=snap,
                    bets=[{"bet_type": "win", "selection": [3], "amount_yen": 500,
                           "odds_used": 5.8}])
    # correction: also bought win #5 for 300 (replaces the bet list)
    _record(session, R1, kind="correction", corrects=base1, note="追加分を反映",
            ts=_TS + datetime.timedelta(minutes=5),
            bets=[{"bet_type": "win", "selection": [3], "amount_yen": 500, "odds_used": 5.8},
                  {"bet_type": "win", "selection": [5], "amount_yen": 300, "odds_used": 4.4}])
    _record(session, R2, kind="freeform",
            bets=[{"bet_type": "win", "selection": [1], "amount_yen": 400}])
    _record(session, R3, kind="freeform", pending=False,   # post-hoc entry
            bets=[{"bet_type": "trio", "selection": [3, 5, 7], "amount_yen": 100}])
    session.commit()


def test_purchase_records_values(client, session):
    _seed_all(session)
    body = client.get("/api/v1/purchase-records",
                      params={"from": "2026-06-01", "to": "2026-06-30"}).json()
    assert body["n_races_recorded"] == 3
    by_race = {r["race_id"]: r for r in body["records"]}

    r1 = by_race[R1]
    assert r1["kind"] == "as_presented" and r1["n_corrections"] == 1
    assert r1["result_pending_at_record"] is True and r1["note"] == "追加分を反映"
    bets = {tuple(b["selection"]): b for b in r1["bets"]}
    assert bets[(3,)]["status"] == "settled_real" and bets[(3,)]["hit"] is True
    assert bets[(3,)]["payout_yen"] == pytest.approx(500 * 6.0)   # official odds, not frozen
    assert bets[(3,)]["is_estimated"] is False
    assert bets[(5,)]["hit"] is False and bets[(5,)]["payout_yen"] == 0.0

    r2 = by_race[R2]
    assert r2["bets"][0]["status"] == "pending"
    assert r2["bets"][0]["hit"] is None and r2["bets"][0]["payout_yen"] is None

    r3 = by_race[R3]
    assert r3["result_pending_at_record"] is False
    trio = r3["bets"][0]
    assert trio["status"] == "settled_estimated" and trio["is_estimated"] is True
    assert trio["hit"] is True and trio["payout_yen"] > 100  # estimated multiple of stake


def test_purchase_comparison_values(client, session):
    _seed_all(session)
    body = client.get("/api/v1/purchase-comparison",
                      params={"from": "2026-06-01", "to": "2026-06-30"}).json()

    # actual line: R1 net = 500*6.0-500 - 300 = +2200; R3 net = est_payout - 100 > 0
    actual = {p["race_id"]: p for p in body["series"]["actual"]}
    assert actual[R1]["net_yen"] == pytest.approx(2200.0)
    assert actual[R3]["net_yen"] == pytest.approx(actual[R3]["cumulative_net_yen"] - 2200.0)
    assert body["cumulative"]["actual"] == pytest.approx(
        actual[R3]["cumulative_net_yen"])
    assert body["cumulative"]["no_bet"] == 0.0
    assert body["cumulative"]["diff_actual_vs_no_bet"] == pytest.approx(
        body["cumulative"]["actual"])

    # policy line: only R1 has a snapshot -> 200*5.8-200 = +960; R3 point is null (unknown)
    policy = {p["race_id"]: p for p in body["series"]["policy"]}
    assert policy[R1]["net_yen"] == pytest.approx(960.0)
    assert policy[R3]["net_yen"] is None
    assert body["cumulative"]["policy"] == pytest.approx(960.0)
    assert body["cumulative"]["diff_actual_vs_policy"] == pytest.approx(
        body["cumulative"]["actual"] - 960.0)

    # pending: R2 held out of the series and disclosed separately
    assert body["pending"] == {"n_races": 1, "n_bets": 1, "amount_yen": 400}
    assert R2 not in actual and R2 not in policy

    # coverage: 3 recorded / 4 held; R3 was a post-ingestion (post-hoc) entry
    cov = body["coverage_rate"]
    assert cov["n_all_races"] == 4 and cov["n_recorded_races"] == 3
    assert cov["overall"] == pytest.approx(0.75)
    assert cov["pre_ingestion"] == pytest.approx(0.5)
    assert cov["post_ingestion"] == pytest.approx(0.25)

    assert body["n_estimated_settlements"] == 1
    assert body["estimated_amount_yen"] == 100
    assert body["n_post_hoc"] == 1 and body["n_corrections"] == 1
    assert "counterfactual_snapshot" in body["notes"] and "pre_tax" in body["notes"]
    assert "049" in body["estimator_provenance"]


def test_purchase_comparison_scope_and_post_hoc_switches(client, session):
    _seed_all(session)
    win_only = client.get("/api/v1/purchase-comparison",
                          params={"from": "2026-06-01", "to": "2026-06-30",
                                  "scope": "win_only"}).json()
    # R3's trio drops out of the actual line under win_only (symmetric view)
    actual = {p["race_id"]: p for p in win_only["series"]["actual"]}
    assert actual[R3]["net_yen"] == pytest.approx(0.0)
    assert win_only["cumulative"]["actual"] == pytest.approx(2200.0)

    no_post = client.get("/api/v1/purchase-comparison",
                         params={"from": "2026-06-01", "to": "2026-06-30",
                                 "include_post_hoc": "false"}).json()
    ids = {p["race_id"] for p in no_post["series"]["actual"]}
    assert R3 not in ids and no_post["n_post_hoc"] == 1
    assert no_post["cumulative"]["actual"] == pytest.approx(2200.0)


def test_purchase_range_guards(client):
    r = client.get("/api/v1/purchase-comparison",
                   params={"from": "2026-06-30", "to": "2026-06-01"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_range"
    r = client.get("/api/v1/purchase-records",
                   params={"from": "2020-01-01", "to": "2026-06-01"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "range_too_wide"


def test_purchase_empty_window(client, session):
    body = client.get("/api/v1/purchase-comparison",
                      params={"from": "2031-01-01", "to": "2031-01-31"}).json()
    assert body["n_races"] == 0 and body["series"]["actual"] == []
    assert body["coverage_rate"]["overall"] is None  # no races held — not 0.0
    assert body["cumulative"]["diff_actual_vs_policy"] is None


def test_policy_line_null_when_snapshot_amounts_unconvertible(client, session):
    """No budget at presentation time -> snapshot win bets carry amount_yen null.

    The policy point must be null (not computable), never a false verified-zero — dropping
    the bets or zeroing them would claim nothing was presented.
    """
    odds = {1: 12.0, 2: 8.0, 3: 6.0, 4: 30.0, 5: 4.5, 6: 50.0, 7: 9.0, 8: 20.0}
    _seed_race(session, R1, 1, results={3: 1, 5: 2, 7: 3, 1: 4, 2: 5, 4: 6, 6: 7, 8: 8},
               odds_by_number=odds)
    snap = {"bets": [{"bet_type": "win", "selection": [3], "amount_yen": None,
                      "odds_used": 5.8}],
            "win_policy": "odds_cap_21", "prediction_run_id": None,
            "snapshot_schema_version": 1}
    _record(session, R1, kind="skipped_presented", bets=[], snapshot=snap)
    session.commit()

    body = client.get("/api/v1/purchase-comparison",
                      params={"from": "2026-06-01", "to": "2026-06-30"}).json()
    policy = {p["race_id"]: p for p in body["series"]["policy"]}
    assert policy[R1]["net_yen"] is None
    assert body["cumulative"]["diff_actual_vs_policy"] is None
