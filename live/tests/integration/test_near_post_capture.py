"""Feature 174: near-post capture against a migrated PostgreSQL (fake fetcher / fake predictor)."""

from __future__ import annotations

import datetime
import json
import uuid

import pandas as pd
import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import DecisionQuote, Race, RaceHorse
from horseracing_scrape.fetch import FetchRefused
from sqlalchemy import select

from horseracing_live import near_post as np_
from tests._capture_support import POST_TIME, RACE_DATE, RACE_ID, seed_race, settle_race

pytestmark = pytest.mark.integration


class FakePredictor:
    def __init__(self):
        self.calls = []

    def predict(self, rows: pd.DataFrame, *, race_date):
        self.calls.append(rows.copy())
        assert rows["race_ok"].all()
        pred = [
            {"horse_id": str(h), "horse_number": int(n), "p_ens": 0.25, "ev_ens": 0.25 * float(o),
             "p_single": 0.2, "ev_single": 0.2 * float(o), "p_mix": None, "ev_mix": None}
            for h, n, o in zip(rows["horse_id"], rows["horse_number"], rows["odds"], strict=True)
        ]
        return np_.PredictionBundle(
            pred=pred, mix_status="no_coefficients", mix_version=None, mix_coefficient_year=None,
            ensemble_version="mev-ens15-v1", ensemble_manifest="ensemble_2026.json",
            ensemble_manifest_sha256="e" * 64, single_version="mev-binary-v2",
            single_booster="model_2026.txt", single_booster_sha256="b" * 64,
        )


class FakeFetcher:
    def __init__(self, payload: str | None = None, *, refuse: bool = False):
        self.payload = payload
        self.refuse = refuse
        self.calls = 0
        self.deadlines = []

    def set_deadline(self, deadline):
        self.deadlines.append(deadline)

    def get(self, url, *, use_cache=True):
        assert use_cache is False and RACE_ID in url or "race_id=" in url
        self.calls += 1
        if self.refuse:
            raise FetchRefused("source_cooldown")
        return self.payload


def payload(n=4, missing=None):
    win = {f"{i:02d}": [str(2.0 + i), "0.0", str(i)] for i in range(1, n + 1) if i != missing}
    return json.dumps({"data": {"odds": {"1": win}, "official_datetime": "2026-07-26 14:56:40"}})


def day_features(n=4, race_id=RACE_ID):
    frame = pd.DataFrame({
        "race_id": [race_id] * n, "horse_id": [f"H{i:02d}" for i in range(1, n + 1)],
        "horse_number": [float(i) for i in range(1, n + 1)], "odds": [3.0] * n, "popularity": [1.0] * n,
    })
    return np_.DayFeatures(day=RACE_DATE, frame=frame, sha256=np_.frame_sha256(frame),
                           built_at=POST_TIME - datetime.timedelta(hours=6))


def sched(post_time=POST_TIME, race_id=RACE_ID):
    return np_.schedule_day([(race_id, post_time)])[0]


IN_WINDOW = POST_TIME - datetime.timedelta(seconds=181)


def seed_other(session, race_id, post_time, n=4):
    """A second race on the same day reusing the horses seed_race already created."""
    session.add(Race(race_id=race_id, race_number=2, race_date=RACE_DATE, post_time=post_time))
    session.flush()
    for i in range(1, n + 1):
        session.add(RaceHorse(race_id=race_id, horse_id=f"H{i:02d}", horse_number=i, odds=3.0,
                              popularity=i, entry_status=EntryStatus.STARTED))
    session.flush()


def _capture(session, **over):
    kw = dict(scheduled=sched(), fetcher=FakeFetcher(payload()), predictor=FakePredictor(),
              day_features=day_features(), run_id=uuid.uuid4(), clock=lambda: IN_WINDOW)
    kw.update(over)
    return np_.capture_race(session, **kw)


def test_captures_once_and_freezes_quote_and_predictions(session):
    seed_race(session)
    fetcher = FakeFetcher(payload())
    pred = FakePredictor()
    report = _capture(session, fetcher=fetcher, predictor=pred)
    assert report.status == "captured" and report.seconds_to_post == 181
    row = session.scalar(select(DecisionQuote).where(DecisionQuote.race_id == RACE_ID))
    assert row.n == 4 and row.capture_target == "t_minus_3" and row.source == "netkeiba"
    assert [r["win_odds"] for r in row.field] == [3.0, 4.0, 5.0, 6.0]
    assert [r["popularity"] for r in row.field] == [1, 2, 3, 4]
    assert row.official_at.isoformat() == "2026-07-26T05:56:40+00:00"
    assert row.pred[0]["ev_ens"] == pytest.approx(0.75) and row.mix_status == "no_coefficients"
    assert row.features_sha256 == day_features().sha256 and row.logic_version == np_.LOGIC_VERSION
    assert len(fetcher.deadlines) == 1
    # the predictor saw the quote through the production market block
    assert pred.calls[0]["odds"].tolist() == [3.0, 4.0, 5.0, 6.0] and pred.calls[0]["race_ok"].all()
    # second attempt: existence alone prevents a fetch
    again = _capture(session, fetcher=fetcher)
    assert (again.status, again.reason) == ("skipped", "already_captured") and fetcher.calls == 1


@pytest.mark.parametrize("delta, reason", [(-120, "window_early"), (120, "window_late")])
def test_outside_window_never_fetches(session, delta, reason):
    seed_race(session)
    fetcher = FakeFetcher(payload())
    report = _capture(session, fetcher=fetcher,
                      clock=lambda: POST_TIME - datetime.timedelta(seconds=180 - delta))
    assert (report.status, report.reason) == ("skipped", reason) and fetcher.calls == 0


def test_dry_run_stops_before_the_fetch(session):
    seed_race(session)
    fetcher = FakeFetcher(payload())
    report = _capture(session, fetcher=fetcher, dry_run=True)
    assert (report.status, report.reason, report.n) == ("dry_run", "would_fetch", 4)
    assert fetcher.calls == 0
    assert session.scalar(select(DecisionQuote.decision_quote_id)) is None


def test_settled_race_and_field_mismatch_and_incomplete_quote(session):
    seed_race(session)
    settle_race(session)
    assert _capture(session).reason == "result_settled"
    session.rollback()
    seed_race(session)
    fetcher = FakeFetcher(payload())
    r = _capture(session, fetcher=fetcher, day_features=day_features(n=3))
    assert r.reason == "field_mismatch" and fetcher.calls == 0
    r = _capture(session, fetcher=FakeFetcher(payload(missing=2)))
    assert (r.status, r.reason) == ("skipped", "quote_incomplete")
    assert session.scalar(select(DecisionQuote.decision_quote_id)) is None


def test_refusal_is_skipped_and_stops_the_day(session):
    seed_race(session)
    other = "202607260102"
    seed_other(session, other, POST_TIME + datetime.timedelta(minutes=30))
    fetcher = FakeFetcher(payload(), refuse=True)
    feats = pd.concat([day_features().frame, day_features(race_id=other).frame], ignore_index=True)
    dayf = np_.DayFeatures(day=RACE_DATE, frame=feats, sha256=np_.frame_sha256(feats), built_at=IN_WINDOW)
    schedule = np_.schedule_day([(RACE_ID, POST_TIME), (other, POST_TIME + datetime.timedelta(minutes=30))])
    now = {"t": IN_WINDOW - datetime.timedelta(seconds=5)}
    slept = []

    def sleep(s):
        slept.append(s)
        now["t"] += datetime.timedelta(seconds=s)

    summary = np_.run_day(session, day=RACE_DATE, schedule=schedule, fetcher=fetcher,
                          predictor=FakePredictor(), day_features=dayf, clock=lambda: now["t"], sleep=sleep)
    assert summary.stopped_early.startswith("source_refused:")
    assert summary.reasons == {summary.stopped_early: 1, "stopped_after_refusal": 1}
    assert fetcher.calls == 1 and session.scalar(select(DecisionQuote.decision_quote_id)) is None


def test_run_day_waits_for_each_window_and_commits_rows(session):
    seed_race(session)
    other = "202607260102"
    seed_other(session, other, POST_TIME)  # same post time → t_minus_4 first
    feats = pd.concat([day_features().frame, day_features(race_id=other).frame], ignore_index=True)
    dayf = np_.DayFeatures(day=RACE_DATE, frame=feats, sha256=np_.frame_sha256(feats), built_at=IN_WINDOW)
    schedule = np_.schedule_day([(RACE_ID, POST_TIME), (other, POST_TIME)])
    assert [s.capture_target for s in schedule] == ["t_minus_4", "t_minus_3"]
    now = {"t": POST_TIME - datetime.timedelta(minutes=20)}
    slept = []

    def sleep(s):
        slept.append(s)
        now["t"] += datetime.timedelta(seconds=s)

    fetcher = FakeFetcher(payload())
    summary = np_.run_day(session, day=RACE_DATE, schedule=schedule, fetcher=fetcher,
                          predictor=FakePredictor(), day_features=dayf, clock=lambda: now["t"], sleep=sleep)
    assert summary.counts == {"captured": 2} and fetcher.calls == 2
    assert slept == pytest.approx([20 * 60 - 300, 60])  # to the first window, then +60 s to the next
    rows = session.execute(select(DecisionQuote.race_id, DecisionQuote.capture_target)).all()
    assert dict(rows) == {other: "t_minus_4", RACE_ID: "t_minus_3"}
    assert summary.in_window_capture_rate == 1.0 and summary.official_lag_median_s is not None
    doc = summary.as_dict()
    assert doc["n_scheduled"] == 2 and len(doc["races"]) == 2
