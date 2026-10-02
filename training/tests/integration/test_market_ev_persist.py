"""Feature 137 (plan 0.3): compute_and_persist + the ``market-ev`` CLI against a real PostgreSQL.

A recompute replaces a race's rows for its model_version in one transaction (race-atomic): a horse
scratched after the first compute leaves no stale row, other model versions are untouched, and every
provenance column (odds used + when observed, result-pending flag, booster + sha256, logic
version, run id, computed_at) is filled.

Feature 138 (T019): with ``--ensemble-dir`` the 15-seed average is written in the same run (one
run_id / computed_at / transaction, per-date locks single → ensemble, the same rows), a failure
anywhere rolls back both versions and the scans, and ``--ensemble-only`` never touches a byte of the
single-seed rows.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
from decimal import Decimal

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import (
    AttentionPick,
    AttentionRaceScan,
    Horse,
    Jockey,
    MarketEvPrediction,
    Race,
    RaceHorse,
    RaceResult,
    Trainer,
)
from sqlalchemy import event, select, update

from horseracing_training import market_ev
from horseracing_training.cli import main
from tests import _attention_synth as att
from tests._market_ev_synth import write_model_dir

pytestmark = pytest.mark.integration

DAY = datetime.date(2026, 9, 27)
HISTORY_DAY = datetime.date(2025, 12, 6)
NEXT_DAY = datetime.date(2026, 9, 28)

HIST = "202512060501"
A = "202609270501"  # result-pending, all priced
B = "202609270502"  # result-pending, all priced
C = "202609270503"  # one started horse unpriced → not race_ok → never computed
D = "202609270504"  # results already in → computed as an after-the-fact value
E = "202609280501"  # another day → outside the scope


def _race(session, rid: str, day: datetime.date, number: int, horses: list[tuple], *,
          results: bool) -> None:
    """horses: (horse_id, jockey_id, odds) in horse-number order."""
    session.add(Race(race_id=rid, race_date=day, race_number=number, venue_code="05",
                     distance=1600, track_type="芝", going="良", weather="晴",
                     race_class="未勝利", prize_money=500))
    for i, (hid, jid, odds) in enumerate(horses):
        session.merge(Horse(horse_id=hid, horse_name=hid, sire_line="ナスルーラ系"))
        session.merge(Jockey(jockey_id=jid, jockey_name=jid))
        session.merge(Trainer(trainer_id="T1", trainer_name="T1"))
        session.flush()
        session.add(RaceHorse(race_id=rid, horse_id=hid, horse_number=i + 1, frame=i + 1,
                              sex="牡", age=4, jockey_id=jid, trainer_id="T1",
                              jockey_weight=Decimal("55.0"), running_style="先行",
                              odds=None if odds is None else Decimal(str(odds)),
                              popularity=i + 1, entry_status=EntryStatus.STARTED))
        if results:
            session.add(RaceResult(race_id=rid, horse_id=hid, finish_order=i + 1,
                                   result_status=ResultStatus.FINISHED,
                                   last_3f=Decimal("34.5"),
                                   finish_time_diff=datetime.timedelta(seconds=0.1 * i)))
    session.commit()


def _seed(session) -> None:
    _race(session, HIST, HISTORY_DAY, 1,
          [("H1", "J1", 2.4), ("H2", "J2", 3.1), ("H3", "J3", 7.8), ("H4", "J1", 15.2)],
          results=True)
    _race(session, A, DAY, 1,
          [("H1", "J1", 2.8), ("H2", "J2", 3.5), ("H3", "J3", 9.1), ("H4", "J4", 21.4)],
          results=False)
    _race(session, B, DAY, 2, [("H5", "J1", 1.9), ("H6", "J2", 4.4), ("H7", "J3", 12.0)],
          results=False)
    _race(session, C, DAY, 3, [("H8", "J1", 3.3), ("H9", "J2", None), ("H10", "J3", 6.6)],
          results=False)
    _race(session, D, DAY, 4, [("H11", "J1", 2.2), ("H12", "J2", 5.0), ("H13", "J3", 30.5)],
          results=True)
    _race(session, E, NEXT_DAY, 1, [("H14", "J1", 2.0), ("H15", "J2", 3.0)], results=False)


def _rows(session, model_version: str) -> list[MarketEvPrediction]:
    session.expire_all()
    return list(session.scalars(
        select(MarketEvPrediction)
        .where(MarketEvPrediction.model_version == model_version)
        .order_by(MarketEvPrediction.race_id, MarketEvPrediction.horse_number)
    ))


def _entries(session) -> dict[tuple[str, str], RaceHorse]:
    session.expire_all()
    return {(rh.race_id, rh.horse_id): rh for rh in session.scalars(select(RaceHorse))}


@pytest.fixture
def model_dir(tmp_path):
    # only a 2025 booster: 2026 races fall back to it and the row must record that file
    return write_model_dir(tmp_path / "mev-it-v1", years=(2025,))


def test_compute_and_persist_fills_every_column_and_replaces_race_atomically(session, model_dir):
    _seed(session)
    sha = hashlib.sha256((model_dir / "model_2025.txt").read_bytes()).hexdigest()

    first = market_ev.compute_and_persist(session, race_date_from=DAY, race_date_to=DAY,
                                          model_dir=model_dir)
    assert first["status"] == "ok" and first["reason"] is None
    assert (first["races"], first["horses"]) == (3, 10)
    assert first["races_in_range"] == 4 and first["result_pending_races"] == 2
    assert first["model_version"] == "mev-it-v1"  # defaults to the model dir's basename
    assert first["boosters"] == {"model_2025.txt": sha}

    rows = _rows(session, "mev-it-v1")
    entries = _entries(session)
    assert {r.race_id for r in rows} == {A, B, D}
    assert len(rows) == 10
    for r in rows:
        entry = entries[(r.race_id, r.horse_id)]
        assert r.horse_number == entry.horse_number
        assert Decimal(0) < r.win_prob < Decimal(1)
        assert r.odds_used == entry.odds  # exactly the stored price
        assert r.expected_return == r.win_prob * r.odds_used
        assert r.odds_observed_at == entry.updated_at
        assert r.result_pending_at_compute is (r.race_id != D)
        assert (r.booster, r.booster_sha256) == ("model_2025.txt", sha)
        assert r.logic_version == market_ev.LOGIC_VERSION
        assert str(r.run_id) == first["run_id"]
        assert r.computed_at is not None

    # a row of ANOTHER model version for race A must survive the recompute
    keep = MarketEvPrediction(
        race_id=A, model_version="other-model", horse_id="H4", horse_number=4,
        win_prob=Decimal("0.05"), odds_used=Decimal("21.4"), expected_return=Decimal("1.07"),
        odds_observed_at=datetime.datetime(2026, 9, 27, tzinfo=datetime.UTC),
        result_pending_at_compute=True, booster="model_2026.txt", booster_sha256="0" * 64,
        logic_version="x", run_id=first["run_id"],
    )
    session.add(keep)
    session.commit()

    # H4 is scratched and H1's price moves after the first compute
    session.execute(update(RaceHorse).where(RaceHorse.race_id == A, RaceHorse.horse_id == "H4")
                    .values(entry_status=EntryStatus.CANCELLED))
    session.execute(update(RaceHorse).where(RaceHorse.race_id == A, RaceHorse.horse_id == "H1")
                    .values(odds=Decimal("9.9")))
    session.commit()

    second = market_ev.compute_and_persist(session, race_date_from=DAY, race_date_to=DAY,
                                           model_dir=model_dir)
    assert (second["races"], second["horses"]) == (3, 9)
    assert second["run_id"] != first["run_id"]

    rows = _rows(session, "mev-it-v1")
    entries = _entries(session)
    by_race: dict[str, set[str]] = {}
    for r in rows:
        by_race.setdefault(r.race_id, set()).add(r.horse_id)
        assert str(r.run_id) == second["run_id"]  # every computed race was replaced
    assert by_race == {A: {"H1", "H2", "H3"}, B: {"H5", "H6", "H7"}, D: {"H11", "H12", "H13"}}
    h1 = next(r for r in rows if (r.race_id, r.horse_id) == (A, "H1"))
    assert h1.odds_used == Decimal("9.9")
    assert h1.odds_observed_at == entries[(A, "H1")].updated_at

    others = _rows(session, "other-model")
    assert [(r.race_id, r.horse_id) for r in others] == [(A, "H4")]


def test_impossible_price_race_is_not_written(session, model_dir):
    _seed(session)
    session.execute(update(RaceHorse).where(RaceHorse.race_id == B, RaceHorse.horse_id == "H6")
                    .values(odds=Decimal("0.5")))
    session.commit()
    out = market_ev.compute_and_persist(session, race_date_from=DAY, race_date_to=DAY,
                                        model_dir=model_dir, model_version="mev-x")
    assert out["races_invalid_odds"] == 1 and out["races"] == 2
    assert {r.race_id for r in _rows(session, "mev-x")} == {A, D}


def test_cli_end_to_end(session, database_url, model_dir, capsys):
    _seed(session)
    base = ["--model-dir", str(model_dir), "--database-url", database_url]

    assert main(["market-ev", "--race-id", C, *base]) == 0  # the whole race day of C
    assert capsys.readouterr().out.strip().splitlines()[-1] == (
        "OK: races=3 horses=10 from=2026-09-27 to=2026-09-27"
    )
    assert len(_rows(session, "mev-it-v1")) == 10

    assert main(["market-ev", "--date", "2026-10-03", *base]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "SKIPPED: no_races_with_odds"

    assert main(["market-ev", "--from", "2026-09-27", "--to", "2026-09-28", *base]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == (
        "OK: races=4 horses=12 from=2026-09-27 to=2026-09-28"
    )

    assert main(["market-ev", "--race-id", "209901010101", *base]) == 1
    captured = capsys.readouterr()
    assert "ERROR: market-ev failed" in captured.err
    assert "OK:" not in captured.out and "SKIPPED:" not in captured.out


def test_pending_only_keeps_the_pre_race_value_of_a_settled_race(session, model_dir, database_url,
                                                                  capsys):
    """The automatic recompute after an odds refresh (ops passes --pending-only) must not rewrite a
    race that has settled: its stored value stays the one computed on the pre-race odds."""
    _seed(session)
    first = market_ev.compute_and_persist(session, race_date_from=DAY, race_date_to=DAY,
                                          model_dir=model_dir)
    settled_before = {(r.horse_id, r.run_id, r.odds_used) for r in _rows(session, "mev-it-v1")
                      if r.race_id == D}
    assert settled_before and first["races"] == 3

    # D's price changes after the first compute (e.g. final odds filled in), A's too
    session.execute(update(RaceHorse).where(RaceHorse.race_id == D, RaceHorse.horse_id == "H11")
                    .values(odds=Decimal("2.6")))
    session.execute(update(RaceHorse).where(RaceHorse.race_id == A, RaceHorse.horse_id == "H1")
                    .values(odds=Decimal("3.3")))
    session.commit()

    second = market_ev.compute_and_persist(session, race_date_from=DAY, race_date_to=DAY,
                                           model_dir=model_dir, pending_only=True)
    assert second["status"] == "ok"
    assert (second["races"], second["races_settled_skipped"]) == (2, 1)
    rows = _rows(session, "mev-it-v1")
    assert {(r.horse_id, r.run_id, r.odds_used) for r in rows if r.race_id == D} == settled_before
    assert {str(r.run_id) for r in rows if r.race_id in (A, B)} == {second["run_id"]}
    assert next(r for r in rows if (r.race_id, r.horse_id) == (A, "H1")).odds_used == Decimal("3.3")

    # a day whose races have all settled: nothing to rewrite, reported as SKIPPED (not an error)
    base = ["--model-dir", str(model_dir), "--database-url", database_url]
    assert main(["market-ev", "--date", HISTORY_DAY.isoformat(), "--pending-only", *base]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "SKIPPED: no_pending_races"


# ------------------------------------------------------------------------ Feature 138: 2 versions


@pytest.fixture
def ens_dirs(tmp_path):
    return att.model_dirs(tmp_path)


def _all_rows(session, model_version: str) -> list[tuple]:
    """Every column of a version's rows (byte-level comparison)."""
    cols = [c.name for c in MarketEvPrediction.__table__.columns]
    session.expire_all()
    rows = session.scalars(
        select(MarketEvPrediction).where(MarketEvPrediction.model_version == model_version)
        .order_by(MarketEvPrediction.race_id, MarketEvPrediction.horse_id)
    )
    return [tuple(getattr(r, c) for c in cols) for r in rows]


def _attention_rows(session) -> tuple[list, list]:
    session.expire_all()
    return (list(session.scalars(select(AttentionRaceScan))),
            list(session.scalars(select(AttentionPick))))


def test_two_versions_share_one_run_and_one_time(session, ens_dirs):
    att.seed_card(session)
    single, ensemble = ens_dirs
    out = att.run(session, single, ensemble)
    assert out["status"] == "ok" and (out["races"], out["horses"]) == (3, 10)
    assert out["model_version"] == att.SINGLE and out["ensemble_version"] == att.ENS
    assert set(out["versions"]) == {att.SINGLE, att.ENS}
    manifest = ensemble / "ensemble_2025.json"
    assert out["versions"][att.ENS]["boosters"] == {
        "ensemble_2025.json": hashlib.sha256(manifest.read_bytes()).hexdigest()
    }

    single_rows, ens_rows = _rows(session, att.SINGLE), _rows(session, att.ENS)
    assert len(single_rows) == len(ens_rows) == 10
    assert {(r.race_id, r.horse_id) for r in single_rows} == {
        (r.race_id, r.horse_id) for r in ens_rows
    }
    assert {(str(r.run_id), r.computed_at) for r in single_rows + ens_rows} == {
        (out["run_id"], single_rows[0].computed_at)
    }
    assert {r.logic_version for r in ens_rows} == {market_ev.ENSEMBLE_LOGIC_VERSION}
    assert {r.logic_version for r in single_rows} == {market_ev.LOGIC_VERSION}
    assert {r.booster for r in ens_rows} == {"ensemble_2025.json"}
    assert {r.booster for r in single_rows} == {"model_2025.txt"}
    for r in ens_rows:
        assert r.expected_return == r.win_prob * r.odds_used
    # the two models really differ (the ensemble is not the single seed written twice)
    assert [r.win_prob for r in single_rows] != [r.win_prob for r in ens_rows]


@pytest.mark.parametrize("where", ["ensemble_rows", "picks"])
def test_a_failure_rolls_back_both_versions_and_the_scans(session, ens_dirs, monkeypatch,
                                                           where):
    att.seed_card(session)
    single, ensemble = ens_dirs
    if where == "ensemble_rows":
        real = market_ev._replace_rows

        def _replace(sess, pred, **kw):
            if kw["model_version"] == att.ENS:
                raise RuntimeError("ensemble insert failed")
            return real(sess, pred, **kw)

        monkeypatch.setattr(market_ev, "_replace_rows", _replace)
    else:
        from horseracing_training import attention_picks

        def _record(sess, **kw):
            raise RuntimeError("pick insert failed")

        monkeypatch.setattr(attention_picks, "record_attention", _record)
    with pytest.raises(RuntimeError, match="failed"):
        att.run(session, single, ensemble)
    assert _rows(session, att.SINGLE) == [] and _rows(session, att.ENS) == []
    assert _attention_rows(session) == ([], [])


@contextlib.contextmanager
def _lock_keys(engine):
    """The keys of every ``pg_advisory_xact_lock`` issued while the block runs, in order."""
    keys: list[str] = []

    def _capture(_conn, _cursor, statement, parameters, _context, _many):
        if "pg_advisory_xact_lock" in statement:
            keys.append(parameters["k"])

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        yield keys
    finally:
        event.remove(engine, "before_cursor_execute", _capture)


def test_advisory_locks_are_per_date_single_then_ensemble(session, engine, ens_dirs):
    att.seed_card(session)
    single, ensemble = ens_dirs
    with _lock_keys(engine) as keys:
        att.run(session, single, ensemble, day_from=att.DAY, day_to=att.NEXT_DAY)
    days = (att.DAY.isoformat(), att.NEXT_DAY.isoformat())
    # the compute transaction's per-date locks (ascending, single then ensemble), then the
    # checkpoint step's own lock in its separate transaction after the commit
    assert keys == [f"market_ev:{v}:{d}" for d in days for v in (att.SINGLE, att.ENS)] + [
        "attention_checkpoints"]


def test_ensemble_only_takes_only_the_ensemble_locks(session, engine, ens_dirs):
    """D24: a backfill run never writes the single-seed rows, so it never serialises on them."""
    att.seed_card(session)
    single, ensemble = ens_dirs
    with _lock_keys(engine) as keys:
        out = att.run(session, single, ensemble, day_from=att.DAY, day_to=att.NEXT_DAY,
                      ensemble_only=True)
    assert out["status"] == "ok"
    days = (att.DAY.isoformat(), att.NEXT_DAY.isoformat())
    assert keys == [f"market_ev:{att.ENS}:{d}" for d in days] + ["attention_checkpoints"]


def test_versions_must_compute_the_same_rows(session, ens_dirs, monkeypatch):
    att.seed_card(session)
    single, ensemble = ens_dirs
    real = market_ev.predict_ensemble
    monkeypatch.setattr(market_ev, "predict_ensemble", lambda m, f: real(m, f).iloc[1:])
    with pytest.raises(RuntimeError, match="disagree"):
        att.run(session, single, ensemble)
    assert _rows(session, att.SINGLE) == [] and _rows(session, att.ENS) == []
    assert _attention_rows(session) == ([], [])


def test_pending_only_with_the_ensemble_keeps_settled_rows_of_both_versions(session, ens_dirs):
    att.seed_card(session)
    single, ensemble = ens_dirs
    first = att.run(session, single, ensemble)
    settled = {v: [r for r in _all_rows(session, v) if r[0] == att.D] for v in (att.SINGLE,
                                                                                att.ENS)}
    assert all(settled.values())
    session.execute(update(RaceHorse).where(RaceHorse.race_id == att.D,
                                            RaceHorse.horse_id == "H11")
                    .values(odds=Decimal("2.6")))
    session.commit()
    second = att.run(session, single, ensemble, pending_only=True)
    assert second["status"] == "ok" and second["races_settled_skipped"] == 1
    assert second["picks"]["races_first_computed"] == 0
    for v in (att.SINGLE, att.ENS):
        rows = _all_rows(session, v)
        assert [r for r in rows if r[0] == att.D] == settled[v]
        assert {str(r.run_id) for r in _rows(session, v) if r.race_id in (att.A, att.B)} == {
            second["run_id"]
        }
    assert first["run_id"] != second["run_id"]


def test_ensemble_only_leaves_the_single_seed_rows_untouched(session, ens_dirs):
    att.seed_card(session)
    single, ensemble = ens_dirs
    att.run(session, single, None)  # 137's single-version rows, computed first
    before = _all_rows(session, att.SINGLE)
    assert before and _rows(session, att.ENS) == []

    out = att.run(session, single, ensemble, ensemble_only=True)
    assert out["status"] == "ok" and list(out["versions"]) == [att.ENS]
    assert out["model_version"] == att.ENS
    assert out["logic_version"] == market_ev.ENSEMBLE_LOGIC_VERSION
    assert _all_rows(session, att.SINGLE) == before  # not one byte changed
    ens_rows = _rows(session, att.ENS)
    assert len(ens_rows) == 10 and {str(r.run_id) for r in ens_rows} == {out["run_id"]}
    scans, _ = _attention_rows(session)
    assert {s.race_id for s in scans} == {att.A, att.B, att.D}
    assert {str(s.run_id) for s in scans} == {out["run_id"]}


def test_cli_end_to_end_with_the_ensemble(session, database_url, ens_dirs, capsys):
    att.seed_card(session)
    single, ensemble = ens_dirs
    base = ["--model-dir", str(single), "--ensemble-dir", str(ensemble),
            "--database-url", database_url]
    assert main(["market-ev", "--date", att.DAY.isoformat(), *base]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    picks = len(_attention_rows(session)[1])
    assert lines[-1] == (
        f"OK: races=3 horses=10 from={att.DAY} to={att.DAY} versions=2 picks={picks} "
        "checkpoints=ok"
    )
    assert lines[-2].startswith("attention-checkpoints: written=- pending=- ")

    assert main(["market-ev", "--date", att.DAY.isoformat(), "--ensemble-only", *base]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == (
        f"OK: races=3 horses=10 from={att.DAY} to={att.DAY} versions=ens picks=0 checkpoints=ok"
    )
    # a mismatched pair is refused (the ERROR path, nothing written)
    bad = ["--model-dir", str(single), "--ensemble-dir", str(ensemble), "--ensemble-version",
           "mev-ens15-v9", "--database-url", database_url]
    assert main(["market-ev", "--date", att.DAY.isoformat(), *bad]) == 1
    assert "ERROR: market-ev failed" in capsys.readouterr().err
