"""176 T037: the JRA host has its own throttle key and a MANDATORY interval (SC-006)."""

from __future__ import annotations

import pytest

from horseracing_scrape.jra.errors import SourceIntervalUnconfigured
from horseracing_scrape.politeness import (
    JRA_SOURCE_INTERVAL_ENV,
    SOURCE_INTERVAL_ENV,
    jra_source_interval_s,
    source_interval_s,
    throttle_key,
)


def test_throttle_key_jra_hosts_share_one_bucket():
    assert throttle_key("https://www.jra.go.jp/JRADB/accessD.html") == "jra.go.jp"
    assert throttle_key("https://jra.go.jp/robots.txt") == "jra.go.jp"
    assert throttle_key("https://notjra.go.jp/x") != "jra.go.jp"
    # netkeiba unchanged
    assert throttle_key("https://race.netkeiba.com/x") == "netkeiba.com"
    assert throttle_key("https://db.netkeiba.com/x") == "netkeiba.com"


@pytest.mark.parametrize("raw", [None, "", "  ", "abc", "nan", "inf", "-inf", "9.9", "0", "-5"])
def test_jra_interval_rejects_unset_and_invalid(raw):
    with pytest.raises(SourceIntervalUnconfigured):
        jra_source_interval_s(raw)


def test_jra_interval_accepts_floor_and_above():
    assert jra_source_interval_s("10") == 10.0
    assert jra_source_interval_s("60") == 60.0
    assert jra_source_interval_s("10.5") == 10.5


def test_source_interval_jra_reads_env_strictly(monkeypatch):
    monkeypatch.delenv(JRA_SOURCE_INTERVAL_ENV, raising=False)
    with pytest.raises(SourceIntervalUnconfigured):
        source_interval_s("jra.go.jp")
    monkeypatch.setenv(JRA_SOURCE_INTERVAL_ENV, "10")
    assert source_interval_s("jra.go.jp") == 10.0


def test_netkeiba_and_other_hosts_unchanged(monkeypatch):
    monkeypatch.delenv(SOURCE_INTERVAL_ENV, raising=False)
    monkeypatch.delenv(JRA_SOURCE_INTERVAL_ENV, raising=False)
    assert source_interval_s("netkeiba.com") == 1.0
    assert source_interval_s("https://example.com") == 0.0
    monkeypatch.setenv(SOURCE_INTERVAL_ENV, "60")
    assert source_interval_s("netkeiba.com") == 60.0
