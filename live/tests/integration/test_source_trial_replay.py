"""178 T062/T063/T064 (DB): the isolation trial on a migrated replica — JRDB files replayed at
their arrival times through the production writer command, the T−3 quote from a (fake) official
単複 page through ``capture_official``, every race scored at trial end; an isolation violation stops
before anything is written; the emergency mode creates the field from the official 出馬表 and the
quotes still land."""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import text

from horseracing_live import near_post as np_
from horseracing_live import source_trial as st

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scrape" / "tests"))
import _jrdb_synth as J  # noqa: E402  (the 178 synthetic JRDB material lives with the scrape tests)

pytestmark = pytest.mark.integration
JST = st.JST
DAY = J.DAY  # 2026-09-13, the public sample moved to the netkeiba era
FIXTURES = REPO / "scrape" / "tests" / "fixtures" / "jra"


# --------------------------------------------------------------------------- fakes


class FakeClock:
    def __init__(self, start: datetime.datetime):
        self.t = start

    def __call__(self) -> datetime.datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t = self.t + datetime.timedelta(seconds=max(0.0, seconds))

    def monotonic(self) -> float:
        return self.t.timestamp()


class FakePredictor:
    def predict(self, rows: pd.DataFrame, *, race_date):
        n = len(rows)
        return np_.PredictionBundle(
            pred=[{"horse_id": str(h), "p": 1.0 / n} for h in rows["horse_id"]],
            mix_status="unavailable",
            mix_version=None,
            mix_coefficient_year=None,
            ensemble_version="fake-ens",
            ensemble_manifest="fake.json",
            ensemble_manifest_sha256="0" * 64,
            single_version="fake-single",
            single_booster="fake.txt",
            single_booster_sha256="1" * 64,
        )


def _features_from_db(session, day):
    rows = session.execute(
        text(
            "SELECT rh.race_id, rh.horse_id, rh.horse_number, r.distance FROM race_horses rh "
            "JOIN races r USING (race_id) WHERE r.race_date = :d AND rh.entry_status = 'started' "
            "ORDER BY 1, 3"
        ),
        {"d": day},
    ).all()
    frame = pd.DataFrame(
        {
            "race_id": [str(r[0]) for r in rows],
            "horse_id": [str(r[1]) for r in rows],
            "horse_number": [float(r[2]) for r in rows],
            "odds": [float("nan")] * len(rows),
            "popularity": [float("nan")] * len(rows),
            "distance": [float(r[3] or 1600) for r in rows],
            "race_date": pd.to_datetime([day] * len(rows)),
        }
    )
    return np_.DayFeatures(
        day=day, frame=frame, sha256=np_.frame_sha256(frame), built_at=datetime.datetime.now(JST)
    )


class _Resp:
    def __init__(self, text):
        self.text = text


def _fake_official(race_ids_by_want: dict[tuple[str, int], str], clock: FakeClock):
    """A walker that yields one 単複 page per wanted race and a parse that quotes 1〜18."""

    class Walker:
        def __init__(self, fetcher, date):
            self.date = date

        def odds_pages(self, *, venue, want):
            for v, no in sorted(want):
                yield None, f"{no}R", _Resp(race_ids_by_want[(v, no)])

    def parse(text_, expected_date):
        return SimpleNamespace(
            race_id=text_,
            official_at=clock() - datetime.timedelta(seconds=30),
            rows=tuple(
                SimpleNamespace(horse_number=n, win_odds=2.0 + n, place_low=1.1, place_high=1.5)
                for n in range(1, 19)
            ),
        )

    return Walker, parse


def _jrdb_day_files(root: Path) -> Path:
    """``root/<KIND>/20260913/…`` — BAC/SED/SRB shifted from the public day, KYI/UKC/HJC synthesized
    from the same SED rows (the public KYI/UKC/HJC samples are other days)."""

    res = J.shifted_results()
    stamp = DAY.strftime("%Y%m%d")
    for kind in ("BAC", "SED", "SRB"):
        src = J.public_sample(kind)
        folder = root / kind / stamp
        folder.mkdir(parents=True)
        (folder / f"{kind}260913.txt").write_bytes(
            J.shift_raw(kind, src.read_bytes(), year_from=2008, year_to=2026)
        )
    (root / "KYI" / stamp).mkdir(parents=True)
    (root / "KYI" / stamp / "KYI260913.txt").write_bytes(J.synth_kyi_raw(res))
    (root / "UKC" / stamp).mkdir(parents=True)
    (root / "UKC" / stamp / "UKC260913.txt").write_bytes(J.synth_ukc_raw(res, race_year=2026))
    (root / "HJC" / stamp).mkdir(parents=True)
    (root / "HJC" / stamp / "HJC260913.txt").write_bytes(J.synth_hjc_raw(res))
    return root


def _policy(tmp_path) -> Path:
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"jrdb_enabled": True, "collection_paused": False}), encoding="utf-8")
    return p


def _want_map(results) -> dict[tuple[str, int], str]:
    out = {}
    for r in results:
        out[(r.key.venue, r.key.r)] = J.race_id_from_key(r.key, file_year=J.YEAR)
    return out


# --------------------------------------------------------------------------- the day


def test_full_day_replay_satisfies_every_race(database_url, engine, tmp_path):
    files = _jrdb_day_files(tmp_path / "files")
    clock = FakeClock(datetime.datetime(2026, 9, 12, 19, 0, tzinfo=JST))
    walker, parse = _fake_official(_want_map(J.shifted_results()), clock)
    arrivals = {
        **st.default_arrivals(DAY),
        "SED": datetime.datetime(
            2026, 9, 13, 20, 0, tzinfo=JST
        ),  # the formal file, replayed same day
        "SRB": datetime.datetime(2026, 9, 13, 20, 0, tzinfo=JST),
        "HJC": datetime.datetime(2026, 9, 13, 20, 30, tzinfo=JST),
    }
    out = tmp_path / "trial" / "2026-09-13.jsonl"
    summary = st.run_trial(
        DAY,
        db_url=database_url,
        files_dir=files,
        out_path=out,
        config_path=tmp_path / "trial" / "config.json",
        policy_path=_policy(tmp_path),
        jra_fetcher=object(),
        predictor=FakePredictor(),
        arrivals=arrivals,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        features_builder=_features_from_db,
        walker_factory=walker,
        parse=parse,
        replay_mode="fake_clock",
    )
    assert summary.error is None, (summary.error, [r.reason for r in summary.races if not r.ok][:5])
    # 23, not 24: 174's day_races leaves the 障害 race out (the sample's 中山 4R), as in production
    assert summary.exit_code == 0 and len(summary.races) == 23 and summary.n_ok == 23
    assert summary.isolation_violations == [] and summary.emergency is None
    kinds = [e["event"] for e in summary.events]
    assert kinds[0] == "arrive" and summary.events[0]["what"] == "entries"
    assert kinds.count("capture") == 23 and "emergency_check" in kinds
    assert summary.events[kinds.index("emergency_check")]["result"] == "jrdb_entries_present"
    assert (
        summary.expected_races == 24 and summary.missing_races == []
    )  # the 障害 race has rows too
    for r in summary.races:
        assert r.entries_source == "jrdb" and r.master_source == "jrdb" and not r.emergency
        assert r.masters_created_in_trial_by_source == {
            "jrdb": r.masters_created_in_trial_by_source["jrdb"]
        }
        assert r.quote_ok and r.results_at and r.final_odds_at and r.payouts_at
        assert r.deadline_delta_s is not None and r.deadline_delta_s > 3600
        assert r.features_ready_at and r.capture_started_at and r.fetch_seconds == 0.0
    with engine.connect() as c:
        quotes = c.execute(
            text("SELECT source, logic_version, n FROM decision_quotes ORDER BY race_id")
        ).all()
        assert len(quotes) == 23 and {q[0] for q in quotes} == {"jra_official"}
        assert all(q[1] == st.LOGIC_VERSION for q in quotes) and all(q[2] >= 2 for q in quotes)
        assert c.execute(text("SELECT count(*) FROM official_win_payouts")).scalar_one() == 24
        assert (
            c.execute(text("SELECT count(DISTINCT race_id) FROM race_results")).scalar_one() == 24
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM race_horses WHERE horse_id LIKE 'nk:%'")
            ).scalar_one()
            == 0
        )
        files_rows = c.execute(
            text("SELECT kind, variant, status FROM jrdb_files ORDER BY 1")
        ).all()
        assert {k for k, _v, _s in files_rows} == {"BAC", "HJC", "KYI", "SED", "SRB", "UKC"}
        assert dict((k, v) for k, v, _s in files_rows)["SED"] == "archive"
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert sum(1 for x in lines if x["record"] == "race") == 23
    assert lines[-1]["record"] == "summary" and lines[-1]["n_ok"] == 23
    cfg = json.loads((tmp_path / "trial" / "config.json").read_text(encoding="utf-8"))
    assert cfg["arrivals"]["SED"].startswith("2026-09-13T20:00") and cfg["frozen_at"]
    assert cfg["replay_mode"] == "fake_clock" and cfg["arrivals_basis"] == "given"
    assert set(cfg["file_sha256"]) == {
        "BAC/BAC260913.txt",
        "SED/SED260913.txt",
        "SRB/SRB260913.txt",
        "KYI/KYI260913.txt",
        "UKC/UKC260913.txt",
        "HJC/HJC260913.txt",
    }
    assert (
        cfg["inherited_state"]["races_before_day"] == 0
        and cfg["inherited_state"]["jrdb_person_mappings"]["n"] == 0
    )
    # the config is append-only: a second trial on the same path is refused before anything runs
    with pytest.raises(FileExistsError):
        st.run_trial(
            DAY,
            db_url=database_url,
            files_dir=files,
            out_path=out,
            config_path=tmp_path / "trial" / "config.json",
            policy_path=_policy(tmp_path),
            jra_fetcher=object(),
            predictor=FakePredictor(),
            clock=clock,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )


def test_isolation_violation_stops_before_any_write(database_url, engine, tmp_path):
    with engine.begin() as c:  # a netkeiba job of the trial day = netkeiba supplied something
        c.execute(
            text(
                "INSERT INTO ingestion_jobs (ingestion_job_id, source, job_type, status, created_at) "
                "VALUES (gen_random_uuid(), 'netkeiba', 'odds', 'succeeded', :t)"
            ),
            {"t": datetime.datetime(2026, 9, 13, 10, 0, tzinfo=JST)},
        )
    files = _jrdb_day_files(tmp_path / "files")
    clock = FakeClock(datetime.datetime(2026, 9, 12, 19, 0, tzinfo=JST))
    out = tmp_path / "t.jsonl"
    summary = st.run_trial(
        DAY,
        db_url=database_url,
        files_dir=files,
        out_path=out,
        config_path=tmp_path / "c.json",
        policy_path=_policy(tmp_path),
        jra_fetcher=object(),
        predictor=FakePredictor(),
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert summary.exit_code == 2 and summary.error == "isolation_violated"
    assert summary.isolation_violations == ["netkeiba_jobs_today=1"]
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM races")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM jrdb_files")).scalar_one() == 0
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert lines == [
        {
            "record": "isolation_violated",
            "day": "2026-09-13",
            "violations": ["netkeiba_jobs_today=1"],
        }
    ]


def test_late_kyi_triggers_the_emergency_mode_and_the_quotes_still_land(
    database_url, engine, tmp_path
):
    """No JRDB 前日 file by 08:00 → the official 出馬表 creates the field; the T−3 records are taken;
    the day fails on the inputs JRDB never delivered (results/odds/payouts), reported per race."""

    from horseracing_scrape.jra.parse.changes import parse_changes
    from horseracing_scrape.jra.parse.entries import parse_entries_day

    day = datetime.date(2026, 11, 1)
    page = parse_entries_day(
        (FIXTURES / "entries_tokyo_20261101.sjis.html").read_bytes().decode("cp932"),
        expected_date=day,
    )
    changes = parse_changes(
        (FIXTURES / "changes_20261101.sjis.html").read_bytes().decode("cp932"), expected_date=day
    )
    fetched = datetime.datetime(2026, 11, 1, 8, 1, tzinfo=JST)

    def official_entries(d, fetcher):
        assert d == day
        return [(fetched, page)], changes

    clock = FakeClock(datetime.datetime(2026, 10, 31, 19, 0, tzinfo=JST))
    want = {(b.payload.race.venue_code, b.race_no): b.race_id for b in page.published()}
    walker, parse = _fake_official(want, clock)
    files = tmp_path / "files"
    files.mkdir()  # nothing from JRDB at all
    out = tmp_path / "e.jsonl"
    summary = st.run_trial(
        day,
        db_url=database_url,
        files_dir=files,
        out_path=out,
        config_path=tmp_path / "c.json",
        policy_path=_policy(tmp_path),
        jra_fetcher=object(),
        predictor=FakePredictor(),
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        features_builder=_features_from_db,
        walker_factory=walker,
        parse=parse,
        official_entries=official_entries,
    )
    assert summary.emergency and summary.emergency["emergency_races_created"] == 12
    assert summary.emergency["emergency_rows_created"] > 100
    assert len(summary.races) == 12 and all(r.emergency for r in summary.races)
    assert all(
        r.entries_source == "jra_official" and r.master_source == "jra_official"
        for r in summary.races
    )
    assert all(r.quote_ok for r in summary.races), [r.quote_reason for r in summary.races]
    assert summary.exit_code == 1 and summary.n_failed == 12
    assert all(
        "results_missing" in r.reason and "payouts_missing" in r.reason for r in summary.races
    )
    assert not any("netkeiba" in r.reason for r in summary.races)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM decision_quotes")).scalar_one() == 12
        srcs = (
            c.execute(
                text(
                    "SELECT DISTINCT source FROM race_horse_field_provenance WHERE field_group = 'entry_static'"
                )
            )
            .scalars()
            .all()
        )
        assert srcs == ["jra_official"]
