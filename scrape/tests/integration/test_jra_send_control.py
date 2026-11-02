"""176 T041 (SC-012): every JRA send goes through the shared slot, across processes.

The cross-process case is the one that matters: a second OS process (``subprocess``) contends for
the same ``jra.go.jp`` row and the sends of both processes stay ≥ the interval apart. A thread in
the same process would not prove anything (an in-process lock could pass it).
The response-level cases (3xx without follow, guard before/after the slot) are unit tests in
``tests/unit/test_jra_fetch.py``; the startup refusals in ``tests/unit/test_jra_policy_startup.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time

import httpx
import pytest

from horseracing_scrape.fetch import FetchRefused
from horseracing_scrape.jra.fetch import JraFetcher
from horseracing_scrape.jra.models import KIND_ACCESS_J, KIND_BABA_FRAGMENT, KIND_INDEX_D
from horseracing_scrape.jra.navigate import (
    ACCESS_J_CNAME,
    ACCESS_J_URL,
    BABA_CUSHION_URL,
    INDEX,
)
from horseracing_scrape.jra.policy import validate_startup
from horseracing_scrape.politeness import JRA_SOURCE_INTERVAL_ENV, SHARED_LIMITER_ENV

pytestmark = pytest.mark.integration
INTERVAL = 10.0
URL = "https://www.jra.go.jp/JRADB/accessD.html"


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    monkeypatch.setenv(JRA_SOURCE_INTERVAL_ENV, str(int(INTERVAL)))
    monkeypatch.delenv(SHARED_LIMITER_ENV, raising=False)
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"jra_official_interval_seconds": INTERVAL}))
    return p


class _Counting:
    """Wraps the real shared policy and counts the slots taken."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[str] = []

    def pre_request(self, url):
        self.calls.append(url)
        self.inner.pre_request(url)

    def record_refusal(self, refusal, *, request_url=None):
        self.inner.record_refusal(refusal, request_url=request_url)


def _fetcher(policy, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return JraFetcher(policy=policy, client=client, respect_robots=False, sleep=lambda s: None)


def test_every_kind_of_send_takes_a_slot(policy_file, database_url, engine, monkeypatch):
    import horseracing_scrape.politeness as pol

    ctx = validate_startup(policy_path=policy_file, database_url=database_url)
    # the slot is always due here: what is counted is that every send ASKS for one
    monkeypatch.setattr(
        pol.RequestPoliteness,
        "reserve",
        lambda self, url: pol.ReservationDecision(domain="jra.go.jp", allowed=True),
    )
    counting = _Counting(ctx.policy)
    sent = []
    attempts = {"n": 0}

    def handler(request):
        sent.append(request.url.path)
        if request.url.path.endswith("_data_cushion.html"):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(502)  # GET retries once on 5xx
        body = "<html><head><meta charset='Shift_JIS'></head><body>ok</body></html>"
        return httpx.Response(
            200, content=body.encode("cp932"), headers={"Content-Type": "text/html"}
        )

    f = _fetcher(counting, handler)
    ref = INDEX[KIND_INDEX_D]
    f.post(ref.url, ref.cname, kind=KIND_INDEX_D)
    f.get(BABA_CUSHION_URL, kind=KIND_BABA_FRAGMENT)
    f.post(ACCESS_J_URL, ACCESS_J_CNAME, kind=KIND_ACCESS_J)
    assert len(sent) == len(counting.calls) == f.requests_sent == 4  # incl. the 5xx retry


_CHILD = textwrap.dedent(
    """
    import json, sys, time
    from pathlib import Path
    from horseracing_scrape.jra.policy import validate_startup
    ctx = validate_startup(policy_path=Path(sys.argv[1]), database_url=sys.argv[2])
    out = []
    for _ in range(2):
        ctx.policy.pre_request("https://www.jra.go.jp/JRADB/accessS.html")
        out.append(time.time())
    print(json.dumps(out))
    """
)


def test_two_processes_share_the_slot(policy_file, database_url, engine):
    env = dict(os.environ)
    env[JRA_SOURCE_INTERVAL_ENV] = str(int(INTERVAL))
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(policy_file), database_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    ctx = validate_startup(policy_path=policy_file, database_url=database_url)
    mine = []
    for _ in range(2):
        ctx.policy.pre_request(URL)
        mine.append(time.time())
    out, err = child.communicate(timeout=120)
    assert child.returncode == 0, err
    theirs = json.loads(out.strip().splitlines()[-1])
    stamps = sorted(mine + theirs)
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    # 4 sends from 2 processes, one shared row: every gap ≥ the interval (small clock slack)
    assert len(stamps) == 4
    assert min(gaps) >= INTERVAL - 0.5, gaps


def test_cooldown_is_refused_without_waiting(policy_file, database_url, engine):
    ctx = validate_startup(policy_path=policy_file, database_url=database_url)
    slept = []
    ctx.policy._sleep = slept.append
    ctx.policy.record_refusal(FetchRefused(429, URL, retry_after_s=600), request_url=URL)
    with pytest.raises(FetchRefused):
        ctx.policy.pre_request(URL)
    assert slept == []  # JRA stops the run instead of sitting through a cooldown (FR-005)


def test_disabled_shared_limiter_sends_nothing(policy_file, database_url, engine, monkeypatch):
    from horseracing_scrape.jra.policy import StartupRefused

    monkeypatch.setenv(SHARED_LIMITER_ENV, "0")
    with pytest.raises(StartupRefused) as exc:
        validate_startup(policy_path=policy_file, database_url=database_url)
    assert exc.value.code == "NoSharedLimiter"
    monkeypatch.delenv(SHARED_LIMITER_ENV)
    with pytest.raises(StartupRefused) as exc:
        validate_startup(
            policy_path=policy_file, database_url="postgresql+psycopg://x:y@127.0.0.1:1/none"
        )
    assert exc.value.code == "NoSharedLimiter"
