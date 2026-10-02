"""Feature 138 (plan 0.4・T020): the attention picks written by an ensemble market-ev run.

Against a real PostgreSQL (migration 0019): only a run that inserts the race's scan row is its
first computation and may create picks (D16) — later runs never add picks, not even for a race
that had none; a race whose odds were incomplete gets its first computation later. A scratched
pick gets exactly one void (D26) — a changed field or a re-keyed horse does not — even from a run
that computes nothing (``--pending-only`` after the last result of the day). Every pick
carries the values of the same run's in-memory predictions (run_id / computed_at / EV / odds /
features). The closure assertion (feature path never imports the writers) lives in the features
leak guard only.
"""

from __future__ import annotations

import datetime

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
from sqlalchemy import event, select, update

from horseracing_training import attention_picks, market_ev
from horseracing_training.cli import main
from tests._attention_synth import (
    DAY,
    ENS,
    SINGLE,
    A,
    B,
    C,
    D,
    ForcedEv,
    model_dirs,
    post_time,
    run,
    seed_card,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def dirs(tmp_path):
    return model_dirs(tmp_path)


@pytest.fixture
def forced(monkeypatch):
    return ForcedEv(monkeypatch)


def _picks(session, kind: str = "pick") -> list[AttentionPick]:
    session.expire_all()
    return list(session.scalars(
        select(AttentionPick).where(AttentionPick.kind == kind)
        .order_by(AttentionPick.race_id, AttentionPick.horse_id, AttentionPick.rule_id)
    ))


def _scans(session) -> dict[str, AttentionRaceScan]:
    session.expire_all()
    return {s.race_id: s for s in session.scalars(select(AttentionRaceScan))}


def _rows(session, version: str) -> dict[tuple[str, str], MarketEvPrediction]:
    session.expire_all()
    return {(r.race_id, r.horse_id): r for r in session.scalars(
        select(MarketEvPrediction).where(MarketEvPrediction.model_version == version))}


def _set_status(session, race_id: str, horse_id: str, status: str) -> None:
    session.execute(update(RaceHorse)
                    .where(RaceHorse.race_id == race_id, RaceHorse.horse_id == horse_id)
                    .values(entry_status=status))
    session.commit()


def test_first_computation_records_scans_and_picks_from_the_same_run(
    session, dirs, forced, monkeypatch
):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", None)  # before go-live: nothing counted
    seed_card(session)
    single, ensemble = dirs
    forced.ens[(A, "H4")] = 1.5     # S1, S2, S3, S4
    forced.single[(A, "H4")] = 1.25  # S5
    forced.ens[(D, "H13")] = 1.25   # settled race: S1, S3, S4 (H13 ran HIST 28 days earlier)

    out = run(session, single, ensemble)
    assert out["status"] == "ok"
    assert out["picks"] == {"races_first_computed": 3, "races_already_scanned": 0,
                            "written": 8, "voided_scratched": 0, "skipped_no_horse_number": 0}
    assert out["checkpoints"] == {"written": [], "pending": [], "error": None,
                                  "prospective_start_date": None, "dry_run": False}

    scans = _scans(session)
    assert set(scans) == {A, B, D}  # C is not race_ok (H9 unpriced): no scan, no pick
    assert {r: s.n_picks for r, s in scans.items()} == {A: 5, B: 0, D: 3}
    assert scans[A].result_pending_at_compute is True
    assert scans[D].result_pending_at_compute is False
    assert scans[A].post_time == post_time(DAY, 1)
    assert scans[A].field_digest == ar.field_digest(["H1", "H2", "H3", "H4"])

    picks = _picks(session)
    assert {(p.race_id, p.horse_id, p.rule_id) for p in picks} == {
        (A, "H4", "S1"), (A, "H4", "S2"), (A, "H4", "S3"), (A, "H4", "S4"), (A, "H4", "S5"),
        (D, "H13", "S1"), (D, "H13", "S3"), (D, "H13", "S4"),
    }
    ens_rows, single_rows = _rows(session, ENS), _rows(session, SINGLE)
    feats = market_ev.build_features(market_ev.load_rows(session.connection(),
                                                         through=DAY.isoformat()))
    gap = feats.set_index(["race_id", "horse_id"])["days_since_last"]
    for p in picks:
        key = (p.race_id, p.horse_id)
        assert str(p.run_id) == out["run_id"] == str(scans[p.race_id].run_id)
        assert p.computed_at == ens_rows[key].computed_at == single_rows[key].computed_at
        assert p.computed_at == scans[p.race_id].computed_at
        assert p.ens_expected_return == ens_rows[key].expected_return
        assert p.single_expected_return == single_rows[key].expected_return
        assert p.odds_used == ens_rows[key].odds_used
        assert p.odds_observed_at == ens_rows[key].odds_observed_at
        assert p.horse_number == ens_rows[key].horse_number
        assert float(p.days_since_last) == gap.loc[key] == 28.0
        assert p.result_pending_at_compute is (p.race_id == A)
        assert p.post_time == scans[p.race_id].post_time
        assert p.seconds_to_post == round((p.post_time - p.computed_at).total_seconds())
        assert p.field_digest == scans[p.race_id].field_digest
        assert (p.ensemble_model_version, p.single_model_version) == (ENS, SINGLE)
        assert p.logic_version == attention_picks.PICK_LOGIC_VERSION
        assert p.logic_version == market_ev.ENSEMBLE_LOGIC_VERSION + ";policy=v1"
        assert p.selection_policy_version == ar.SELECTION_POLICY_VERSION
        assert p.rule_set_version == ar.RULE_SET_VERSION
        assert p.void_reason is None and p.voids_pick_id is None

    # a recompute is never a first computation: the same horses do not get a second pick
    again = run(session, single, ensemble)
    assert again["picks"]["written"] == 0 and again["picks"]["races_already_scanned"] == 3
    assert len(_picks(session)) == 8
    assert {r: str(s.run_id) for r, s in _scans(session).items()} == {
        r: out["run_id"] for r in (A, B, D)
    }


def test_a_race_with_no_pick_at_its_first_computation_never_gets_one(session, dirs, forced):
    seed_card(session)
    single, ensemble = dirs
    first = run(session, single, ensemble)  # nothing matches
    assert first["picks"]["written"] == 0 and _scans(session)[A].n_picks == 0

    forced.ens[(A, "H4")] = 1.6  # H4 enters every band rule on the second computation
    forced.single[(A, "H4")] = 1.6
    second = run(session, single, ensemble)
    assert second["picks"]["written"] == 0 and second["picks"]["races_already_scanned"] == 3
    assert _picks(session) == []


def test_an_unpriced_race_gets_its_first_computation_once_priced(session, dirs, forced):
    seed_card(session)
    single, ensemble = dirs
    forced.ens[(C, "H10")] = 1.5
    run(session, single, ensemble)
    assert C not in _scans(session) and _picks(session) == []

    session.execute(update(RaceHorse).where(RaceHorse.race_id == C, RaceHorse.horse_id == "H9")
                    .values(odds=4.0))
    session.commit()
    out = run(session, single, ensemble)
    assert out["picks"]["races_first_computed"] == 1  # C only; A / B / D were scanned before
    assert {(p.race_id, p.horse_id, p.rule_id) for p in _picks(session)} == {
        (C, "H10", "S1"), (C, "H10", "S2"), (C, "H10", "S3"), (C, "H10", "S4"),
    }
    assert _scans(session)[C].run_id == _picks(session)[0].run_id

    # the void pass also reaches a race that is no longer race_ok
    session.execute(update(RaceHorse).where(RaceHorse.race_id == C, RaceHorse.horse_id == "H9")
                    .values(odds=None))
    session.commit()
    _set_status(session, C, "H10", EntryStatus.EXCLUDED)
    voided = run(session, single, ensemble)
    assert voided["picks"]["voided_scratched"] == 4
    assert {v.voids_pick_id for v in _picks(session, "void")} == {
        p.pick_id for p in _picks(session)
    }


def test_scratch_voids_once_and_a_field_change_never_voids(session, dirs, forced):
    seed_card(session)
    single, ensemble = dirs
    forced.ens[(A, "H4")] = 1.5
    run(session, single, ensemble)
    picks = _picks(session)
    assert len(picks) == 4

    _set_status(session, A, "H2", EntryStatus.CANCELLED)  # another horse leaves the field
    changed = run(session, single, ensemble)
    assert changed["picks"]["voided_scratched"] == 0 and changed["picks"]["written"] == 0
    assert _picks(session, "void") == []
    assert {p.field_digest for p in _picks(session)} == {
        ar.field_digest(["H1", "H2", "H3", "H4"])  # the stored digest stays the judged field
    }

    _set_status(session, A, "H4", EntryStatus.CANCELLED)  # the picked horse is scratched
    out = run(session, single, ensemble)
    assert out["picks"]["voided_scratched"] == 4
    voids = _picks(session, "void")
    by_target = {p.pick_id: p for p in picks}
    assert {v.voids_pick_id for v in voids} == set(by_target)
    for v in voids:
        target = by_target[v.voids_pick_id]
        assert (v.race_id, v.horse_id, v.rule_id, v.rule_set_version) == (
            target.race_id, target.horse_id, target.rule_id, target.rule_set_version)
        assert v.void_reason == "scratched" and v.horse_number is None
        assert v.ens_expected_return is None and v.odds_used is None
        assert str(v.run_id) == out["run_id"]

    # idempotent: one void per pick, and no new pick for the race either
    again = run(session, single, ensemble)
    assert again["picks"]["voided_scratched"] == 0
    assert len(_picks(session, "void")) == 4 and len(_picks(session)) == 4


def _settle(session, race_id: str, horse_ids: list[str]) -> None:
    for i, hid in enumerate(horse_ids):
        session.add(RaceResult(race_id=race_id, horse_id=hid, finish_order=i + 1,
                               result_status=ResultStatus.FINISHED))
    session.commit()


def test_a_run_that_computes_nothing_still_voids(session, engine, database_url, dirs, forced,
                                                 capsys):
    """A 競走除外 at the gate becomes known with the results, i.e. when ``--pending-only`` has
    nothing left to compute: the skipped run still writes the void (its own short transaction
    under the ensemble locks, with a fresh run_id), and touches nothing else."""
    seed_card(session)
    single, ensemble = dirs
    forced.ens[(A, "H4")] = 1.5
    first = run(session, single, ensemble)
    assert first["picks"]["written"] == 4
    rows_before = {v: _rows(session, v) for v in (ENS, SINGLE)}
    scans_before = {r: (s.run_id, s.n_picks) for r, s in _scans(session).items()}

    _set_status(session, A, "H4", EntryStatus.EXCLUDED)
    _settle(session, A, ["H1", "H2", "H3"])  # the excluded horse has no result row (INV-1)
    _settle(session, B, ["H5", "H6", "H7"])
    _settle(session, C, ["H8", "H9", "H10"])

    keys: list[str] = []

    def _capture(_conn, _cursor, statement, parameters, _context, _many):
        if "pg_advisory_xact_lock" in statement:
            keys.append(parameters["k"])

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        out = run(session, single, ensemble, pending_only=True)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    assert (out["status"], out["reason"]) == ("skipped", "no_pending_races")
    assert out["run_id"] is None and out["checkpoints"] is None
    assert out["picks"]["voided_scratched"] == 4 and out["picks"]["written"] == 0
    assert keys == [f"market_ev:{ENS}:{DAY.isoformat()}"]

    voids = _picks(session, "void")
    assert {v.voids_pick_id for v in voids} == {p.pick_id for p in _picks(session)}
    assert {str(v.run_id) for v in voids} == {out["picks"]["void_run_id"]}
    assert all(v.result_pending_at_compute is False for v in voids)
    assert {v.field_digest for v in voids} == {ar.field_digest(["H1", "H2", "H3"])}
    # nothing but the voids: both versions' rows and the scans are as the first run left them
    for v in (ENS, SINGLE):
        assert {k: (r.run_id, r.expected_return) for k, r in _rows(session, v).items()} == {
            k: (r.run_id, r.expected_return) for k, r in rows_before[v].items()
        }
    assert {r: (s.run_id, s.n_picks) for r, s in _scans(session).items()} == scans_before

    # idempotent, and the CLI keeps SKIPPED as the last line with the void pass right before it
    base = ["--model-dir", str(single), "--ensemble-dir", str(ensemble),
            "--database-url", database_url]
    assert main(["market-ev", "--date", DAY.isoformat(), "--pending-only", *base]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1] == "SKIPPED: no_pending_races"
    assert lines[-2].startswith("attention-picks: ") and "voided_scratched=0" in lines[-2]
    assert len(_picks(session, "void")) == 4


def test_a_rekeyed_horse_is_not_voided(session, dirs, forced):
    seed_card(session)
    single, ensemble = dirs
    forced.ens[(A, "H4")] = 1.5
    run(session, single, ensemble)
    # 067-style re-key: the (race, horse) row of the pick now carries another id
    session.add(Horse(horse_id="H4X", horse_name="H4X"))
    session.flush()
    session.execute(update(RaceHorse).where(RaceHorse.race_id == A, RaceHorse.horse_id == "H4")
                    .values(horse_id="H4X"))
    session.commit()
    out = run(session, single, ensemble)
    assert out["picks"]["voided_scratched"] == 0 and out["picks"]["written"] == 0
    assert _picks(session, "void") == []


def test_without_ensemble_dir_nothing_attention_is_touched(session, dirs, forced):
    seed_card(session)
    single, ensemble = dirs
    forced.ens[(A, "H4")] = 1.5
    out = run(session, single, None)
    assert out["picks"] is None and out["checkpoints"] is None
    assert list(out["versions"]) == [SINGLE]
    assert _scans(session) == {} and _picks(session) == [] and _rows(session, ENS) == {}

    run(session, single, ensemble)  # picks A/H4
    _set_status(session, A, "H4", EntryStatus.CANCELLED)
    run(session, single, None)  # a single-version run does not void either
    assert _picks(session, "void") == []
    session.expire_all()
    assert list(session.scalars(select(AttentionCheckpoint))) == []


def test_picks_need_a_post_time_only_for_the_tally(session, dirs, forced):
    """An unknown post time is recorded as NULL (pending at compute, seconds_to_post NULL)."""
    seed_card(session)
    single, ensemble = dirs
    session.execute(update(Race).where(Race.race_id == A)
                    .values(post_time=None))
    session.commit()
    forced.ens[(A, "H4")] = 1.5
    run(session, single, ensemble)
    picks = _picks(session)
    assert picks and all(p.post_time is None and p.seconds_to_post is None for p in picks)
    assert all(p.result_pending_at_compute for p in picks)
    assert _scans(session)[A].post_time is None


def test_computed_after_the_post_is_not_result_pending(session, dirs, forced):
    seed_card(session)
    single, ensemble = dirs
    past = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    session.execute(update(Race).where(Race.race_id == A)
                    .values(post_time=past))
    session.commit()
    forced.ens[(A, "H4")] = 1.5
    run(session, single, ensemble)
    picks = _picks(session)
    assert picks and all(p.result_pending_at_compute is False for p in picks)
    assert all(p.seconds_to_post < 0 for p in picks)
    assert _scans(session)[A].result_pending_at_compute is False
