"""178 T064a: the production capture with ``quote_source='jra_official'`` — the 174 gate order,
the official 単複 page through the 176 walker (the fixture fetcher), ``source='jra_official'``,
no popularity (the official page has none), and the netkeiba path left as it was."""

from __future__ import annotations

import datetime
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest
from horseracing_db.models import DecisionQuote, Horse, Race, RaceHorse
from sqlalchemy import select

from horseracing_live import near_post as np_

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scrape" / "tests"))
import jra_fixtures  # noqa: E402

sys.modules.setdefault("tests.jra_fixtures", jra_fixtures)  # _jra_fake imports it by that name
from _jra_fake import FakeJraFetcher  # noqa: E402
from horseracing_scrape.jra.parse.odds import parse_win_odds  # noqa: E402

pytestmark = pytest.mark.integration
JST = datetime.timezone(datetime.timedelta(hours=9))
DAY = datetime.date(2026, 11, 1)
POST = datetime.datetime(2026, 11, 1, 15, 40, tzinfo=JST)


class _Predictor:
    def predict(self, rows: pd.DataFrame, *, race_date):
        return np_.PredictionBundle(
            pred=[{"horse_id": str(h), "p": 1.0 / len(rows)} for h in rows["horse_id"]],
            mix_status="unavailable",
            mix_version=None,
            mix_coefficient_year=None,
            ensemble_version="e",
            ensemble_manifest="m",
            ensemble_manifest_sha256="0" * 64,
            single_version="s",
            single_booster="b",
            single_booster_sha256="1" * 64,
        )


def _seed(session):
    odds = parse_win_odds(
        jra_fixtures.jra_text("odds_tokyo11r_20261101_0822.sjis.html"), expected_date=DAY
    )
    rid = odds.race_id
    session.add(
        Race(
            race_id=rid,
            race_date=DAY,
            race_number=11,
            venue_code="05",
            post_time=POST,
            distance=1600,
            track_type="芝",
        )
    )
    rows = [r for r in odds.rows if r.horse_number is not None and r.entry_status == "started"]
    for r in rows:
        session.add(Horse(horse_id=r.horse_id, horse_name=r.horse_name))
    session.flush()
    for r in rows:
        session.add(
            RaceHorse(
                race_id=rid,
                horse_id=r.horse_id,
                horse_number=r.horse_number,
                entry_status="started",
            )
        )
    session.commit()
    frame = pd.DataFrame(
        {
            "race_id": [rid] * len(rows),
            "horse_id": [r.horse_id for r in rows],
            "horse_number": [float(r.horse_number) for r in rows],
            "odds": [float("nan")] * len(rows),
            "popularity": [float("nan")] * len(rows),
            "distance": [1600.0] * len(rows),
        }
    )
    features = np_.DayFeatures(day=DAY, frame=frame, sha256=np_.frame_sha256(frame), built_at=POST)
    return rid, odds, features


def test_official_source_captures_through_the_walker(session):
    rid, odds, features = _seed(session)
    (s,) = np_.schedule_day([(rid, POST)])
    clock = lambda: s.target_at  # noqa: E731 — inside the window
    report = np_.capture_race(
        session,
        scheduled=s,
        fetcher=FakeJraFetcher(now=s.target_at),
        predictor=_Predictor(),
        day_features=features,
        run_id=uuid.uuid4(),
        clock=clock,
        monotonic=lambda: 0.0,
        quote_source="jra_official",
    )
    assert (report.status, report.reason) == ("captured", "ok"), report
    session.commit()
    row = session.execute(select(DecisionQuote).where(DecisionQuote.race_id == rid)).scalar_one()
    assert row.source == "jra_official" and row.logic_version.endswith(";src=jra_official")
    assert row.official_at == odds.official_at and row.n == len(features.frame)
    by_number = {f["horse_number"]: f for f in row.field}
    assert by_number[1]["win_odds"] == 2.7 and by_number[1]["popularity"] is None
    assert by_number[1]["place_low"] == 1.2  # the official page carries the place range too
    assert len(row.quote_digest) == 64 and row.field_digest == np_.field_digest(
        [(f["horse_id"], f["horse_number"]) for f in row.field]
    )


def test_official_source_never_falls_back_to_netkeiba(session):
    """A fetcher whose official walk fails yields ``failed:fetch_failed`` — no netkeiba request."""

    rid, _odds, features = _seed(session)
    (s,) = np_.schedule_day([(rid, POST)])

    class Netkeiba:  # the 174 fetcher: must never be asked anything on the official path
        calls = 0

        def get(self, *a, **k):
            Netkeiba.calls += 1
            raise AssertionError("netkeiba was asked")

        def post(self, *a, **k):  # the official walker would call this: make it fail
            raise RuntimeError("official site down")

    report = np_.capture_race(
        session,
        scheduled=s,
        fetcher=Netkeiba(),
        predictor=_Predictor(),
        day_features=features,
        run_id=uuid.uuid4(),
        clock=lambda: s.target_at,
        monotonic=lambda: 0.0,
        quote_source="jra_official",
    )
    assert report.status == "failed" and report.reason.startswith("fetch_failed")
    assert Netkeiba.calls == 0
    assert (
        session.scalar(select(DecisionQuote.decision_quote_id).where(DecisionQuote.race_id == rid))
        is None
    )
