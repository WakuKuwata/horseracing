"""093: the parts of request politeness that decide whether "1 request per minute" is real.

These are pure-logic tests around `throttle_key` and the contract resolution. The reservation
itself needs PostgreSQL (`clock_timestamp()` + `FOR UPDATE`), so its behaviour is covered by the
integration tests; what is checked here is the reasoning that no database can rescue:

  * which requests share a budget,
  * whether a caller can talk the source into a weaker interval,
  * and which statuses count as "the source is refusing".
"""

from __future__ import annotations

import pytest

from horseracing_scrape.politeness import (
    _COOLDOWN_S,
    SOURCE_INTERVAL_ENV,
    RequestPoliteness,
    source_interval_s,
    throttle_key,
)

# --- one operator, one budget ----------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://race.netkeiba.com/race/result.html?race_id=1",
        "https://db.netkeiba.com/horse/2022103995/",
        "https://www.netkeiba.com/",
        "https://netkeiba.com/",
        "http://race.netkeiba.com/api/api_get_jra_odds.html?type=1",
    ],
)
def test_every_netkeiba_host_shares_one_bucket(url):
    """race.* and db.* are the same operator. Keying by hostname gave each its own bucket and
    silently doubled the rate the site actually saw."""
    assert throttle_key(url) == "netkeiba.com"


@pytest.mark.parametrize(
    "url",
    [
        "https://evilnetkeiba.com/x",       # suffix must not match by string containment
        "https://netkeiba.com.example.org/x",
        "https://example.org/netkeiba.com",
    ],
)
def test_unrelated_hosts_keep_their_own_bucket(url):
    assert throttle_key(url) != "netkeiba.com"


def test_throttle_key_is_not_the_robots_origin():
    """`_domain` builds `{domain}/robots.txt`. If the throttle key had replaced it, robots would
    be fetched from the wrong host — so these two must stay separate functions."""
    from horseracing_scrape.fetch import _domain

    url = "https://db.netkeiba.com/horse/1/"
    assert _domain(url) == "https://db.netkeiba.com"
    assert throttle_key(url) == "netkeiba.com"


# --- one contract, and callers cannot weaken it -----------------------------

def test_the_interval_is_configured_in_one_place(monkeypatch):
    monkeypatch.setenv(SOURCE_INTERVAL_ENV, "60")
    assert source_interval_s("netkeiba.com") == 60.0
    monkeypatch.setenv(SOURCE_INTERVAL_ENV, "not-a-number")
    assert source_interval_s("netkeiba.com") == 1.0  # malformed config must not disable politeness


def test_a_caller_cannot_shorten_the_source_contract(monkeypatch):
    """Capture constructed its policy with the 1.0 default while ops asked for 60. Both write the
    SAME throttle row, so capture's reservation used to advance it by one second — quietly
    shortening the contract for everybody."""
    monkeypatch.setenv(SOURCE_INTERVAL_ENV, "60")
    weak = RequestPoliteness(lambda: None, min_interval_s=1.0)
    assert weak._interval_for("netkeiba.com") == 60.0


def test_a_caller_may_tighten_it(monkeypatch):
    monkeypatch.setenv(SOURCE_INTERVAL_ENV, "60")
    strict = RequestPoliteness(lambda: None, min_interval_s=90.0)
    assert strict._interval_for("netkeiba.com") == 90.0


def test_non_netkeiba_hosts_are_left_to_the_caller(monkeypatch):
    monkeypatch.setenv(SOURCE_INTERVAL_ENV, "60")
    p = RequestPoliteness(lambda: None, min_interval_s=2.0)
    assert p._interval_for("https://example.org") == 2.0


# --- modes ------------------------------------------------------------------

def test_background_callers_wait_and_bounded_callers_refuse():
    """`max_wait_s=3` refuses as soon as the queue is longer than three seconds. At a sixty-second
    interval that is EVERY request after the first, so the daily pass would quietly bleed ingest.
    The background contract is `None` = wait for the turn."""
    from horseracing_scrape.politeness import ReservationDecision

    queued = ReservationDecision(domain="netkeiba.com", allowed=False,
                                 reason="throttle_backlog", wait_s=59.0)

    background = RequestPoliteness(lambda: None, max_wait_s=None)
    assert background._wait_for(queued, "netkeiba.com") == 59.0

    capture = RequestPoliteness(lambda: None, max_wait_s=3.0)
    assert capture._wait_for(queued, "netkeiba.com") is None  # refuse, do not stall the deadline


def test_only_background_callers_wait_out_a_cooldown():
    """A deadline-bound capture cannot outlast a 30-minute block; a background job should."""
    from horseracing_scrape.politeness import ReservationDecision

    blocked = ReservationDecision(domain="netkeiba.com", allowed=False, reason="source_cooldown")
    assert RequestPoliteness(lambda: None, max_wait_s=None)._wait_for(blocked, "x") is not None
    assert RequestPoliteness(lambda: None, max_wait_s=3.0)._wait_for(blocked, "x") is None


def test_a_bounded_caller_still_waits_a_short_queue():
    from horseracing_scrape.politeness import ReservationDecision

    soon = ReservationDecision(domain="netkeiba.com", allowed=False,
                               reason="throttle_backlog", wait_s=1.5)
    assert RequestPoliteness(lambda: None, max_wait_s=3.0)._wait_for(soon, "x") == 1.5


# --- refusal statuses -------------------------------------------------------

def test_the_status_netkeiba_actually_blocks_with_installs_a_cooldown():
    """netkeiba answers sustained load with a bare 400, not 403/429 — the documented pair. The
    cooldown map covered only the documented ones, so the mechanism that exists to back off never
    fired for the real block."""
    from horseracing_scrape.fetch import REFUSAL_STATUSES

    assert 400 in _COOLDOWN_S, "the real block status must install a cooldown"
    assert set(_COOLDOWN_S) >= REFUSAL_STATUSES, (
        "every status treated as a refusal should also back the source off"
    )
