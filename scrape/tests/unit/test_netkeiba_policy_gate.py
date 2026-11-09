"""178 T066: the policy switch that stops netkeiba (FR-014) — the flag reader, the scrape CLI's
fetcher factory refusing BEFORE any HTTP client exists, and the command ending SKIPPED with zero
requests; ``true`` (or no key) keeps the old path."""

from __future__ import annotations

import json

import pytest

from horseracing_scrape import cli as scli
from horseracing_scrape import netkeiba_policy as npol
from horseracing_scrape.jrdb import policy as jpol
from horseracing_scrape.jrdb.errors import JrdbStartupRefused


def _policy(tmp_path, monkeypatch, **doc):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv(npol.POLICY_PATH_ENV, str(p))
    return p


def test_read_flag_defaults_and_refuses_non_booleans():
    assert npol.read_flag({}, "netkeiba_enabled", True) is True
    assert npol.read_flag({"netkeiba_enabled": False}, "netkeiba_enabled", True) is False
    for bad in ("false", 0, 1, None, [False]):
        with pytest.raises(npol.PolicyFlagInvalid):
            npol.read_flag({"netkeiba_enabled": bad}, "netkeiba_enabled", True)


def test_missing_policy_file_means_enabled_but_a_broken_one_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv(npol.POLICY_PATH_ENV, str(tmp_path / "absent.json"))
    assert npol.read_netkeiba_enabled() is True
    npol.assert_netkeiba_enabled()  # no exception
    p = tmp_path / "broken.json"
    p.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setenv(npol.POLICY_PATH_ENV, str(p))
    with pytest.raises(npol.PolicyFlagInvalid):
        npol.assert_netkeiba_enabled()
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        npol.assert_netkeiba_enabled()


def test_false_raises_netkeiba_disabled_with_the_path(tmp_path, monkeypatch):
    p = _policy(tmp_path, monkeypatch, netkeiba_enabled=False)
    with pytest.raises(npol.NetkeibaDisabled) as exc:
        npol.assert_netkeiba_enabled()
    assert exc.value.code == "netkeiba_disabled" and exc.value.policy_path == str(p)
    # an explicit path wins over the env
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"netkeiba_enabled": True}), encoding="utf-8")
    npol.assert_netkeiba_enabled(other)


def test_the_committed_policy_keeps_netkeiba_enabled():
    """The six 178 keys are in the committed file at their defaults (T069)."""

    doc = json.loads(npol.DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    assert doc["netkeiba_enabled"] is True and doc["jrdb_enabled"] is False
    assert doc["jra_emergency_entries"] is False and doc["jrdb_archive_blackout"] is True
    assert doc["jrdb_interval_seconds"] == 5 and doc["near_post_source"] == "netkeiba"


def test_make_fetcher_refuses_before_building_a_client(tmp_path, monkeypatch):
    _policy(tmp_path, monkeypatch, netkeiba_enabled=False)

    def boom(*a, **k):
        raise AssertionError("HttpFetcher must not be constructed when netkeiba is disabled")

    monkeypatch.setattr(scli, "HttpFetcher", boom)
    monkeypatch.setattr(scli, "background_policy", boom)
    with pytest.raises(npol.NetkeibaDisabled):
        scli._make_fetcher(1.0, None, None, None)


def test_cli_command_ends_skipped_with_zero_requests(tmp_path, monkeypatch, capsys):
    _policy(tmp_path, monkeypatch, netkeiba_enabled=False)
    monkeypatch.setattr(
        scli, "discover_races", lambda *a, **k: pytest.fail("a request was about to be made")
    )
    rc = scli.main(["list-races", "--date", "2026-11-08"])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("SKIPPED: netkeiba_disabled")


def test_true_keeps_the_old_path(tmp_path, monkeypatch):
    _policy(tmp_path, monkeypatch, netkeiba_enabled=True)
    built = {}

    class Fetcher:
        def __init__(self, **kw):
            built.update(kw)

    monkeypatch.setattr(scli, "HttpFetcher", Fetcher)
    monkeypatch.setattr(scli, "background_policy", lambda **k: None)
    f = scli._make_fetcher(2.5, None, None, None)
    assert isinstance(f, Fetcher) and built["min_interval_s"] == 2.5


def test_jrdb_enabled_is_read_through_the_same_reader():
    jpol.require_enabled({"jrdb_enabled": True})
    for doc in ({}, {"jrdb_enabled": False}, {"jrdb_enabled": "true"}, {"jrdb_enabled": 1}):
        with pytest.raises(JrdbStartupRefused) as exc:
            jpol.require_enabled(doc)
        assert exc.value.code == "jrdb_disabled"


def test_a_fetcher_built_while_enabled_stops_at_the_next_request(tmp_path, monkeypatch):
    """codex D25 Q1-1: the policy is re-read before EVERY request, not only at construction."""

    p = _policy(tmp_path, monkeypatch, netkeiba_enabled=True)
    built = {}

    class Fetcher:
        def __init__(self, **kw):
            built.update(kw)

    monkeypatch.setattr(scli, "HttpFetcher", Fetcher)
    monkeypatch.setattr(scli, "background_policy", lambda **k: None)
    scli._make_fetcher(1.0, None, None, None)
    pre = built["pre_request"]
    pre("https://race.netkeiba.com/x")  # enabled: passes
    p.write_text(json.dumps({"netkeiba_enabled": False}), encoding="utf-8")  # the operator flips it
    with pytest.raises(npol.NetkeibaDisabled):
        pre("https://race.netkeiba.com/x")


def test_gate_pre_request_keeps_the_inner_hook(tmp_path, monkeypatch):
    _policy(tmp_path, monkeypatch, netkeiba_enabled=True)
    seen = []
    hook = npol.gate_pre_request(seen.append)
    hook("u")
    assert seen == ["u"]


def test_every_netkeiba_fetcher_construction_is_in_a_gated_factory():
    """Static: the only places that build a netkeiba HTTP client are the three factories that
    call ``assert_netkeiba_enabled`` (codex D25 Q1-2)."""

    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    allowed = {
        repo / "scrape/src/horseracing_scrape/cli.py",
        repo / "ops/src/horseracing_ops/runner.py",
        repo / "live/src/horseracing_live/chaos_politeness.py",
    }
    hits = {}
    for pkg in ("scrape", "ops", "live", "serving", "betting", "training"):
        for path in (repo / pkg / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"\b\w*HttpFetcher\(", text):  # Capture… subclasses too
                line = text[: m.start()].count("\n") + 1
                snippet = text.splitlines()[line - 1]
                if "class " in snippet or "isinstance" in snippet:
                    continue
                hits.setdefault(path, []).append(line)
    assert set(hits) == allowed, hits
    for path in allowed:
        assert "assert_netkeiba_enabled()" in path.read_text(encoding="utf-8"), path


def test_env_policy_path_must_be_absolute_and_fingerprint_names_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv(npol.POLICY_PATH_ENV, "relative/policy.json")
    with pytest.raises(npol.PolicyFlagInvalid):
        npol.policy_path()
    p = _policy(tmp_path, monkeypatch, netkeiba_enabled=False)
    fp = npol.policy_fingerprint()
    assert fp["path"] == str(p) and len(fp["sha256"]) == 64
    monkeypatch.setenv(npol.POLICY_PATH_ENV, str(tmp_path / "absent.json"))
    assert npol.policy_fingerprint()["sha256"] is None
