"""178 T066/T068 (live): the capture fetcher is never built while ``netkeiba_enabled`` is false;
the official T−3 source is unaffected by the netkeiba switch."""

from __future__ import annotations

import json

import pytest
from horseracing_scrape.netkeiba_policy import POLICY_PATH_ENV, NetkeibaDisabled

from horseracing_live import chaos_politeness as cp
from horseracing_live import near_post as np_


def _policy(tmp_path, monkeypatch, **doc):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"collection_paused": False, **doc}), encoding="utf-8")
    monkeypatch.setenv(POLICY_PATH_ENV, str(p))
    return p


def test_capture_fetcher_is_refused_before_any_client(tmp_path, monkeypatch):
    _policy(tmp_path, monkeypatch, netkeiba_enabled=False)
    monkeypatch.setattr(
        cp, "CaptureHttpFetcher", lambda **k: pytest.fail("client built while disabled")
    )
    monkeypatch.setattr(cp, "create_db_engine", lambda *a, **k: pytest.fail("engine built"))
    with pytest.raises(NetkeibaDisabled):
        cp.make_capture_fetcher(database_url="postgresql://nobody@127.0.0.1:1/x")


def test_true_builds_the_capture_fetcher(tmp_path, monkeypatch):
    _policy(tmp_path, monkeypatch, netkeiba_enabled=True)
    fetcher = cp.make_capture_fetcher(policy=cp.RequestPoliteness.__new__(cp.RequestPoliteness))
    assert isinstance(fetcher, cp.CaptureHttpFetcher)


def test_official_source_policy_coexists_with_the_netkeiba_switch(tmp_path, monkeypatch):
    p = _policy(tmp_path, monkeypatch, netkeiba_enabled=False, near_post_source="jra_official")
    pol = np_.load_capture_policy(p)
    assert pol[np_.POLICY_SOURCE_KEY] == "jra_official"  # the capture does not need netkeiba
