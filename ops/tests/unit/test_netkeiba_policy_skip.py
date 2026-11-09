"""178 T066/T068 (ops): with ``netkeiba_enabled: false`` the worker builds no HttpFetcher and the
refresh / day jobs end SKIPPED with the reason and zero requests; ``true`` keeps the factory."""

from __future__ import annotations

import json

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob
from horseracing_scrape.netkeiba_policy import POLICY_PATH_ENV, NetkeibaDisabled

from horseracing_ops import runner


class _Session:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


def _disable(tmp_path, monkeypatch, enabled=False):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"netkeiba_enabled": enabled}), encoding="utf-8")
    monkeypatch.setenv(POLICY_PATH_ENV, str(p))


def test_make_fetcher_raises_before_any_client(tmp_path, monkeypatch):
    _disable(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner, "HttpFetcher", lambda **k: pytest.fail("HttpFetcher built while disabled")
    )
    monkeypatch.setattr(
        runner, "background_policy", lambda **k: pytest.fail("politeness built while disabled")
    )
    with pytest.raises(NetkeibaDisabled):
        runner.make_fetcher()


@pytest.mark.parametrize(
    "fn,scope", [(runner.run_one, "202605040111"), (runner.run_day, "2026-11-08")]
)
def test_jobs_end_skipped_with_the_reason(tmp_path, monkeypatch, fn, scope):
    _disable(tmp_path, monkeypatch)
    session = _Session()
    job = IngestionJob(
        source="netkeiba",
        job_type="refresh_race",
        scope="race",
        scope_value=scope,
        status=JobStatus.RUNNING,
        summary={"refresh_origin": "manual_ui"},
    )
    out = fn(session, job)
    assert out.status == JobStatus.SKIPPED and out.completed_at is not None
    assert out.summary["reason"] == "netkeiba_disabled" and out.summary["requests_sent"] == 0
    assert out.summary["policy_disabled"] == "netkeiba"
    assert out.summary["policy"]["path"].endswith("policy.json") and out.summary["policy"]["sha256"]
    assert out.summary["refresh_origin"] == "manual_ui"  # the original summary is kept
    assert session.commits == 1 and session.added == [job]


def test_true_builds_the_fetcher_as_before(tmp_path, monkeypatch):
    _disable(tmp_path, monkeypatch, enabled=True)
    built = {}

    class Fetcher:
        def __init__(self, **kw):
            built.update(kw)

    monkeypatch.setattr(runner, "HttpFetcher", Fetcher)
    monkeypatch.setattr(runner, "background_policy", lambda **k: None)
    monkeypatch.setattr(runner, "owner_database_url", lambda: None)
    assert isinstance(runner.make_fetcher(min_interval=3.0), Fetcher)
    assert built["min_interval_s"] == 3.0
