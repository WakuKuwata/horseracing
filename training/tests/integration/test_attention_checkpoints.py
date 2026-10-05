"""Feature 138 (plan 0.4・T020b, D18) / 139 (T009): the recorded 300/600-point checkpoint decisions.

Synthetic picks (one race per day, ten picked horses per race, rule S3) settle at the official win
payout (selection policy v2, 139 D3) — the winner's judged odds and its payout differ, so a v1
(judged-odds) settlement would give another ROI: only picks whose post time is at least three days
old are material, a race with results but no payout (``payout_race_missing``) and a payout that
disagrees with the result (``payout_inconsistent``) are never material, v2 counts from its own
start date and never reads a v1 record, a 600 decision is written only after a 300 ``continue``
taken under the current prospective start date, a decision is recorded once and a late result or
payout never changes it, ``--dry-run`` writes nothing, and a failing judgement never undoes the
computation that ran before it.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from decimal import Decimal

import pytest
from horseracing_db.enums import ResultStatus
from horseracing_db.models import (
    AttentionCheckpoint,
    AttentionPick,
    AttentionRaceScan,
    Horse,
    MarketEvPrediction,
    OfficialWinPayout,
    Race,
    RaceHorse,
    RaceResult,
)
from horseracing_eval import attention_rules as ar
from sqlalchemy import select

from horseracing_training import attention_checkpoints
from horseracing_training.cli import main
from tests import _attention_synth as att

pytestmark = pytest.mark.integration

FIRST_DAY = datetime.date(2025, 1, 1)  # in the past: the CLI judges at the real now
HORSES = [f"CH{i}" for i in range(10)]


def _post(i: int, first: datetime.date = FIRST_DAY) -> datetime.datetime:
    day = first + datetime.timedelta(days=i)
    return datetime.datetime(day.year, day.month, day.day, 3, tzinfo=datetime.UTC)  # 12:00 JST


def _rid(i: int, first: datetime.date = FIRST_DAY) -> str:
    day = first + datetime.timedelta(days=i)
    return f"{day:%Y%m%d}0601"


def _drifted(winner_odds):
    """The official payout of race i's winner: its judged odds drifted to final odds +0.3 — so a
    settlement at the judged odds (v1) and at the official payout (v2) never coincide."""
    return lambda i: round(winner_odds(i) * 100) + 30


def seed_picks(session, n_races: int, winner_odds, *, results=True, rule="S3", payout=None,
               first: datetime.date = FIRST_DAY, policy=None) -> list[str]:
    """n_races races (one per day), ten S3 picks each; horse 0 (馬番 1) wins at judged odds
    ``winner_odds(i)`` and is paid ``payout(i)`` yen per 100 (default: ``_drifted``; None = the
    race has results but no official payout). ``policy(i)`` = the pick rows' recorded policy.
    Each race's entries carry the 馬番 (the race-level payout check reads the winners' 馬番)."""
    payout = _drifted(winner_odds) if payout is None else payout
    for h in HORSES:
        session.merge(Horse(horse_id=h, horse_name=h))
    session.flush()
    ids: list[str] = []
    for i in range(n_races):
        rid, post = _rid(i, first), _post(i, first)
        session.add(Race(race_id=rid, race_date=post.date(), race_number=1, venue_code="06",
                         distance=1600, track_type="芝", post_time=post))
        session.flush()
        for n, h in enumerate(HORSES):
            session.add(RaceHorse(race_id=rid, horse_id=h, horse_number=n + 1))
        session.flush()
        for n, h in enumerate(HORSES):
            pick_id = uuid.uuid4()
            ids.append(str(pick_id))
            session.add(AttentionPick(
                pick_id=pick_id, race_id=rid, horse_id=h, horse_number=n + 1, rule_id=rule,
                rule_set_version=ar.RULE_SET_VERSION, kind="pick",
                ens_expected_return=Decimal("1.5"), single_expected_return=Decimal("1.0"),
                odds_used=Decimal(str(winner_odds(i) if n == 0 else 10.0)),
                odds_observed_at=post - datetime.timedelta(hours=2),
                days_since_last=Decimal("28.0"), post_time=post, seconds_to_post=3600,
                result_pending_at_compute=True, field_digest=ar.field_digest(HORSES),
                ensemble_model_version=att.ENS, single_model_version=att.SINGLE,
                logic_version="test", run_id=uuid.uuid4(),
                selection_policy_version=(
                    ar.SELECTION_POLICY_VERSION if policy is None else policy(i)
                ),
                computed_at=post - datetime.timedelta(hours=1),
            ))
        if results:
            add_results(session, i, payout(i), first=first)
    session.commit()
    return ids


def add_results(session, i: int, payout_yen: int | None, *, first: datetime.date = FIRST_DAY,
                paid_number: int = 1) -> None:
    """Race i's results (馬番 n+1 finishes n+1st) and, unless ``payout_yen`` is None, the official
    win payout row — on ``paid_number`` (the winner's 馬番 unless a test breaks it)."""
    for n, h in enumerate(HORSES):
        session.add(RaceResult(race_id=_rid(i, first), horse_id=h, finish_order=n + 1,
                               result_status=ResultStatus.FINISHED))
    session.flush()
    if payout_yen is not None:
        add_payout(session, i, payout_yen, first=first, horse_number=paid_number)


def add_payout(session, i: int, payout_yen: int, *, first: datetime.date = FIRST_DAY,
               horse_number: int = 1) -> None:
    session.add(OfficialWinPayout(
        race_id=_rid(i, first), horse_number=horse_number, payout_yen=payout_yen,
        observed_at=_post(i, first) + datetime.timedelta(minutes=20),
    ))
    session.flush()


def _alternating(i: int) -> float:
    return 15.0 if i % 2 == 0 else 5.0  # day ROI ≈1.5 / ≈0.5: the CI straddles 100%


def _no_payout_on(*races: int):
    """Payouts as ``_drifted(_alternating)`` except None (results but no payout) on ``races``."""
    drifted = _drifted(_alternating)
    return lambda i: None if i in races else drifted(i)


def _records(session) -> list[AttentionCheckpoint]:
    session.expire_all()
    return list(session.scalars(
        select(AttentionCheckpoint).order_by(AttentionCheckpoint.rule_id,
                                             AttentionCheckpoint.checkpoint)))


@pytest.fixture
def started(monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", FIRST_DAY)
    return FIRST_DAY


def _evaluate(session, last_race: int, *, days_after: float, **kw) -> dict:
    now = _post(last_race) + datetime.timedelta(days=days_after)
    return attention_checkpoints.evaluate_checkpoints(session, now=now, run_id=uuid.uuid4(),
                                                      **kw)


def test_no_start_date_counts_nothing(session, monkeypatch):
    seed_picks(session, 31, _alternating)
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", None)  # before go-live
    out = _evaluate(session, 30, days_after=10)
    assert out == {"written": [], "pending": [], "error": None, "prospective_start_date": None,
                   "dry_run": False}
    assert _records(session) == []


def test_picks_younger_than_the_settlement_lag_are_not_material(session, started):
    seed_picks(session, 31, _alternating)  # 310 counted picks
    early = _evaluate(session, 30, days_after=1)  # races 29-30 are within three days: 290 material
    assert early["written"] == [] and early["pending"] == [("S3", 300)]
    assert _records(session) == []

    due = _evaluate(session, 30, days_after=3)
    assert due["written"] == [("S3", 300, "continue")] and due["pending"] == []
    (rec,) = _records(session)
    assert (rec.rule_id, rec.checkpoint, rec.decision, rec.n_counted) == ("S3", 300, "continue",
                                                                          300)
    assert rec.settlement_cutoff == _post(30)  # now − 3 days
    assert rec.prospective_start_date == FIRST_DAY
    assert rec.selection_policy_version == ar.SELECTION_POLICY_VERSION
    assert rec.rule_set_version == ar.RULE_SET_VERSION
    assert rec.skipped_pending_before_last == 0
    assert rec.bootstrap["b"] == ar.BOOTSTRAP["b"] and rec.bootstrap["seed"] == ar.BOOTSTRAP["seed"]
    assert rec.bootstrap["settlement"] == "official_win_payout"
    assert rec.ci_low < 1 < rec.ci_high


def test_counted_pick_ids_sha256_is_the_first_300_in_post_order(session, started):
    seed_picks(session, 31, _alternating)
    _evaluate(session, 30, days_after=5)
    (rec,) = _records(session)
    session.expire_all()
    picks = list(session.scalars(select(AttentionPick).where(AttentionPick.kind == "pick")))
    ordered = sorted(picks, key=lambda p: (p.post_time, p.race_id, p.horse_number,
                                           str(p.pick_id)))[:300]
    expected = hashlib.sha256("\n".join(str(p.pick_id) for p in ordered).encode()).hexdigest()
    assert rec.counted_pick_ids_sha256 == expected
    assert rec.last_pick_id == ordered[-1].pick_id
    hits = sum(1 for p in ordered if p.horse_number == 1)
    assert rec.n_hits == hits == 30
    paid = {(o.race_id, o.horse_number): o.payout_yen
            for o in session.scalars(select(OfficialWinPayout))}
    official = sum(paid[(p.race_id, p.horse_number)] for p in ordered if p.horse_number == 1)
    judged = sum(float(p.odds_used) for p in ordered if p.horse_number == 1) * 100
    # settled at the official payout (v2), not at the judged odds (v1)
    assert rec.roi_frozen == Decimal(repr(official / 30000))
    assert official != judged and rec.roi_frozen != Decimal(repr(judged / 30000))


def test_600_is_written_only_after_a_300_continue(session, started):
    seed_picks(session, 62, _alternating)
    out = _evaluate(session, 61, days_after=5)
    assert out["written"] == [("S3", 300, "continue"), ("S3", 600, "undecided")]
    assert [(r.checkpoint, r.decision) for r in _records(session)] == [(300, "continue"),
                                                                       (600, "undecided")]
    # recorded once: a second judgement writes nothing (the records stand)
    again = _evaluate(session, 61, days_after=9)
    assert again["written"] == [] and again["pending"] == [] and again["error"] is None
    assert len(_records(session)) == 2


def test_a_record_written_concurrently_stands_and_nothing_is_added(session, started, monkeypatch):
    """The ON CONFLICT path: the record appears after this judgement read the existing records
    (another writer won the race) — the insert returns nothing, nothing is reported as written,
    and the stored record is unchanged."""
    seed_picks(session, 31, _alternating)
    _evaluate(session, 30, days_after=5)
    (rec,) = _records(session)
    before = (rec.checkpoint_id, rec.decided_at, rec.counted_pick_ids_sha256)
    monkeypatch.setattr(attention_checkpoints, "_existing_records", lambda _session: {})
    out = _evaluate(session, 30, days_after=9)
    assert out["written"] == [] and out["pending"] == [] and out["error"] is None
    (after,) = _records(session)
    assert (after.checkpoint_id, after.decided_at, after.counted_pick_ids_sha256) == before


def test_a_failed_300_is_final(session, started):
    seed_picks(session, 62, lambda i: 2.0)  # every day returns 23%: the CI is below 100%
    out = _evaluate(session, 61, days_after=5)
    assert out["written"] == [("S3", 300, "failed")] and out["pending"] == []
    assert [(r.checkpoint, r.decision) for r in _records(session)] == [(300, "failed")]


def test_a_late_result_never_changes_the_record(session, started):
    ids = seed_picks(session, 32, _alternating, results=False)
    pay = _drifted(_alternating)
    for i in range(32):
        if i != 4:  # race 4's results arrive late
            add_results(session, i, pay(i))
    session.commit()
    _evaluate(session, 31, days_after=5)
    (rec,) = _records(session)
    # race 4's ten picks precede the material's last pick but had no result yet
    assert rec.skipped_pending_before_last == 10
    before = (rec.checkpoint_id, rec.counted_pick_ids_sha256, rec.decision, rec.last_pick_id)
    assert str(rec.last_pick_id) in ids

    add_results(session, 4, pay(4))
    session.commit()
    again = _evaluate(session, 31, days_after=9)
    assert again["written"] == []
    (after,) = _records(session)
    assert (after.checkpoint_id, after.counted_pick_ids_sha256, after.decision,
            after.last_pick_id) == before


def test_dry_run_writes_nothing(session, started):
    seed_picks(session, 31, _alternating)
    out = _evaluate(session, 30, days_after=5, dry_run=True)
    assert out["dry_run"] is True and out["written"] == [("S3", 300, "continue")]
    assert _records(session) == []


def test_a_record_under_another_start_date_is_never_extended(session, started, monkeypatch):
    seed_picks(session, 62, _alternating)
    # a 300 continue recorded under the go-live date, with only 300 material at the time
    _evaluate(session, 32, days_after=0)
    assert [(r.checkpoint, r.decision) for r in _records(session)] == [(300, "continue")]
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", FIRST_DAY + datetime.timedelta(days=1))
    out = _evaluate(session, 61, days_after=5)
    assert out["written"] == []
    assert out["error"].startswith(f"{attention_checkpoints.MISMATCH}: S3/300 recorded under")
    assert [r.checkpoint for r in _records(session)] == [300]


def test_voided_picks_are_not_material(session, started):
    ids = seed_picks(session, 31, _alternating)
    first = next(p for p in _picks_of_race(session, 0) if p.horse_number == 2)
    session.add(AttentionPick(
        pick_id=uuid.uuid4(), race_id=first.race_id, horse_id=first.horse_id, rule_id="S3",
        rule_set_version=ar.RULE_SET_VERSION, kind="void", void_reason="scratched",
        voids_pick_id=first.pick_id, result_pending_at_compute=True,
        field_digest=first.field_digest, ensemble_model_version=att.ENS,
        single_model_version=att.SINGLE, logic_version="test", run_id=uuid.uuid4(),
        selection_policy_version=ar.SELECTION_POLICY_VERSION,
    ))
    session.commit()
    assert str(first.pick_id) in ids
    _evaluate(session, 30, days_after=5)
    (rec,) = _records(session)
    # with one pick of race 0 voided, the 300th pick moves one place later (into race 30)
    assert rec.last_pick_id in {p.pick_id for p in _picks_of_race(session, 30)}


def _picks_of_race(session, i: int, first: datetime.date = FIRST_DAY) -> list[AttentionPick]:
    session.expire_all()
    return list(session.scalars(select(AttentionPick).where(
        AttentionPick.race_id == _rid(i, first), AttentionPick.kind == "pick")))


def test_a_failing_judgement_never_undoes_the_computation(session, database_url, tmp_path,
                                                           monkeypatch, capsys):
    att.seed_card(session)
    single, ensemble = att.model_dirs(tmp_path)
    forced = att.ForcedEv(monkeypatch)
    forced.ens[(att.A, "H4")] = 1.5

    def _boom(*_a, **_k):
        raise RuntimeError("checkpoint\nstore unavailable")

    monkeypatch.setattr(attention_checkpoints, "evaluate_checkpoints", _boom)
    out = att.run(session, single, ensemble)
    assert out["status"] == "ok"
    assert out["checkpoints"] == {"written": [], "pending": [],
                                  "error": "RuntimeError: checkpoint store unavailable"}
    session.expire_all()
    assert session.scalars(select(MarketEvPrediction)).first() is not None
    assert len(list(session.scalars(select(AttentionRaceScan)))) == 3
    assert len(list(session.scalars(select(AttentionPick)))) == 4

    base = ["--model-dir", str(single), "--ensemble-dir", str(ensemble),
            "--database-url", database_url]
    assert main(["market-ev", "--date", att.DAY.isoformat(), *base]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1].endswith("versions=2 picks=0 checkpoints=error")
    assert lines[-2] == "attention-checkpoints: error=RuntimeError: checkpoint store unavailable"


def test_cli_attention_checkpoints_end_to_end(session, database_url, started, capsys):
    seed_picks(session, 31, _alternating)  # the races ran in 2025-01: all past the lag now
    assert main(["attention-checkpoints", "--dry-run", "--database-url", database_url]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == (
        "OK: written=1 pending=0 dry_run=true"
    )
    assert _records(session) == []
    assert main(["attention-checkpoints", "--database-url", database_url]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "OK: written=1 pending=0 dry_run=false"
    assert out[-2].startswith("attention-checkpoints: written=S3:300:continue pending=- ")
    assert [(r.checkpoint, r.decision) for r in _records(session)] == [(300, "continue")]


# --- Feature 139 (T009): selection policy v2 = settlement at the official win payout ----------


def _classes(session, rule: str = "S3") -> dict[str, int]:
    """class → number of the rule's picks, classified exactly as the decision classifies them."""
    out: dict[str, int] = {}
    for f in attention_checkpoints.load_pick_facts(session).get(rule, []):
        c = ar.classify_pick(f)
        out[c] = out.get(c, 0) + 1
    return out


def _material(session, last_race: int, *, days_after: float, first=FIRST_DAY) -> list:
    """The counted picks old enough to settle — what ``evaluate_checkpoints`` decides from."""
    now = _post(last_race, first) + datetime.timedelta(days=days_after)
    cutoff = now - ar.CHECKPOINT_SETTLEMENT_LAG
    facts = attention_checkpoints.load_pick_facts(session)["S3"]
    return [f for f in facts if ar.classify_pick(f) == "counted" and f.post_time <= cutoff]


def test_load_pick_facts_carries_the_official_payout_by_horse_number(session, started):
    seed_picks(session, 3, _alternating, payout=_no_payout_on(2))
    facts = {(f.race_id, f.horse_number): f
             for f in attention_checkpoints.load_pick_facts(session)["S3"]}
    winner, loser = facts[(_rid(0), 1)], facts[(_rid(0), 2)]
    assert (winner.won, winner.official_payout_yen, winner.race_payout_known) == (True, 1530.0,
                                                                                 True)
    assert (loser.won, loser.official_payout_yen, loser.race_payout_known) == (False, None, True)
    assert winner.odds_used == 15.0 and winner.stored_odds is None  # race_horses is never read
    unpaid = facts[(_rid(2), 1)]
    assert (unpaid.won, unpaid.official_payout_yen, unpaid.race_payout_known) == (True, None,
                                                                                 False)
    assert ar.classify_pick(winner) == ar.classify_pick(loser) == "counted"
    assert ar.classify_pick(unpaid) == "payout_race_missing"


def test_decisions_settle_at_the_official_payout_not_at_the_judged_odds(session, started):
    """Judged odds 12.0 (v1 would settle every day at 120% and pass) but the official payout is
    900 yen (90%): the recorded v2 decision fails, with the official ROI."""
    seed_picks(session, 31, lambda i: 12.0, payout=lambda i: 900)
    out = _evaluate(session, 30, days_after=5)
    assert out["written"] == [("S3", 300, "failed")] and out["error"] is None
    (rec,) = _records(session)
    assert rec.selection_policy_version == ar.SELECTION_POLICY_VERSION == "v2"
    assert rec.bootstrap["settlement"] == "official_win_payout"
    assert rec.n_hits == 30
    assert rec.roi_frozen == Decimal(repr(30 * 900 / 30000))
    assert rec.ci_high < 1

    # the same material settled at the judged odds (v1, kept only as a reference) differs
    material = _material(session, 30, days_after=5)
    assert len(material) == 310
    v1_decision, v1 = ar.decide_checkpoint(material, 300, payout_of=ar.frozen_payout)
    assert v1["bootstrap"]["settlement"] == "frozen_pick_odds"
    assert (v1_decision, v1["roi_frozen"]) == ("passed", 30 * 1200 / 30000)
    assert v1["counted_pick_ids_sha256"] == rec.counted_pick_ids_sha256  # same picks, other money
    assert Decimal(repr(v1["roi_frozen"])) != rec.roi_frozen


def test_a_race_without_any_official_payout_is_not_material(session, started):
    seed_picks(session, 32, _alternating, payout=_no_payout_on(4))
    assert _classes(session) == {"counted": 310, "payout_race_missing": 10}
    out = _evaluate(session, 31, days_after=5)
    assert out["written"] == [("S3", 300, "continue")]
    (rec,) = _records(session)
    # race 4's ten picks are skipped: the 300th counted pick moves one race later (into race 30)
    assert rec.last_pick_id in {p.pick_id for p in _picks_of_race(session, 30)}
    assert rec.skipped_pending_before_last == 10  # awaiting settlement material, like a result
    race4 = {str(p.pick_id) for p in _picks_of_race(session, 4)}
    material = _material(session, 31, days_after=5)
    assert not race4 & {f.pick_id for f in material}
    before = (rec.checkpoint_id, rec.counted_pick_ids_sha256, rec.decision, rec.roi_frozen)

    # the payout arrives late: the recorded decision stands (ratchet)
    add_payout(session, 4, _drifted(_alternating)(4))
    session.commit()
    assert _classes(session) == {"counted": 320}
    again = _evaluate(session, 31, days_after=9)
    assert again["written"] == [] and again["error"] is None
    (after,) = _records(session)
    assert (after.checkpoint_id, after.counted_pick_ids_sha256, after.decision,
            after.roi_frozen) == before


def test_a_race_whose_payout_disagrees_with_the_result_is_left_out_whole(session, started):
    """Race 2's payout row names 馬番 2 although 馬番 1 won: the race's payout rows disagree with
    its result, so ALL ten of its picks are ``payout_inconsistent`` (race level, 139 D11) — never
    only the unpaid winner and the paid loser, which would keep the race's other losers at 0 and
    bias the ROI down. The exclusive Σ still reconciles and the race is never material."""
    seed_picks(session, 31, _alternating, results=False)
    pay = _drifted(_alternating)
    for i in range(31):
        add_results(session, i, pay(i), paid_number=2 if i == 2 else 1)
    session.commit()
    classes = _classes(session)
    assert classes == {"counted": 300, "payout_inconsistent": 10}
    assert sum(classes.values()) == 310
    facts = {(f.race_id, f.horse_number): f
             for f in attention_checkpoints.load_pick_facts(session)["S3"]}
    race2 = [facts[(_rid(2), n)] for n in range(1, 11)]
    assert {ar.classify_pick(f) for f in race2} == {"payout_inconsistent"}
    assert all(not f.race_payout_consistent and f.race_payout_known for f in race2)
    # the other races stay consistent
    assert facts[(_rid(3), 1)].race_payout_consistent and facts[(_rid(3), 5)].race_payout_consistent

    out = _evaluate(session, 30, days_after=5)
    assert out["written"] == [("S3", 300, "continue")]
    (rec,) = _records(session)
    material = _material(session, 30, days_after=5)
    assert len(material) == 300 and not {f.pick_id for f in race2} & {f.pick_id for f in material}
    # ten picks fewer before race 30: the 300th counted pick is race 30's last (馬番 10)
    last = next(p for p in _picks_of_race(session, 30) if p.horse_number == 10)
    assert rec.last_pick_id == last.pick_id
    # race 2 is out whole: 30 winners (races 0..30 but 2) over 300 picks of 30 races
    assert rec.n_hits == 30
    assert rec.roi_frozen == Decimal(repr(sum(pay(i) for i in range(31) if i != 2) / 30000))
    assert rec.skipped_pending_before_last == 0  # a defect is not awaiting settlement


def test_a_winner_without_a_known_horse_number_makes_its_race_inconsistent(session, started):
    """The race-level check reads the winners' 馬番 from the entries; a winner whose entry has no
    馬番 can never match a payout row (fail-closed), so the whole race is left out."""
    seed_picks(session, 3, _alternating)
    session.query(RaceHorse).filter(RaceHorse.race_id == _rid(1), RaceHorse.horse_id == HORSES[0]
                                    ).update({"horse_number": None})
    session.commit()
    assert _classes(session) == {"counted": 20, "payout_inconsistent": 10}


V2_FIRST = datetime.date(2026, 10, 4)  # race 0 runs the day before policy v2's start


def test_v2_counts_from_its_own_start_date_and_never_reads_a_v1_record(session):
    """With the real constants: a pick computed on 2026-10-04 (JST) is ``before_start`` under v2;
    a pick computed on 2026-10-05 counts even when its row was written under v1 (the selection is
    the same); a v1 checkpoint record (start 2026-10-02) is neither read nor extended."""
    assert ar.SELECTION_POLICY_VERSION == "v2"
    assert ar.PROSPECTIVE_START_DATE == datetime.date(2026, 10, 5)
    ids = seed_picks(session, 31, _alternating, first=V2_FIRST,
                     policy=lambda i: "v1" if i <= 1 else "v2")
    assert _classes(session) == {"before_start": 10, "counted": 300}
    session.add(AttentionCheckpoint(
        checkpoint_id=uuid.uuid4(), rule_id="S3", checkpoint=300, selection_policy_version="v1",
        rule_set_version=ar.RULE_SET_VERSION, decision="continue", n_counted=300, n_hits=30,
        roi_frozen=Decimal("1.0"), ci_low=Decimal("0.5"), ci_high=Decimal("1.5"),
        bootstrap={"settlement": "frozen_pick_odds"}, counted_pick_ids_sha256="0" * 64,
        last_pick_id=uuid.UUID(ids[0]), settlement_cutoff=_post(0, V2_FIRST),
        prospective_start_date=ar.V1_START_DATE, skipped_pending_before_last=0,
        decided_at=_post(0, V2_FIRST),
    ))
    session.commit()

    out = _evaluate_at(session, _post(30, V2_FIRST) + datetime.timedelta(days=5))
    assert out["error"] is None  # a v1 record under 2026-10-02 is not a v2 start-date mismatch
    assert out["prospective_start_date"] == "2026-10-05"
    assert out["written"] == [("S3", 300, "continue")]
    v2 = [r for r in _records(session) if r.selection_policy_version == "v2"]
    (rec,) = v2
    assert rec.prospective_start_date == datetime.date(2026, 10, 5)
    assert rec.bootstrap["settlement"] == "official_win_payout"
    race0 = {p.pick_id for p in _picks_of_race(session, 0, V2_FIRST)}
    ordered = sorted(
        (p for p in _all_picks(session) if p.pick_id not in race0),
        key=lambda p: (p.post_time, p.race_id, p.horse_number, str(p.pick_id)),
    )
    assert len(ordered) == 300
    expected = hashlib.sha256("\n".join(str(p.pick_id) for p in ordered).encode()).hexdigest()
    assert rec.counted_pick_ids_sha256 == expected
    # the v1 record is left exactly as it was
    (v1,) = [r for r in _records(session) if r.selection_policy_version == "v1"]
    assert (v1.decision, v1.prospective_start_date) == ("continue", datetime.date(2026, 10, 2))


def _evaluate_at(session, now: datetime.datetime) -> dict:
    return attention_checkpoints.evaluate_checkpoints(session, now=now, run_id=uuid.uuid4())


def _all_picks(session) -> list[AttentionPick]:
    session.expire_all()
    return list(session.scalars(select(AttentionPick).where(AttentionPick.kind == "pick")))
