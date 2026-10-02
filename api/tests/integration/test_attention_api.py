"""Feature 138 (T027): the 注目条件 endpoints on a real database.

GET /races/{race_id}/attention, /attention-rules and /attention/day: typed errors and empty states,
the displayed version shown by /market-ev while two versions coexist, stages that follow the
recorded checkpoint decisions (not the live points), the exclusive tally with its Σ check, the
field digest recomputed over the started ids, the post-ordered day list, the memo key (new pick /
new record / result re-ingest / odds re-ingest) and that every read writes nothing.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import (
    AttentionCheckpoint,
    AttentionPick,
    AttentionRaceScan,
    Horse,
    MarketEvPrediction,
    Race,
    RaceHorse,
    RaceResult,
)
from horseracing_eval import attention_rules as ar
from sqlalchemy import delete, func, select
from sqlalchemy import update as sa_update

from horseracing_api.attention import TALLY_MEMO
from tests._synth import seed_market_ev

pytestmark = pytest.mark.integration

_UTC = datetime.UTC
_START = datetime.date(2026, 10, 1)
_DAY = datetime.date(2026, 10, 3)
_RACE = "202610030511"


@pytest.fixture(autouse=True)
def _fresh_memo():
    # The memo lives in the API process across requests; tests reuse fixed timestamps, so start
    # each test from an empty memo (production keys always move forward).
    TALLY_MEMO.clear()
    yield
    TALLY_MEMO.clear()


@pytest.fixture
def go_live(monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", _START)
    return _START


# --- seeding -------------------------------------------------------------------------------------


def _post(day: datetime.date, number: int) -> datetime.datetime:
    return datetime.datetime(day.year, day.month, day.day, 1, 0, tzinfo=_UTC) + datetime.timedelta(
        minutes=30 * number
    )


def _race(session, race_id, *, day=_DAY, number=11, post_time="auto", horses=6, odds=None,
          statuses=None):
    """A race with started horses H1..Hn (win odds 30.0 unless overridden)."""
    post = _post(day, number) if post_time == "auto" else post_time
    session.merge(Race(race_id=race_id, race_date=day, race_number=number, venue_code="05",
                       post_time=post))
    for n in range(1, horses + 1):
        session.merge(Horse(horse_id=f"H{n}", horse_name=f"馬{n}"))
    session.flush()
    odds = odds or {}
    statuses = statuses or {}
    for n in range(1, horses + 1):
        o = odds.get(n, 30.0)
        session.merge(RaceHorse(
            race_id=race_id, horse_id=f"H{n}", horse_number=n,
            odds=None if o is None else Decimal(str(o)),
            entry_status=statuses.get(n, EntryStatus.STARTED),
        ))
    session.commit()
    return post


def _results(session, race_id, finishes: dict[int, int | None]):
    for n, order in finishes.items():
        session.merge(RaceResult(race_id=race_id, horse_id=f"H{n}", finish_order=order,
                                 result_status=ResultStatus.FINISHED))
    session.commit()


def _started_digest(session, race_id) -> str:
    """The shared eval digest of the STARTED horse ids — what the training writer is specified to
    store (the writer itself is checked in training/tests/integration/test_attention_picks.py)."""
    ids = session.scalars(
        select(RaceHorse.horse_id)
        .where(RaceHorse.race_id == race_id, RaceHorse.entry_status == EntryStatus.STARTED)
    ).all()
    return ar.field_digest(ids)


def _scan(session, race_id, *, computed_at, digest, run_id, n_picks=0, post_time=None):
    session.add(AttentionRaceScan(
        race_id=race_id, rule_set_version=ar.RULE_SET_VERSION, run_id=run_id,
        computed_at=computed_at, post_time=post_time, result_pending_at_compute=True,
        field_digest=digest, n_picks=n_picks,
    ))
    session.commit()


def _pick(session, race_id, n, rule_id, *, computed_at, post_time, digest, run_id=None,
          odds=30.0, ens=1.35, single=1.25, gap=40, observed=None, pending=True):
    pick = AttentionPick(
        pick_id=uuid.uuid4(), race_id=race_id, horse_id=f"H{n}", horse_number=n,
        rule_id=rule_id, rule_set_version=ar.RULE_SET_VERSION, kind="pick",
        ens_expected_return=Decimal(str(ens)), single_expected_return=Decimal(str(single)),
        odds_used=Decimal(str(odds)),
        odds_observed_at=observed or computed_at - datetime.timedelta(minutes=5),
        days_since_last=Decimal(str(gap)), post_time=post_time,
        seconds_to_post=None if post_time is None else int((post_time - computed_at).total_seconds()),
        result_pending_at_compute=pending, field_digest=digest,
        ensemble_model_version=ar.DISPLAYED_MARKET_EV_MODEL_VERSION,
        single_model_version=ar.SINGLE_SEED_MODEL_VERSION,
        logic_version="test;policy=v1", run_id=run_id or uuid.uuid4(),
        selection_policy_version=ar.SELECTION_POLICY_VERSION, computed_at=computed_at,
    )
    session.add(pick)
    session.commit()
    return pick


def _void(session, target: AttentionPick):
    session.add(AttentionPick(
        pick_id=uuid.uuid4(), race_id=target.race_id, horse_id=target.horse_id,
        horse_number=None, rule_id=target.rule_id, rule_set_version=ar.RULE_SET_VERSION,
        kind="void", void_reason="scratched", voids_pick_id=target.pick_id,
        result_pending_at_compute=True, field_digest=target.field_digest,
        ensemble_model_version=ar.DISPLAYED_MARKET_EV_MODEL_VERSION,
        single_model_version=ar.SINGLE_SEED_MODEL_VERSION, logic_version="test;policy=v1",
        run_id=uuid.uuid4(), selection_policy_version=ar.SELECTION_POLICY_VERSION,
    ))
    session.commit()


def _record(session, rule_id, checkpoint, decision, *, last_pick_id, start=_START):
    session.add(AttentionCheckpoint(
        checkpoint_id=uuid.uuid4(), rule_id=rule_id, checkpoint=checkpoint,
        selection_policy_version=ar.SELECTION_POLICY_VERSION,
        rule_set_version=ar.RULE_SET_VERSION, decision=decision, n_counted=checkpoint,
        n_hits=checkpoint // 10, roi_frozen=Decimal("0.9"), ci_low=Decimal("0.7"),
        ci_high=Decimal("1.1"),
        bootstrap={"impl": ar.BOOTSTRAP["impl"], "b": 20000, "seed": 20260905,
                   "block_universe": ar.BOOTSTRAP["block_universe"], "numpy_version": "x"},
        counted_pick_ids_sha256="0" * 64, last_pick_id=last_pick_id,
        settlement_cutoff=datetime.datetime(2026, 12, 1, tzinfo=_UTC),
        prospective_start_date=start, skipped_pending_before_last=0,
    ))
    session.commit()


def _counted_block(session, rule_id, *, n_races, first_day=_DAY, winner_odds=12.0,
                   settle=True, horses=10):
    """``n_races`` races of ``horses`` started horses, every horse judged under ``rule_id``
    (counted once settled): one winner per race at ``winner_odds``, the rest at 30.0 —
    so the frozen ROI is ``winner_odds / horses``. Returns the picks in post order."""
    picks = []
    for i in range(n_races):
        day = first_day + datetime.timedelta(days=i // 12)
        number = i % 12 + 1
        race_id = f"{day:%Y%m%d}06{number:02d}"
        post = _race(session, race_id, day=day, number=number, horses=horses,
                     odds={1: winner_odds})
        digest = _started_digest(session, race_id)
        computed = post - datetime.timedelta(hours=3)
        if session.get(AttentionRaceScan, (race_id, ar.RULE_SET_VERSION)) is None:
            _scan(session, race_id, computed_at=computed, digest=digest, run_id=uuid.uuid4(),
                  post_time=post)
        for n in range(1, horses + 1):
            picks.append(_pick(session, race_id, n, rule_id, computed_at=computed,
                               post_time=post, digest=digest,
                               odds=winner_odds if n == 1 else 30.0, ens=1.25))
        if settle:
            _results(session, race_id, {n: n for n in range(1, horses + 1)})
    return picks


def _rules(client) -> dict[str, dict]:
    resp = client.get("/api/v1/attention-rules")
    assert resp.status_code == 200
    return {r["id"]: r for r in resp.json()["items"]}


def _url(race_id=_RACE):
    return f"/api/v1/races/{race_id}/attention"


# --- typed states --------------------------------------------------------------------------------


def test_invalid_race_id_is_typed_422(client):
    resp = client.get(_url("bad"))
    assert resp.status_code == 422
    assert resp.json() == {
        "status": 422, "code": "invalid_race_id", "detail": "race_id must be 12 digits",
    }


def test_unknown_race_is_typed_404(client):
    resp = client.get(_url("202610039999"))
    assert resp.status_code == 404
    assert resp.json()["code"] == "race_not_found"


def test_race_without_first_computation_is_not_computed(client, session, monkeypatch):
    from horseracing_api.routers import attention as router

    def must_not_load(*_args, **_kwargs):
        raise AssertionError("loaded for a race without a first computation")

    # answered from the entries alone: no memo-key query, tally, picks or market-ev read
    for name in ("rule_tallies", "attention_memo_key", "attention_picks_for_race",
                 "market_ev_rows", "race_has_results"):
        monkeypatch.setattr(router, name, must_not_load)
    _race(session, _RACE)
    assert client.get(_url()).json() == {
        "status": "unavailable", "race_id": _RACE, "reason": "not_computed",
    }


def test_race_without_first_computation_and_missing_odds_is_odds_unavailable(client, session):
    _race(session, _RACE, odds={2: None})
    assert client.get(_url()).json()["reason"] == "odds_unavailable"


def test_first_computation_with_no_judged_horse_lists_every_horse(client, session):
    post = _race(session, _RACE)
    computed = post - datetime.timedelta(hours=3)
    _scan(session, _RACE, computed_at=computed, digest=_started_digest(session, _RACE),
          run_id=uuid.uuid4())
    body = client.get(_url()).json()
    assert body["status"] == "available"
    assert body["rule_set_version"] == ar.RULE_SET_VERSION
    assert [h["horse_number"] for h in body["horses"]] == [1, 2, 3, 4, 5, 6]
    assert all(h["applicable"] == [] and h["chip_rule"] is None for h in body["horses"])


# --- judged / current and the displayed version --------------------------------------------------


def _judged_race(session):
    post = _race(session, _RACE, odds={3: 32.5, 2: 3.0})
    computed = post - datetime.timedelta(hours=3)
    run = uuid.uuid4()
    digest = _started_digest(session, _RACE)
    _scan(session, _RACE, computed_at=computed, digest=digest, run_id=run, n_picks=5)
    for rule_id in ("S1", "S3", "S4", "S5"):
        _pick(session, _RACE, 3, rule_id, computed_at=computed, post_time=post, digest=digest,
              run_id=run, odds=32.5, ens=1.31, single=1.284)
    _pick(session, _RACE, 2, "S5", computed_at=computed, post_time=post, digest=digest,
          run_id=run, odds=3.0, ens=1.0, single=1.3)
    later = post - datetime.timedelta(minutes=42)
    ev = {n: {"win_prob": 0.02, "odds_used": 30.0} for n in range(1, 7)}
    ev[3] = {"win_prob": 0.04, "odds_used": 32.5, "expected_return": "1.246"}
    ev[2] = {"win_prob": 0.4, "odds_used": 3.0}
    ens_run = seed_market_ev(session, race_id=_RACE, horses=ev,
                             model_version=ar.DISPLAYED_MARKET_EV_MODEL_VERSION,
                             computed_at=later, odds_observed_at=later)
    single_ev = {n: {**h, "expected_return": "1.26"} if n == 3 else h for n, h in ev.items()}
    seed_market_ev(session, race_id=_RACE, horses=single_ev,
                   model_version=ar.SINGLE_SEED_MODEL_VERSION,
                   computed_at=later, odds_observed_at=later)
    # one compute run writes both versions with one run_id (seed_market_ev draws its own per call)
    session.execute(
        sa_update(MarketEvPrediction)
        .where(MarketEvPrediction.model_version == ar.SINGLE_SEED_MODEL_VERSION)
        .values(run_id=ens_run)
    )
    session.commit()
    return post, computed, run, later, ens_run


def _ts(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_judged_values_come_from_the_pick_and_current_from_the_displayed_row(client, session):
    post, computed, run, later, ens_run = _judged_race(session)
    resp = client.get(_url())
    assert resp.status_code == 200
    assert "win_prob" not in resp.text
    body = resp.json()
    assert _ts(body["judged_at"]) == computed
    assert _ts(body["post_time"]) == post and body["has_results"] is False
    h3 = next(h for h in body["horses"] if h["horse_number"] == 3)
    assert h3["applicable"] == ["S1", "S3", "S4", "S5"]
    assert h3["chip_rule"] == "S1" and h3["chip_s2"] is False
    assert h3["chip_stage"] == {"stage": "researching", "checkpoint": None,
                                "checkpoint_pending": False}
    assert h3["judged"]["ens_expected_return"] == 1.31
    assert h3["judged"]["single_expected_return"] == 1.284
    assert h3["judged"]["odds"] == 32.5 and h3["judged"]["run_id"] == str(run)
    assert h3["current"]["ens_expected_return"] == 1.246
    assert h3["current"]["single_expected_return"] == 1.26
    assert h3["current"]["run_id"] == str(ens_run)
    assert _ts(h3["current"]["odds_observed_at"]) == later
    assert h3["chip_now"] == "matches"
    assert h3["levels"] == {"backtest": ar.backtest_level(ar.rule("S1")), "prospective": 1,
                            "price_noise": ar.price_noise_level(ar.rule("S1"))}
    h2 = next(h for h in body["horses"] if h["horse_number"] == 2)
    assert h2["applicable"] == ["S5"] and h2["chip_rule"] is None and h2["levels"] is None


def test_single_seed_recompute_moves_neither_current_nor_the_column(client, session):
    _judged_race(session)
    before_attention = client.get(_url()).json()
    before_column = client.get(f"/api/v1/races/{_RACE}/market-ev").json()
    assert before_column["model_version"] == ar.DISPLAYED_MARKET_EV_MODEL_VERSION

    # a later manual single-seed-only recompute replaces the single-seed rows (latest-only table)
    session.execute(delete(MarketEvPrediction).where(
        MarketEvPrediction.model_version == ar.SINGLE_SEED_MODEL_VERSION))
    session.commit()
    much_later = datetime.datetime(2026, 10, 3, 12, 0, tzinfo=_UTC)
    seed_market_ev(session, race_id=_RACE,
                   horses={n: {"win_prob": 0.01, "odds_used": 45.0} for n in range(1, 7)},
                   model_version=ar.SINGLE_SEED_MODEL_VERSION, computed_at=much_later,
                   odds_observed_at=much_later)

    after_column = client.get(f"/api/v1/races/{_RACE}/market-ev").json()
    assert after_column == before_column
    after = client.get(_url()).json()
    h_before = next(h for h in before_attention["horses"] if h["horse_number"] == 3)
    h_after = next(h for h in after["horses"] if h["horse_number"] == 3)
    assert h_before["current"]["single_expected_return"] == 1.26
    assert h_after["current"]["single_expected_return"] is None
    for key in ("ens_expected_return", "odds", "odds_observed_at", "computed_at", "run_id"):
        assert h_after["current"][key] == h_before["current"][key]
    assert h_after["chip_now"] == h_before["chip_now"] == "matches"


def test_api_recomputes_the_digest_over_the_started_ids(client, session):
    # The api recomputes the shared eval digest over the STARTED ids. The stored digest here is
    # built by the test the same way (``_started_digest``), so this checks the api's input set, not
    # that the training writer feeds the same set — that writer is checked in training's own
    # integration test (the api's dependency closure does not include horseracing_training).
    _judged_race(session)
    h3 = next(h for h in client.get(_url()).json()["horses"] if h["horse_number"] == 3)
    assert h3["field_changed_after_pick"] is False

    session.execute(sa_update(RaceHorse).where(RaceHorse.race_id == _RACE,
                                               RaceHorse.horse_id == "H5")
                    .values(entry_status=EntryStatus.CANCELLED))
    session.commit()
    h3 = next(h for h in client.get(_url()).json()["horses"] if h["horse_number"] == 3)
    assert h3["field_changed_after_pick"] is True
    assert h3["chip_rule"] == "S1"            # the pick stays; the audit flag is all that moves
    assert h3["current"] is None and h3["chip_now"] == "unknown"   # 137 state: field_changed


# --- stages --------------------------------------------------------------------------------------


def test_before_go_live_every_pick_is_before_start(client, session, monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", None)
    _counted_block(session, "S3", n_races=2)
    s3 = _rules(client)["S3"]["prospective"]
    assert s3["start_date"] is None
    assert s3["stage"] == "researching" and s3["n_counted"] == 0
    assert s3["counts"]["before_start"] == s3["n_picks_total"] == 20
    assert all(r["prospective"]["stage"] == "researching" for r in _rules(client).values())


def test_observing_with_points_above_par_gives_prospective_level_two(client, session, go_live):
    picks = _counted_block(session, "S3", n_races=12, winner_odds=12.0)     # 120 counted, 120%
    s3 = _rules(client)["S3"]["prospective"]
    assert s3["n_counted"] == 120 and s3["n_hits"] == 12
    assert s3["stage"] == "observing" and s3["checkpoint_pending"] is False
    assert s3["frozen"]["roi"] == pytest.approx(1.2)
    assert s3["next_checkpoint"] == 300 and s3["remaining_to_next"] == 180
    race_id = picks[0].race_id
    h1 = next(h for h in client.get(_url(race_id)).json()["horses"] if h["horse_number"] == 1)
    assert h1["chip_rule"] == "S3" and h1["chip_stage"]["stage"] == "observing"
    assert h1["levels"]["prospective"] == 2


def test_stage_transitions_follow_the_records(client, session, go_live):
    picks = _counted_block(session, "S3", n_races=30, winner_odds=5.0)       # 300 counted
    s3 = _rules(client)["S3"]["prospective"]
    assert s3["n_counted"] == 300
    assert s3["stage"] == "observing" and s3["checkpoint_pending"] is True   # 判定待ち

    last = picks[-1].pick_id
    _record(session, "S3", 300, "continue", last_pick_id=last)
    s3 = _rules(client)["S3"]["prospective"]
    assert (s3["stage"], s3["checkpoint"], s3["checkpoint_pending"]) == ("observing", 300, False)
    assert s3["next_checkpoint"] == 600 and s3["remaining_to_next"] == 300
    assert [d["decision"] for d in s3["decisions"]] == ["continue"]

    _record(session, "S3", 600, "undecided", last_pick_id=last)
    s3 = _rules(client)["S3"]["prospective"]
    assert (s3["stage"], s3["checkpoint"]) == ("undecided", 600)

    _record(session, "S1", 300, "passed", last_pick_id=last)
    _record(session, "S2", 300, "failed", last_pick_id=last)
    rules = _rules(client)
    assert (rules["S1"]["prospective"]["stage"], rules["S1"]["prospective"]["checkpoint"]) == (
        "passed", 300)
    assert rules["S2"]["prospective"]["stage"] == "failed"
    assert rules["S4"]["prospective"]["stage"] == "researching"
    decision = rules["S2"]["prospective"]["decisions"][0]
    assert decision["ci"] == [0.7, 1.1] and decision["prospective_start_date"] == "2026-10-01"
    assert decision["bootstrap"]["b"] == 20000

    # an undecided S3 horse still gets its chip, with the stage as the main label
    h = next(h for h in client.get(_url(picks[0].race_id)).json()["horses"]
             if h["horse_number"] == 1)
    assert h["chip_rule"] == "S3" and h["chip_stage"]["stage"] == "undecided"
    assert h["levels"]["prospective"] == 1


def test_late_result_does_not_change_a_recorded_stage(client, session, go_live):
    settled = _counted_block(session, "S3", n_races=30, winner_odds=5.0)
    _record(session, "S3", 300, "failed", last_pick_id=settled[-1].pick_id)
    # earlier races whose results arrive only after the decision was recorded
    early = _counted_block(session, "S3", n_races=2, first_day=_DAY - datetime.timedelta(days=1),
                           winner_odds=50.0, settle=False)
    before = _rules(client)["S3"]["prospective"]
    assert before["stage"] == "failed" and before["counts"]["pending_result"] == 20

    for race_id in sorted({p.race_id for p in early}):
        _results(session, race_id, {n: n for n in range(1, 11)})
    after = _rules(client)["S3"]["prospective"]
    assert after["n_counted"] == before["n_counted"] + 20
    assert after["frozen"]["roi"] > before["frozen"]["roi"]
    assert (after["stage"], after["checkpoint"]) == ("failed", 300)


def test_record_under_another_start_date_leaves_the_stage_pending(client, session, go_live):
    picks = _counted_block(session, "S4", n_races=1)
    _record(session, "S4", 300, "passed", last_pick_id=picks[0].pick_id,
            start=datetime.date(2026, 9, 1))
    s4 = _rules(client)["S4"]["prospective"]
    assert s4["stage"] == "researching" and s4["checkpoint"] is None
    assert s4["checkpoint_pending"] is True
    assert s4["decisions"][0]["prospective_start_date"] == "2026-09-01"


# --- tally ---------------------------------------------------------------------------------------


def test_exclusion_counts_reconcile_and_flags_stay_out_of_the_sum(client, session, go_live):
    def race(i, **kw):
        race_id = f"20261003{i:02d}11"
        return race_id, _race(session, race_id, number=11, **kw)

    def judged(race_id, post, n=1, **kw):
        if "computed_at" not in kw:
            kw["computed_at"] = post - datetime.timedelta(hours=2)
        return _pick(session, race_id, n, "S3", post_time=post,
                     digest=_started_digest(session, race_id), **kw)

    rid, post = race(1)                                          # counted (won)
    judged(rid, post, odds=12.0)
    _results(session, rid, {n: n for n in range(1, 7)})
    rid, post = race(2)                                          # voided_scratched
    p = judged(rid, post)
    _void(session, p)
    rid, post = race(3)                                          # before_start
    judged(rid, post, computed_at=datetime.datetime(2026, 9, 29, tzinfo=_UTC))
    rid, post = race(4, post_time=None)                          # post_time_unknown
    judged(rid, None, computed_at=datetime.datetime(2026, 10, 3, tzinfo=_UTC))
    rid, post = race(5)                                          # computed_after_post
    judged(rid, post, computed_at=post + datetime.timedelta(minutes=1))
    rid, post = race(6)                                          # result_known_at_compute
    judged(rid, post, pending=False)
    rid, post = race(7)                                          # observed_after_post
    judged(rid, post, observed=post)
    rid, post = race(8)                                          # pending_result
    judged(rid, post)
    rid, post = race(9)                                          # unsettled_horse
    judged(rid, post)
    _results(session, rid, {n: n - 1 for n in range(2, 7)})
    rid, post = race(10)                                         # dead_heat
    judged(rid, post)
    _results(session, rid, {1: 1, 2: 1, 3: 3})
    rid, post = race(11)                                         # counted (lost), field changed
    judged(rid, post, n=2)
    _results(session, rid, {n: n for n in range(1, 7)})
    session.execute(sa_update(RaceHorse).where(RaceHorse.race_id == rid,
                                               RaceHorse.horse_id == "H6")
                    .values(entry_status=EntryStatus.EXCLUDED))
    session.commit()

    s3 = _rules(client)["S3"]["prospective"]
    assert s3["counts"] == dict.fromkeys(ar.EXCLUSION_ORDER, 1)
    assert s3["n_counted"] == 2 and s3["n_hits"] == 1
    assert s3["n_counted"] + sum(s3["counts"].values()) == s3["n_picks_total"] == 11
    assert s3["flags"] == {"field_changed_after_pick": 1}
    assert s3["frozen"]["roi"] == pytest.approx(6.0)
    assert s3["frozen"]["valuation_basis"] == "frozen_pick_odds"
    assert s3["stored"]["valuation_basis"] == "stored_odds_mutable"
    assert (s3["stored"]["n"], s3["stored"]["n_missing_stored_odds"]) == (2, 0)
    assert set(s3["by_judged_freshness"]) == {"<=10m", "<=60m", ">60m"}
    assert s3["by_judged_freshness"][">60m"]["n"] == 2
    assert s3["bootstrap"]["b"] == ar.BOOTSTRAP["b"]


def test_rules_listing_is_rank_ordered_and_carries_the_frozen_values(client):
    body = client.get("/api/v1/attention-rules").json()
    assert [r["id"] for r in body["items"]] == ["S1", "S2", "S3", "S4", "S5"]
    assert body["rule_set_version"] == ar.RULE_SET_VERSION and body["disclaimer"]
    s1 = body["items"][0]
    assert s1["backtest"]["all"]["roi"] == ar.rule("S1").backtest_all.roi
    assert s1["backtest"]["valuation_basis"] == "closing_odds_approx"
    assert s1["backtest"]["selected"]["all"]["mean_ev"] == ar.rule("S1").selected_all.mean_ev
    assert s1["odds_band"] == [20.0, 40.0] and s1["gap_days"] == [14, 112]
    assert body["items"][4]["control"] is True
    assert "win_prob" not in str(body) and "p_hat" not in str(body)


# --- day list ------------------------------------------------------------------------------------


def test_day_list_is_post_ordered_and_typed(client, session):
    def judged_race(race_id, number, *, post_time="auto", day=_DAY, rules=("S1", "S3", "S4")):
        post = _race(session, race_id, number=number, post_time=post_time, day=day)
        computed = datetime.datetime(day.year, day.month, day.day, 0, 0, tzinfo=_UTC)
        digest = _started_digest(session, race_id)
        _scan(session, race_id, computed_at=computed, digest=digest, run_id=uuid.uuid4())
        for rule_id in rules:
            _pick(session, race_id, 4, rule_id, computed_at=computed, post_time=post,
                  digest=digest)

    judged_race("202610030512", 12)
    judged_race("202610030501", 1)
    judged_race("202610030503", 3, post_time=None)
    judged_race("202610030504", 4, rules=("S5",))            # S5 only: not listed
    judged_race("202610040501", 1, day=_DAY + datetime.timedelta(days=1))

    body = client.get("/api/v1/attention/day", params={"date": "2026-10-03"}).json()
    assert body["date"] == "2026-10-03"
    assert [i["race_id"] for i in body["items"]] == [
        "202610030501", "202610030512", "202610030503",
    ]
    item = body["items"][0]
    assert item["chip_rule"] == "S1" and item["horse_number"] == 4
    assert item["horse_name"] == "馬4" and item["venue_code"] == "05"
    assert item["chip_now"] == "unknown" and item["current_odds_observed_at"] is None
    assert item["chip_stage"]["stage"] == "researching"

    empty = client.get("/api/v1/attention/day", params={"date": "2026-10-05"})
    assert empty.status_code == 200 and empty.json() == {"date": "2026-10-05", "items": []}
    for params in ({"date": "2026-13-01"}, {"date": "x"}, {}):
        bad = client.get("/api/v1/attention/day", params=params)
        assert bad.status_code == 422
        assert bad.json()["code"] == "validation_error"


# --- memo / read-only ----------------------------------------------------------------------------


def test_memo_follows_new_picks_records_results_and_odds(client, session, go_live):
    picks = _counted_block(session, "S3", n_races=2, winner_odds=12.0)
    race_id = picks[0].race_id
    post = picks[0].post_time
    session.merge(Horse(horse_id="H11", horse_name="馬11"))
    session.merge(RaceHorse(race_id=race_id, horse_id="H11", horse_number=11,
                            odds=Decimal("30.0"), entry_status=EntryStatus.STARTED))
    session.commit()
    s3 = _rules(client)["S3"]["prospective"]
    assert (s3["n_picks_total"], s3["n_counted"], s3["n_hits"]) == (20, 20, 2)
    assert s3["stored"]["roi"] == pytest.approx(1.2)
    assert _rules(client)["S3"]["prospective"] == s3                # memo hit: same answer

    _pick(session, race_id, 11, "S3", computed_at=post - datetime.timedelta(hours=3),
          post_time=post, digest="x")                              # new pick (no result row)
    s3 = _rules(client)["S3"]["prospective"]
    assert s3["n_picks_total"] == 21 and s3["counts"]["unsettled_horse"] == 1

    _record(session, "S3", 300, "failed", last_pick_id=picks[-1].pick_id)   # new record
    assert _rules(client)["S3"]["prospective"]["stage"] == "failed"

    # result re-ingest: the first race loses its winner (no 1st place → every pick there is a
    # dead heat by the race-level definition)
    session.execute(sa_update(RaceResult).where(RaceResult.race_id == race_id)
                    .values(finish_order=RaceResult.finish_order + 1))
    session.commit()
    s3 = _rules(client)["S3"]["prospective"]
    assert (s3["n_counted"], s3["n_hits"], s3["counts"]["dead_heat"]) == (10, 1, 10)

    other = picks[-1].race_id                                   # odds re-ingest
    session.execute(sa_update(RaceHorse).where(RaceHorse.race_id == other,
                                               RaceHorse.horse_id == "H1")
                    .values(odds=Decimal("24.0")))
    session.commit()
    s3 = _rules(client)["S3"]["prospective"]
    assert s3["frozen"]["roi"] == pytest.approx(1.2)          # judged odds never move
    assert s3["stored"]["roi"] == pytest.approx(2.4)
    assert s3["odds_drift"]["n"] == 10

    # odds re-ingested as null: the winner leaves the stored basis (never valued at the judged
    # odds under the stored label) and is counted; the frozen basis keeps it
    session.execute(sa_update(RaceHorse).where(RaceHorse.race_id == other,
                                               RaceHorse.horse_id == "H1")
                    .values(odds=None))
    session.commit()
    s3 = _rules(client)["S3"]["prospective"]
    assert s3["frozen"]["roi"] == pytest.approx(1.2) and s3["n_counted"] == 10
    assert (s3["stored"]["n"], s3["stored"]["n_missing_stored_odds"]) == (9, 1)
    assert s3["stored"]["roi"] == pytest.approx(0.0)


def test_attention_reads_write_nothing(client, session, go_live):
    _judged_race(session)
    _counted_block(session, "S3", n_races=1)
    tables = (AttentionPick, AttentionRaceScan, AttentionCheckpoint, MarketEvPrediction,
              RaceHorse, RaceResult)

    def counts():
        return tuple(session.scalar(select(func.count()).select_from(t)) for t in tables)

    before = counts()
    assert client.get(_url()).status_code == 200
    assert client.get("/api/v1/attention-rules").status_code == 200
    assert client.get("/api/v1/attention/day", params={"date": "2026-10-03"}).status_code == 200
    session.expire_all()
    assert counts() == before


def test_attention_paths_are_get_only(client):
    spec = client.get("/openapi.json").json()
    for path in ("/api/v1/races/{race_id}/attention", "/api/v1/attention-rules",
                 "/api/v1/attention/day"):
        assert set(spec["paths"][path]) == {"get"}
