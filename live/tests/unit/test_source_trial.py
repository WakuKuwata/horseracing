"""178 T062 (pure parts): arrival schedule and delay injection, the 段 0 arrivals table, the
official quote conversion, the frozen config, the outbound block, and the T064a policy key."""

from __future__ import annotations

import datetime
import json
import socket
from types import SimpleNamespace

import pytest

from horseracing_live import near_post as np_
from horseracing_live import source_trial as st

JST = st.JST
DAY = datetime.date(2026, 9, 13)  # a Sunday


def test_default_arrivals_follow_the_member_site_schedule():
    a = st.default_arrivals(DAY)
    assert a["KYI"] == datetime.datetime(2026, 9, 12, 20, 0, tzinfo=JST)
    assert a["TYB"] == datetime.datetime(2026, 9, 13, 9, 0, tzinfo=JST)
    assert a["HJC"] == datetime.datetime(2026, 9, 13, 18, 30, tzinfo=JST)
    assert a["SED"] == datetime.datetime(2026, 9, 17, 16, 0, tzinfo=JST)  # the next Thursday
    assert a["SED"] == a["SRB"] and a["SED"].weekday() == 3
    # a Thursday race day: the formal SED is the FOLLOWING Thursday, never the same day
    thu = datetime.date(2026, 9, 17)
    assert st.default_arrivals(thu)["SED"].date() == datetime.date(2026, 9, 24)


def test_apply_delay_and_parse_duration():
    a = st.default_arrivals(DAY)
    d = st.apply_delay(a, {"KYI": st.parse_duration("2h"), "XXX": st.parse_duration("1m")})
    assert d["KYI"] == a["KYI"] + datetime.timedelta(hours=2) and d["BAC"] == a["BAC"]
    assert st.parse_duration("90s") == datetime.timedelta(seconds=90)
    assert st.parse_duration("1d") == datetime.timedelta(days=1)
    with pytest.raises(ValueError):
        st.parse_duration("3x")


def test_arrivals_from_probe_matches_this_days_files_and_takes_the_first_present(tmp_path):
    from horseracing_live import source_probe as sp
    from horseracing_scrape.jrdb import fetch as jrdb_fetch

    sha = {
        label: jrdb_fetch.source_url_sha256(url) for label, url in sp.arrival_targets(DAY).items()
    }
    other = jrdb_fetch.source_url_sha256(
        sp.arrival_targets(DAY - datetime.timedelta(days=7))["KYI"]
    )
    recs = [
        {
            "mode": "arrivals",
            "label": "KYI",
            "source_url_sha256": sha["KYI"],
            "status": "absent:HTTPStatusError",
            "observed_at": "2026-09-12T19:00:00+09:00",
            "last_modified": None,
        },
        {
            "mode": "arrivals",
            "label": "KYI",
            "source_url_sha256": sha["KYI"],
            "status": "present",
            "observed_at": "2026-09-12T20:10:00+09:00",
            "last_modified": "2026-09-12T20:03:00+09:00",
        },
        {
            "mode": "arrivals",
            "label": "KYI",
            "source_url_sha256": sha["KYI"],
            "status": "present",
            "observed_at": "2026-09-12T20:20:00+09:00",
            "last_modified": "2026-09-12T20:03:00+09:00",
        },
        {
            "mode": "arrivals",
            "label": "KYI",
            "source_url_sha256": other,
            "status": "present",
            "observed_at": "2026-09-05T20:00:00+09:00",
            "last_modified": None,
        },
        {
            "mode": "arrivals",
            "label": "TYB_tyokuzen",
            "source_url_sha256": sha["TYB_tyokuzen"],
            "status": "present",
            "observed_at": "2026-09-13T09:31:00+09:00",
            "last_modified": None,
        },
        {
            "mode": "arrivals",
            "label": "SED_sokuho",
            "source_url_sha256": sha["SED_sokuho"],
            "status": "present",
            "observed_at": "2026-09-13T18:05:00+09:00",
            "last_modified": None,
        },
        {"mode": "official-odds", "race_id": "x"},
    ]
    path = tmp_path / "a.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    got = st.arrivals_from_probe(path, DAY)
    # the first PRESENT observation (not Last-Modified), this day's file only, no 速報版
    assert got == {
        "KYI": datetime.datetime(2026, 9, 12, 20, 10, tzinfo=JST),
        "TYB": datetime.datetime(2026, 9, 13, 9, 31, tzinfo=JST),
    }


def test_official_quote_converts_the_official_rows_to_the_174_shape():
    odds = SimpleNamespace(
        race_id="202605040111",
        official_at=datetime.datetime(2026, 11, 1, 15, 26, tzinfo=JST),
        rows=(
            SimpleNamespace(horse_number=1, win_odds=2.5, place_low=1.1, place_high=1.3),
            SimpleNamespace(horse_number=2, win_odds=None, place_low=None, place_high=None),
            SimpleNamespace(horse_number=None, win_odds=9.9, place_low=None, place_high=None),
        ),
    )
    q = st.official_quote(odds)
    assert [(r.horse_number, r.odds, r.popularity) for r in q.rows] == [
        (1, 2.5, None),
        (2, None, None),
    ]
    assert [(r.horse_number, r.odds_low, r.odds_high) for r in q.place_rows] == [(1, 1.1, 1.3)]
    assert q.official_at == odds.official_at
    rows = np_.quote_field(q, [("H1", 1), ("H2", 2)])
    assert (
        rows[0]["win_odds"] == 2.5 and rows[0]["place_low"] == 1.1 and rows[1]["win_odds"] is None
    )


def test_config_is_frozen_once_and_names_the_inherited_data(tmp_path):
    a = st.default_arrivals(DAY)
    cfg = st.build_config(
        DAY,
        files_dir=tmp_path,
        arrivals=a,
        arrivals_basis="default",
        delay={"KYI": datetime.timedelta(hours=2)},
        emergency_at=datetime.datetime(2026, 9, 13, 8, 0, tzinfo=JST),
        replay_mode="fake_clock",
        inherited_state_={"races_before_day": 0},
        file_sha256={"KYI/x.txt": "0" * 64},
    )
    path = st.freeze_config(cfg, tmp_path / "trial-config.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["delay_s"] == {"KYI": 7200.0} and doc["blocked_hosts"] == list(st.BLOCKED_HOSTS)
    assert doc["inherited"]["person_mapping"].startswith("id_mappings(source=jrdb")
    assert doc["logic_version"].endswith("src=jra_official") and doc["frozen_at"]
    assert doc["replay_mode"] == "fake_clock" and doc["arrivals_basis"] == "default"
    assert doc["inherited_state"] == {"races_before_day": 0} and doc["file_sha256"] == {
        "KYI/x.txt": "0" * 64
    }
    with pytest.raises(FileExistsError):
        st.freeze_config(cfg, path)
    with pytest.raises(ValueError):
        st.build_config(
            DAY,
            files_dir=tmp_path,
            arrivals=a,
            arrivals_basis="default",
            delay=None,
            emergency_at=datetime.datetime(2026, 9, 13, 8, 0, tzinfo=JST),
            replay_mode="dreamt",
        )


def test_outbound_block_allows_only_the_resolved_hosts():
    resolve = lambda host: {"203.0.113.10"} if host == "www.jra.go.jp" else set()  # noqa: E731
    with st.block_outbound_except(("www.jra.go.jp",), resolve=resolve) as allowed:
        assert "203.0.113.10" in allowed and "127.0.0.1" in allowed
        with pytest.raises(st.OutboundBlocked):
            socket.create_connection(("198.51.100.7", 80), timeout=0.01)  # netkeiba's address
        s = socket.socket()
        with pytest.raises(st.OutboundBlocked):
            s.connect(("198.51.100.7", 443))
        s.close()
    # restored afterwards: the check no longer runs (the refused connection is now a real one)
    assert socket.create_connection is not None and socket.socket.connect is not None


def test_policy_key_near_post_source_defaults_to_netkeiba(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"collection_paused": False}), encoding="utf-8")
    pol = np_.load_capture_policy(p)
    assert np_.POLICY_SOURCE_KEY not in pol  # absent key → the 174 dict, byte for byte
    p.write_text(
        json.dumps({"collection_paused": False, "near_post_source": "jra_official"}),
        encoding="utf-8",
    )
    assert np_.load_capture_policy(p)[np_.POLICY_SOURCE_KEY] == "jra_official"
    p.write_text(
        json.dumps({"collection_paused": False, "near_post_source": "both"}), encoding="utf-8"
    )
    with pytest.raises(np_.NearPostRefused):
        np_.load_capture_policy(p)


def test_capture_race_rejects_an_unknown_quote_source():
    with pytest.raises(ValueError):
        np_.capture_race(
            None,
            scheduled=None,
            fetcher=None,
            predictor=None,
            day_features=None,
            run_id=None,
            quote_source="mixed",
        )


def test_fetcher_mismatch_is_detected_for_the_official_source():
    from horseracing_live.official_quote import is_official_fetcher

    class Netkeiba:
        def get(self, *a, **k): ...

    class Official:
        def post(self, *a, **k): ...

    assert not is_official_fetcher(Netkeiba()) and is_official_fetcher(Official())
    with pytest.raises(ValueError):  # a quote fetcher is only meaningful on the official source
        np_.capture_race(
            None,
            scheduled=None,
            fetcher=None,
            predictor=None,
            day_features=None,
            run_id=None,
            quote_source="netkeiba",
            quote_fetcher=lambda r, d: None,
        )
