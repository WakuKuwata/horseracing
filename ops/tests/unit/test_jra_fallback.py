"""2026-11-07: the JRA-first odds step — availability (env) and the fallback decision, no DB."""

from __future__ import annotations

import logging

from horseracing_db.enums import JobStatus
from horseracing_scrape.pipeline import JobSummary
from horseracing_scrape.politeness import JRA_SOURCE_INTERVAL_ENV

from horseracing_ops import runner


def _s(**extra):
    return JobSummary("jra_odds", "r", 0, 0, 0, 0, JobStatus.SKIPPED, extra)


def test_fallback_decision_follows_the_writer_reason():
    assert runner._jra_fallback_needed(None) is True
    assert runner._jra_fallback_needed(_s(odds_written_races=1)) is False
    assert runner._jra_fallback_needed(_s(odds_skipped_stale=1)) is False
    assert runner._jra_fallback_needed(_s()) is True
    assert runner._jra_fallback_needed(_s(odds_partial_field=1)) is True
    assert runner._jra_fallback_needed(_s(odds_prev_day_final_ignored=1)) is True


def test_unconfigured_interval_disables_the_route_and_logs_once(monkeypatch, caplog):
    monkeypatch.delenv(JRA_SOURCE_INTERVAL_ENV, raising=False)
    runner._jra_unavailable_logged.clear()
    with caplog.at_level(logging.WARNING, logger="horseracing_ops.runner"):
        assert runner.make_jra_fetcher() is None
        assert runner.make_jra_fetcher() is None
    assert sum("JRA official route unavailable" in r.message for r in caplog.records) == 1


def test_interval_below_floor_disables_the_route(monkeypatch):
    monkeypatch.setenv(JRA_SOURCE_INTERVAL_ENV, "3")
    runner._jra_unavailable_logged.clear()
    assert runner.make_jra_fetcher() is None
