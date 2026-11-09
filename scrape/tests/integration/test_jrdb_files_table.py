"""178 T039: ``jrdb_files`` rows — same sha twice is one row with its original fetched_at, a
速報→正式 pair supersedes the old row, a held row keeps applied_at NULL, no UNIQUE violation."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models.jrdb import JrdbFile
from sqlalchemy import select

from horseracing_scrape.jrdb import files
from horseracing_scrape.jrdb.models import JrdbFileMeta

pytestmark = pytest.mark.integration

JST = datetime.timezone(datetime.timedelta(hours=9))
D = datetime.date(2026, 11, 7)
T_SOKUHO = datetime.datetime(2026, 11, 7, 18, 5, tzinfo=JST)
T_FORMAL = datetime.datetime(2026, 11, 12, 16, 30, tzinfo=JST)


def _meta(sha, edition, variant="daily"):
    return JrdbFileMeta("SED", "4", D, variant, sha, 376 * 10, 376, edition=edition)


def test_same_sha_is_one_row_and_fetched_at_is_untouched(session):
    m = _meta("a", "sokuho")
    row1, created1 = files.record_fetched(
        session, m, source_url_sha256="u", fetched_at=T_SOKUHO, published_at=None
    )
    session.commit()
    row2, created2 = files.record_fetched(
        session, m, source_url_sha256="u", fetched_at=T_FORMAL, published_at=T_FORMAL
    )
    session.commit()
    assert created1 is True and created2 is False and row1.jrdb_file_id == row2.jrdb_file_id
    session.expire_all()
    rows = session.scalars(select(JrdbFile)).all()
    assert len(rows) == 1
    assert rows[0].fetched_at == T_SOKUHO and rows[0].published_at is None
    assert rows[0].status == "fetched" and rows[0].race_status == {}


def test_sokuho_then_formal_supersedes_the_old_row(session):
    old = _meta("a", "sokuho")
    new = _meta("b", "formal")
    row_old, _ = files.record_fetched(
        session, old, source_url_sha256="u", fetched_at=T_SOKUHO, published_at=None
    )
    session.commit()
    assert files.classify_version(old, new) == "supersede"
    row_new, created = files.record_fetched(
        session, new, source_url_sha256="u", fetched_at=T_FORMAL, published_at=None
    )
    files.mark_superseded(session, row_old)
    session.commit()
    session.expire_all()
    by_sha = {r.sha256: r for r in session.scalars(select(JrdbFile))}
    assert created and by_sha["a"].status == "superseded" and by_sha["b"].status == "fetched"
    assert len(files.find_versions(session, "SED", D, "daily")) == 2


def test_held_row_keeps_applied_at_null_and_an_applied_row_cannot_be_held(session):
    first = _meta("a", "formal")
    second = _meta("c", "formal")  # differing formal content → held
    row_a, _ = files.record_fetched(
        session, first, source_url_sha256="u", fetched_at=T_FORMAL, published_at=None
    )
    row_c, _ = files.record_fetched(
        session, second, source_url_sha256="u", fetched_at=T_FORMAL, published_at=None
    )
    assert files.classify_version(first, second) == "held"
    files.mark_held(session, row_c)
    session.commit()
    session.expire_all()
    c = session.scalar(select(JrdbFile).where(JrdbFile.sha256 == "c"))
    assert c.status == "held" and c.applied_at is None
    row_a.status = "applied"
    session.commit()
    with pytest.raises(ValueError):
        files.mark_held(session, row_a)
    session.rollback()


def test_daily_and_archive_versions_coexist_without_unique_violation(session):
    daily = _meta("a", None)
    archive = _meta("a", "formal", variant="archive")  # same content from the back-number
    files.record_fetched(
        session, daily, source_url_sha256="u1", fetched_at=T_SOKUHO, published_at=None
    )
    files.record_fetched(
        session, archive, source_url_sha256="u2", fetched_at=T_FORMAL, published_at=None
    )
    session.commit()
    assert len(session.scalars(select(JrdbFile)).all()) == 2
