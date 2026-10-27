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
from horseracing_db.models import (
    Horse,
    OfficialWinPayout,
    PurchaseRecord,
    Race,
    RaceHorse,
    RaceResult,
)

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


def _payout(session, race_id, number, payout_yen):
    session.add(OfficialWinPayout(
        race_id=race_id, horse_number=number, payout_yen=payout_yen,
        source="netkeiba_result", observed_at=_TS, html_sha256="a" * 64,
    ))


def _records(client):
    response = client.get("/api/v1/purchase-records",
                          params={"from": "2026-06-01", "to": "2026-06-30"})
    assert response.status_code == 200
    return response.json()["records"]


def _comparison(client):
    response = client.get("/api/v1/purchase-comparison",
                          params={"from": "2026-06-01", "to": "2026-06-30"})
    assert response.status_code == 200
    return response.json()


def _win_bets(numbers):
    return [{"bet_type": "win", "selection": [n], "amount_yen": 100} for n in numbers]


def _seed_compact_record(session, *, results, payouts, numbers=(1, 2, 3)):
    _seed_race(session, R1, 1, results=results, odds_by_number=dict.fromkeys(numbers, 99.9))
    for number, payout in payouts.items():
        _payout(session, R1, number, payout)
    bets = _win_bets(numbers)
    _record(session, R1, kind="as_presented", bets=bets,
            snapshot={"bets": [{**b, "odds_used": 999.9} for b in bets]})
    session.commit()


def _seed_all(session):
    odds = {1: 12.0, 2: 8.0, 3: 6.0, 4: 30.0, 5: 4.5, 6: 50.0, 7: 9.0, 8: 20.0}
    # R1: official payout is 600, distinct from current odds 9.0 and snapshot odds 5.8.
    _seed_race(session, R1, 1, results={3: 1, 5: 2, 7: 3, 1: 4, 2: 5, 4: 6, 6: 7, 8: 8},
               odds_by_number={**odds, 3: 9.0})
    # R2: pending (no results yet)
    _seed_race(session, R2, 2, results=None, odds_by_number=odds)
    # R3: settled — trio {3,5,7} hits, no exotic dividend row -> estimated settlement
    _seed_race(session, R3, 3, results={3: 1, 5: 2, 7: 3, 1: 4, 2: 5, 4: 6, 6: 7, 8: 8},
               odds_by_number=odds)
    # R4: held but never recorded (coverage denominator)
    _seed_race(session, R4, 4, results={1: 1, 2: 2, 3: 3, 5: 4, 4: 5, 6: 6, 7: 7, 8: 8},
               odds_by_number=odds)

    _payout(session, R1, 3, 600)
    _payout(session, R3, 3, 600)
    _payout(session, R4, 1, 1200)

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
    assert bets[(3,)]["payout_yen"] == pytest.approx(500 / 100 * 600)   # official payout, neither odds column
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

    # actual line: R1 net = 500/100*600-500 - 300 = +2200; R3 net = est_payout - 100 > 0
    actual = {p["race_id"]: p for p in body["series"]["actual"]}
    assert actual[R1]["net_yen"] == pytest.approx(2200.0)
    assert actual[R3]["net_yen"] == pytest.approx(actual[R3]["cumulative_net_yen"] - 2200.0)
    assert body["cumulative"]["actual"] == pytest.approx(
        actual[R3]["cumulative_net_yen"])
    assert body["cumulative"]["no_bet"] == 0.0
    assert body["cumulative"]["diff_actual_vs_no_bet"] == pytest.approx(
        body["cumulative"]["actual"])

    # policy line: frozen choices/stakes, official payout -> 200/100*600-200 = +1000; R3 point is null (unknown)
    policy = {p["race_id"]: p for p in body["series"]["policy"]}
    assert policy[R1]["net_yen"] == pytest.approx(1000.0)
    assert policy[R3]["net_yen"] is None
    assert body["cumulative"]["policy"] == pytest.approx(1000.0)
    assert body["cumulative"]["diff_actual_vs_policy"] == pytest.approx(
        body["cumulative"]["actual"] - 1000.0)

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


def test_dead_heat_uses_each_winners_official_payout_for_actual_and_policy(client, session):
    _seed_compact_record(session, results={5: 1, 15: 1, 3: 3},
                         payouts={5: 1340, 15: 1960}, numbers=(5, 15, 3))
    record = _records(client)[0]
    by_number = {b["selection"][0]: b for b in record["bets"]}
    assert by_number[5]["payout_yen"] == 1340
    assert by_number[15]["payout_yen"] == 1960
    assert by_number[3]["payout_yen"] == 0
    assert all(b["status"] == "settled_real" and not b["is_estimated"]
               for b in record["bets"])
    comparison = _comparison(client)
    assert comparison["series"]["actual"][0]["net_yen"] == 3000
    assert comparison["series"]["policy"][0]["net_yen"] == 3000
    assert comparison["cumulative"]["diff_actual_vs_policy"] == 0


def test_money_back_win_is_an_official_hit_not_a_refund(client, session):
    _seed_compact_record(session, results={1: 1, 2: 2, 3: 3}, payouts={1: 100})
    winner = _records(client)[0]["bets"][0]
    assert winner["status"] == "settled_real"
    assert winner["hit"] is True and winner["payout_yen"] == 100
    comparison = _comparison(client)
    assert comparison["cumulative"]["actual"] == -200
    assert comparison["cumulative"]["policy"] == -200


@pytest.mark.parametrize(("results", "payouts", "reason"), [
    ({1: 1, 2: 2, 3: 3}, {}, "win_payout_missing"),
    ({1: 1, 2: 1, 3: 3}, {1: 1340}, "win_payout_inconsistent"),
    ({1: 1, 2: 2, 3: 3}, {3: 100}, "win_payout_inconsistent"),
])
def test_missing_or_inconsistent_payout_leaves_entire_race_open(
    client, session, results, payouts, reason,
):
    _seed_compact_record(session, results=results, payouts=payouts)
    # Winners, known losers and missing dead-heat dividends must use the same eligibility gate.
    bets = _records(client)[0]["bets"]
    assert len(bets) == 3
    assert all(b["status"] == "unsettleable" and b["settlement_reason"] == reason for b in bets)
    assert all(b["hit"] is None and b["payout_yen"] is None for b in bets)
    comparison = _comparison(client)
    assert comparison["series"]["actual"] == comparison["series"]["policy"] == []
    assert comparison["pending"] == {"n_races": 1, "n_bets": 3, "amount_yen": 300}
    assert comparison["cumulative"]["actual"] == comparison["cumulative"]["policy"] == 0
    assert comparison["cumulative"]["diff_actual_vs_policy"] is None
    assert comparison["n_estimated_settlements"] == 0


def test_partial_results_are_not_losses_or_refunds_and_later_results_settle_on_next_get(
    client, session,
):
    _seed_compact_record(session, results={1: 1, 2: 2}, payouts={1: 600})
    first = _records(client)[0]
    # Includes the known winner, a known loser and a started horse whose result is missing.
    assert all(b["status"] == "pending" and b["settlement_reason"] == "results_incomplete"
               and b["hit"] is None and b["payout_yen"] is None for b in first["bets"])
    assert _comparison(client)["pending"] == {"n_races": 1, "n_bets": 3, "amount_yen": 300}

    session.add(RaceResult(race_id=R1, horse_id=f"H{R1[-4:]}03", finish_order=3,
                           result_status=ResultStatus.FINISHED))
    session.commit()
    later = _records(client)[0]
    assert later["record_id"] == first["record_id"]
    assert [b["payout_yen"] for b in later["bets"]] == [600, 0, 0]
    assert all(b["status"] == "settled_real" for b in later["bets"])
    comparison = _comparison(client)
    assert comparison["pending"] == {"n_races": 0, "n_bets": 0, "amount_yen": 0}
    assert comparison["cumulative"]["actual"] == comparison["cumulative"]["policy"] == 300


def test_later_official_payout_and_correction_revalue_existing_records(client, session):
    _seed_compact_record(session, results={1: 1, 2: 2, 3: 3}, payouts={})
    first = _records(client)[0]
    assert first["bets"][0]["settlement_reason"] == "win_payout_missing"
    assert _comparison(client)["series"]["actual"] == []

    _payout(session, R1, 1, 600)
    session.commit()
    second = _records(client)[0]
    assert second["record_id"] == first["record_id"]
    assert second["bets"][0]["payout_yen"] == 600
    comparison = _comparison(client)
    assert comparison["cumulative"]["actual"] == comparison["cumulative"]["policy"] == 300

    session.get(OfficialWinPayout, (R1, 1)).payout_yen = 700
    session.commit()
    third = _records(client)[0]
    assert third["record_id"] == first["record_id"]
    assert third["bets"][0]["payout_yen"] == 700
    assert _comparison(client)["cumulative"]["actual"] == 400


@pytest.mark.parametrize("entry_status", [EntryStatus.CANCELLED, EntryStatus.EXCLUDED])
@pytest.mark.parametrize("results", [None, {1: 1, 2: 2}])
def test_only_explicit_nonstarters_are_refunded_even_before_results(
    client, session, entry_status, results,
):
    _seed_race(session, R1, 1, results=results, odds_by_number={1: 4, 2: 8, 3: 12})
    session.flush()
    session.get(RaceHorse, (R1, f"H{R1[-4:]}03")).entry_status = entry_status
    bets = _win_bets([3])
    _record(session, R1, kind="as_presented", bets=bets, snapshot={"bets": bets})
    session.commit()
    bet = _records(client)[0]["bets"][0]
    assert bet["status"] == "refunded" and bet["hit"] is None and bet["payout_yen"] == 100
    comparison = _comparison(client)
    assert comparison["cumulative"]["actual"] == comparison["cumulative"]["policy"] == 0
    assert comparison["pending"]["n_bets"] == 0


def test_unknown_selection_is_not_refunded(client, session):
    _seed_race(session, R1, 1, results={1: 1, 2: 2}, odds_by_number={1: 4, 2: 8})
    _payout(session, R1, 1, 400)
    _record(session, R1, kind="freeform", bets=_win_bets([9]))
    session.commit()
    bet = _records(client)[0]["bets"][0]
    assert bet["status"] == "unsettleable"
    assert bet["settlement_reason"] == "selection_unknown"
    assert bet["payout_yen"] is None and bet["hit"] is None
    assert _comparison(client)["pending"] == {"n_races": 1, "n_bets": 1, "amount_yen": 100}


def test_stopped_and_disqualified_horses_have_results_and_are_settled_misses(client, session):
    _seed_compact_record(session, results={1: 1, 2: 2, 3: 3}, payouts={1: 500})
    for n, status in [(2, ResultStatus.STOPPED), (3, ResultStatus.DISQUALIFIED)]:
        result = session.get(RaceResult, (R1, f"H{R1[-4:]}{n:02d}"))
        result.finish_order = None
        result.result_status = status
    session.commit()
    bets = _records(client)[0]["bets"]
    assert [b["payout_yen"] for b in bets] == [500, 0, 0]
    assert [b["hit"] for b in bets] == [True, False, False]
    assert all(b["status"] == "settled_real" for b in bets)
    assert _comparison(client)["cumulative"]["actual"] == 200


@pytest.mark.parametrize(("defect", "reason"), [
    ("duplicate_number", "field_inconsistent"),
    ("orphan_result", "results_inconsistent"),
    ("winner_absent", "results_inconsistent"),
])
def test_invalid_field_or_results_prevent_real_settlement(client, session, defect, reason):
    _seed_compact_record(session, results={1: 1, 2: 2, 3: 3}, payouts={1: 500})
    if defect == "duplicate_number":
        session.get(RaceHorse, (R1, f"H{R1[-4:]}03")).horse_number = 2
    elif defect == "orphan_result":
        # Result has a valid Horse FK but no RaceHorse. A RaceHorse-rooted join would hide it.
        session.add(Horse(horse_id="orphan", horse_name="orphan"))
        session.flush()
        session.add(RaceResult(race_id=R1, horse_id="orphan", finish_order=None,
                               result_status=ResultStatus.STOPPED))
    else:
        session.get(RaceResult, (R1, f"H{R1[-4:]}01")).finish_order = 2
    session.commit()
    bets = _records(client)[0]["bets"]
    assert all(b["status"] == "unsettleable" and b["settlement_reason"] == reason
               and b["hit"] is None and b["payout_yen"] is None for b in bets)
    comparison = _comparison(client)
    assert comparison["series"]["actual"] == comparison["series"]["policy"] == []
    assert comparison["pending"] == {"n_races": 1, "n_bets": 3, "amount_yen": 300}
