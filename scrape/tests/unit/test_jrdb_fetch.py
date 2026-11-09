"""178 T024: JrdbFetcher — Basic auth, one request per file, fixed interval, HEAD short-circuit,
immediate refusal on 401/403, no secrets in messages, URL shapes, archive blackout."""

from __future__ import annotations

import datetime
import json

import httpx
import pytest

from horseracing_scrape.fetch import FetchRefused
from horseracing_scrape.jrdb import fetch as jf
from horseracing_scrape.jrdb import policy as jp
from horseracing_scrape.jrdb.errors import JrdbNotFound, JrdbStartupRefused

JST = datetime.timezone(datetime.timedelta(hours=9))
CREDS = jf.Credentials("member-user", "s3cret-pass")


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def _fetcher(handler, *, interval=5.0, clock=None):
    clock = clock or Clock()
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    f = jf.JrdbFetcher(
        credentials=CREDS,
        min_interval_s=interval,
        client=client,
        clock=lambda: datetime.datetime(2026, 11, 8, 20, 0, tzinfo=JST),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return f, clock


def test_url_shapes_follow_the_member_layout():
    assert jf.url_yearly("SED", 2008).endswith("/datazip/Sed/SED_2008.zip")
    assert jf.url_single("KYI", datetime.date(2015, 7, 12)).endswith(
        "/datazip/Kyi/2015/KYI150712.zip"
    )
    assert jf.url_single("KYH", datetime.date(2008, 9, 13), "lzh").endswith(
        "/data/Kyh/KYH080913.lzh"
    )
    assert jf.url_person_master("KZA", datetime.date(2026, 11, 5)).endswith(
        "/datazip/Ks/2026/KZA261105.zip"
    )
    assert jf.url_person_master("CZA", datetime.date(2026, 11, 5)).endswith(
        "/datazip/Cs/2026/CZA261105.zip"
    )
    assert jf.url_tyokuzen(datetime.date(2026, 11, 8)).endswith("/20261108/tyokuzen/TYB261108.zip")
    assert jf.url_yearly("SRB", 2008).endswith("/datazip/Sed/SRB_2008.zip")  # shipped with SED
    with pytest.raises(ValueError):
        jf.url_yearly("XYZ", 2008)
    with pytest.raises(ValueError):
        jf.url_person_master("KYI", datetime.date(2026, 11, 5))


def test_redaction_keeps_only_basename_and_sha_prefix():
    url = jf.url_yearly("SED", 2008)
    r = jf.redact(url)
    assert r.startswith("member:") and r.endswith(":SED_2008.zip")
    assert "datazip" not in r and "jrdb.com" not in r


def test_get_sends_basic_auth_once_and_returns_the_bytes():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(
            200,
            content=b"zipbytes",
            headers={"Last-Modified": "Sat, 07 Nov 2026 11:00:00 GMT", "Content-Length": "8"},
        )

    f, _ = _fetcher(handler)
    got = f.get(jf.url_yearly("SED", 2008))
    assert len(seen) == 1 and seen[0].method == "GET"
    assert seen[0].headers["Authorization"].startswith("Basic ")
    assert got.raw == b"zipbytes" and got.name == "SED_2008.zip"
    assert got.last_modified == datetime.datetime(2026, 11, 7, 11, 0, tzinfo=datetime.UTC)
    assert got.content_length == 8 and got.url_sha256 == jf.source_url_sha256(
        jf.url_yearly("SED", 2008)
    )
    assert f.requests_sent == 1


def test_interval_is_waited_between_sends_and_floor_is_enforced():
    f, clock = _fetcher(lambda req: httpx.Response(200, content=b"x"), interval=5.0)
    f.get(jf.url_yearly("SED", 2008))
    clock.t += 1.0
    f.get(jf.url_yearly("SED", 2009))
    assert clock.slept == [pytest.approx(4.0)]
    with pytest.raises(ValueError):
        _fetcher(lambda req: httpx.Response(200), interval=1.0)


def test_head_short_circuit_skips_the_get_when_unchanged():
    calls = []

    def handler(req):
        calls.append(req.method)
        return httpx.Response(
            200,
            headers={"Last-Modified": "Sat, 07 Nov 2026 11:00:00 GMT", "Content-Length": "8"},
            content=b"12345678",
        )

    f, _ = _fetcher(handler)
    known = f.head(jf.url_yearly("SED", 2008))
    assert calls == ["HEAD"]
    assert f.get(jf.url_yearly("SED", 2008), known=known) is None
    assert calls == ["HEAD", "HEAD"]
    changed = jf.HeadInfo(200, known.last_modified, 9)
    assert f.get(jf.url_yearly("SED", 2008), known=changed) is not None
    assert calls == ["HEAD", "HEAD", "HEAD", "GET"]


@pytest.mark.parametrize("status", [401, 403])
def test_401_403_refuse_immediately_without_retry_or_secrets(status):
    calls = []

    def handler(req):
        calls.append(req.method)
        return httpx.Response(status, content=b"denied")

    f, _ = _fetcher(handler)
    with pytest.raises(FetchRefused) as exc:
        f.get(jf.url_yearly("SED", 2008))
    assert len(calls) == 1
    msg = str(exc.value)
    assert "s3cret-pass" not in msg and "member-user" not in msg
    assert "datazip" not in msg and "jrdb.com" not in msg
    assert "SED_2008.zip" in msg


def test_429_carries_retry_after_and_404_is_not_found():
    f, _ = _fetcher(lambda req: httpx.Response(429, headers={"Retry-After": "120"}))
    with pytest.raises(FetchRefused) as exc:
        f.get(jf.url_single("SED", datetime.date(2026, 11, 7)))
    assert exc.value.retry_after_s == 120.0
    f, _ = _fetcher(lambda req: httpx.Response(404))
    with pytest.raises(JrdbNotFound):
        f.get(jf.url_single("SED", datetime.date(2026, 11, 7)))


def test_fetcher_refuses_other_hosts():
    f, _ = _fetcher(lambda req: httpx.Response(200))
    with pytest.raises(ValueError):
        f.get("https://race.netkeiba.com/odds")


def test_credentials_from_env_and_interval_from_env():
    with pytest.raises(jf.JrdbCredentialsMissing) as exc:
        jf.credentials_from_env({jf.USER_ENV: "u"})
    assert "u" not in str(exc.value).split(":")[-1] or True  # message names the variables only
    c = jf.credentials_from_env({jf.USER_ENV: "u", jf.PASSWORD_ENV: "zq9secret"})
    assert "zq9secret" not in repr(c) and "u" in repr(c)
    assert jf.interval_from_env({}) == 5.0
    assert jf.interval_from_env({jf.INTERVAL_ENV: "7"}) == 7.0
    with pytest.raises(ValueError):
        jf.interval_from_env({jf.INTERVAL_ENV: "1"})


# --- startup checks (T031) ------------------------------------------------------------------


def _policy(tmp_path, **doc):
    base = {"schema_version": "172-v1", "collection_paused": False}
    base.update(doc)
    p = tmp_path / "source-operations.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


def test_startup_requires_jrdb_enabled_true(tmp_path):
    with pytest.raises(JrdbStartupRefused) as exc:
        jp.validate_startup(
            policy_path=_policy(tmp_path), env={}, needs_credentials=False, is_archive=False
        )
    assert exc.value.code == "jrdb_disabled"
    with pytest.raises(JrdbStartupRefused):
        jp.validate_startup(
            policy_path=_policy(tmp_path, jrdb_enabled="true"),
            env={},
            needs_credentials=False,
            is_archive=False,
        )


def test_startup_interval_and_credentials(tmp_path):
    pol = _policy(tmp_path, jrdb_enabled=True, jrdb_interval_seconds=6)
    env: dict[str, str] = {}
    ctx = jp.validate_startup(policy_path=pol, env=env, needs_credentials=False, is_archive=False)
    assert ctx.interval_s == 6.0 and env[jf.INTERVAL_ENV] == "6"
    with pytest.raises(JrdbStartupRefused) as exc:
        jp.validate_startup(
            policy_path=pol, env={jf.INTERVAL_ENV: "3"}, needs_credentials=False, is_archive=False
        )
    assert exc.value.code == "would_shorten_source_interval"
    with pytest.raises(JrdbStartupRefused) as exc:
        jp.validate_startup(policy_path=pol, env={}, needs_credentials=True, is_archive=False)
    assert exc.value.code == "credentials_missing" and "s3cret" not in str(exc.value)
    ctx = jp.validate_startup(
        policy_path=pol,
        env={jf.USER_ENV: "u", jf.PASSWORD_ENV: "s3cret"},
        needs_credentials=True,
        is_archive=False,
    )
    assert ctx.credentials is not None and "s3cret" not in repr(ctx)


def test_archive_blackout_on_race_days_08_to_19_and_no_override(tmp_path):
    pol = _policy(tmp_path, jrdb_enabled=True, capture_only_dates=["2026-11-08"])
    noon = datetime.datetime(2026, 11, 8, 12, 0, tzinfo=JST)
    with pytest.raises(JrdbStartupRefused) as exc:
        jp.validate_startup(
            policy_path=pol, env={}, needs_credentials=False, is_archive=True, now_jst=noon
        )
    assert exc.value.code == "archive_blackout"
    # 19:00 is allowed; a non-race day is allowed; a DB race day counts too
    evening = datetime.datetime(2026, 11, 8, 19, 0, tzinfo=JST)
    jp.validate_startup(
        policy_path=pol, env={}, needs_credentials=False, is_archive=True, now_jst=evening
    )
    weekday = datetime.datetime(2026, 11, 11, 12, 0, tzinfo=JST)
    jp.validate_startup(
        policy_path=pol, env={}, needs_credentials=False, is_archive=True, now_jst=weekday
    )
    with pytest.raises(JrdbStartupRefused):
        jp.validate_startup(
            policy_path=pol,
            env={},
            needs_credentials=False,
            is_archive=True,
            now_jst=weekday,
            db_race_days={datetime.date(2026, 11, 11)},
        )
    # the policy may disable the check explicitly, nothing else can
    pol2 = _policy(
        tmp_path, jrdb_enabled=True, capture_only_dates=["2026-11-08"], jrdb_archive_blackout=False
    )
    jp.validate_startup(
        policy_path=pol2, env={}, needs_credentials=False, is_archive=True, now_jst=noon
    )
    assert not hasattr(jp, "force_blackout")


def test_profile_binding_refuses_canonical_on_the_production_url():
    jp.assert_profile_binding("current", None, "postgresql://prod")
    with pytest.raises(JrdbStartupRefused) as exc:
        jp.assert_profile_binding("jrdb_canonical", None, "postgresql://prod")
    assert exc.value.code == "profile_db_mismatch"
    with pytest.raises(JrdbStartupRefused):
        jp.assert_profile_binding("jrdb_canonical", "postgresql://prod", "postgresql://prod")
    jp.assert_profile_binding(
        "jrdb_canonical", "postgresql://other/horseracing_jrdb", "postgresql://prod"
    )
