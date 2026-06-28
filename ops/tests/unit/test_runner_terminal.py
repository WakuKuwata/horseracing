"""T037: terminal-status decision edge cases (pure, no DB)."""

from __future__ import annotations

from horseracing_db.enums import JobStatus

from horseracing_ops.runner import _terminal


def test_entries_failure_is_failed():
    # couldn't build the entry population -> a real failure
    assert _terminal(entries_failed=True, any_failed=True, errors=0, written=0) == JobStatus.FAILED


def test_non_entries_failure_is_partial():
    # entries ok but a sub-step failed (e.g. a future race has no result page yet) -> PARTIAL
    assert _terminal(entries_failed=False, any_failed=True, errors=0, written=10) \
        == JobStatus.PARTIAL


def test_errors_make_partial():
    assert _terminal(entries_failed=False, any_failed=False, errors=2, written=3) \
        == JobStatus.PARTIAL


def test_nothing_written_is_skipped():
    # page not published yet / no rows -> distinct from a real success
    assert _terminal(entries_failed=False, any_failed=False, errors=0, written=0) \
        == JobStatus.SKIPPED


def test_clean_write_succeeds():
    assert _terminal(entries_failed=False, any_failed=False, errors=0, written=18) \
        == JobStatus.SUCCEEDED
