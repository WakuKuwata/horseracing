"""T037: terminal-status decision edge cases (pure, no DB)."""

from __future__ import annotations

from horseracing_db.enums import JobStatus

from horseracing_ops.runner import _terminal


def test_failed_dominates():
    assert _terminal([JobStatus.SUCCEEDED, JobStatus.FAILED], written=5, errors=0) == JobStatus.FAILED


def test_errors_make_partial():
    assert _terminal([JobStatus.PARTIAL], written=3, errors=2) == JobStatus.PARTIAL


def test_nothing_written_is_skipped():
    # page not published yet / no rows -> distinct from a real success
    assert _terminal([JobStatus.SUCCEEDED], written=0, errors=0) == JobStatus.SKIPPED


def test_clean_write_succeeds():
    assert _terminal([JobStatus.SUCCEEDED], written=18, errors=0) == JobStatus.SUCCEEDED
