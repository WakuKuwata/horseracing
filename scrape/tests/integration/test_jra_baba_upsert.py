"""176 T086: track conditions are idempotent per value, never from the future (INV-B1/B2)."""

from __future__ import annotations

import argparse
import dataclasses
import datetime

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import JraTrackCondition
from sqlalchemy import func, select

from horseracing_scrape.jra import baba_cli
from horseracing_scrape.jra.cli import run_command
from horseracing_scrape.jra.parse.baba import parse_cushion, parse_kaisai_info, parse_moisture
from horseracing_scrape.jra.upsert_jra import upsert_jra_baba
from tests._jra_fake import FakeJraFetcher
from tests.jra_fixtures import fetched_at, jra_text

pytestmark = pytest.mark.integration
CUSHION = "baba_cushion_20261101.sjis.html"
MOIST = "baba_moist_20261101.sjis.html"
ACCESS_J = "accessJ_20261101.json"
D = datetime.date(2026, 11, 1)


def _n(session):
    return session.scalar(select(func.count()).select_from(JraTrackCondition))


def _write(session, name, parser, readings=None, at=None):
    at = at or fetched_at(name)
    rs = readings if readings is not None else parser(jra_text(name), fetched_at=fetched_at(name))
    c = upsert_jra_baba(session, rs, fetched_at=at, raw_sha256="sha-" + name)
    session.commit()
    return c, rs


def test_same_fragment_twice_changes_nothing(session):
    c1, rs = _write(session, MOIST, parse_moisture)
    assert c1.extra["baba_rows_written"] == len(rs) == _n(session)
    row = session.get(JraTrackCondition, (rs[0].venue_code, rs[0].measured_at, rs[0].item))
    first_fetch = row.fetched_at
    c2, _ = _write(
        session, MOIST, parse_moisture, at=fetched_at(MOIST) + datetime.timedelta(hours=1)
    )
    assert c2.written == 0 and _n(session) == len(rs)
    session.expire_all()
    assert (
        session.get(JraTrackCondition, (rs[0].venue_code, rs[0].measured_at, rs[0].item)).fetched_at
        == first_fetch
    )
    # four simultaneous moisture readings of one venue do not collide (item carries the object)
    same_time = [
        r for r in rs if r.venue_code == rs[0].venue_code and r.measured_at == rs[0].measured_at
    ]
    assert len({r.item for r in same_time}) == len(same_time) >= 2


def test_changed_value_overwrites_and_moves_fetched_at(session):
    _, rs = _write(session, CUSHION, parse_cushion)
    later = fetched_at(CUSHION) + datetime.timedelta(hours=2)
    changed = [dataclasses.replace(rs[0], value_num=(rs[0].value_num or 0) + 0.5), *rs[1:]]
    c, _ = _write(session, CUSHION, parse_cushion, readings=changed, at=later)
    assert c.extra.get("baba_value_changed") == 1 and _n(session) == len(rs)
    session.expire_all()
    row = session.get(JraTrackCondition, (rs[0].venue_code, rs[0].measured_at, rs[0].item))
    assert row.fetched_at == later and float(row.value_num) == pytest.approx(rs[0].value_num + 0.5)


def test_future_measurement_is_rejected(session):
    rs = parse_cushion(jra_text(CUSHION), fetched_at=fetched_at(CUSHION))
    future = dataclasses.replace(
        rs[0], measured_at=fetched_at(CUSHION) + datetime.timedelta(minutes=1)
    )
    c, _ = _write(session, CUSHION, parse_cushion, readings=[future])
    assert c.extra.get("baba_rejected_future") == 1 and _n(session) == 0


def test_fetched_basis_records_change_points_only(session):
    c1, rs = _write(session, ACCESS_J, parse_kaisai_info)
    assert c1.extra["baba_rows_written"] == len(rs)
    later = fetched_at(ACCESS_J) + datetime.timedelta(hours=1)
    same_later = [dataclasses.replace(r, measured_at=later) for r in rs]
    c2, _ = _write(session, ACCESS_J, parse_kaisai_info, readings=same_later, at=later)
    assert c2.written == 0 and _n(session) == len(rs)
    changed = [dataclasses.replace(same_later[0], value_text="稍重"), *same_later[1:]]
    c3, _ = _write(session, ACCESS_J, parse_kaisai_info, readings=changed, at=later)
    assert c3.written == 1 and _n(session) == len(rs) + 1


def _args(**over):
    base = dict(
        date=D.isoformat(),
        venue=None,
        race_ids=None,
        dry_run=False,
        json=True,
        database_url=None,
        policy=None,
        archive_dir=None,
        command="jra-baba",
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_cli_writes_one_job_with_three_requests(session, session_factory):
    f = FakeJraFetcher()
    r = run_command("jra-baba", _args(), fetcher=f, session_factory=session_factory, date=D)
    assert r.exit_code == 0, r.to_json()
    assert r.jobs[0].job_type == "jra_baba" and r.jobs[0].status == JobStatus.SUCCEEDED
    assert r.jobs[0].extra["requests_sent"] == 3 == f.requests_sent
    assert r.jobs[0].extra["baba_rows_written"] == _n(session) == 36 + 144 + 6


def test_cli_refuses_before_sending_when_the_table_is_missing(session_factory, monkeypatch):
    monkeypatch.setattr(baba_cli, "table_present", lambda s: False)
    f = FakeJraFetcher()
    r = run_command("jra-baba", _args(), fetcher=f, session_factory=session_factory, date=D)
    assert r.exit_code == 2 and f.requests_sent == 0 and r.jobs == []
    assert any("table_missing" in n for n in r.notes)
