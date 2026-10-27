"""Synthetic boundary and provenance checks for the read-only saved-data audit."""

import gzip
import hashlib
import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest


HERE = Path(__file__).resolve().parents[1] / "roi_explore/odds_settlement_audit_155"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


m = load_module("odds155_test_analysis", HERE / "analysis.py")
a = load_module("odds155_test_archive", HERE / "archive.py")


@pytest.mark.parametrize(("odds", "payout", "status", "delta"), [
    ("2.4", 240, "exact", 0),
    ("2.30", 230, "exact", 0),
    ("2.4", 250, "near_10_yen", 10),
    ("2.4", 230, "near_10_yen", -10),
    ("2.4", 251, "mismatch", 11),
    ("2.4", 229, "mismatch", -11),
    ("1.0", 100, "exact", 0),
    (None, 240, "missing_or_invalid", None),
    ("NaN", 240, "missing_or_invalid", None),
    ("Infinity", 240, "missing_or_invalid", None),
    ("---", 240, "missing_or_invalid", None),
    ("0.9", 100, "missing_or_invalid", None),
    ("1.0", 70, "missing_or_invalid", None),
    ("2.4", None, "missing_or_invalid", None),
])
def test_price_match_decimal_and_thresholds(odds, payout, status, delta):
    assert m.price_match(odds, payout) == (status, delta)


def test_dead_heat_is_never_ordinary_price_equality():
    assert m.price_match("26.5", 1340, dead_heat=True) == ("dead_heat_separate", None)


@pytest.mark.parametrize(("event", "post", "expected"), [
    (None, "2026-10-25T06:00:00Z", "missing_event"),
    ("bad date", None, "missing_event"),
    ("2026-10-25T05:59:59Z", None, "post_time_unknown"),
    ("2026-10-25T05:59:59Z", "2026-10-25T06:00:00Z", "before_post"),
    ("2026-10-25T15:00:00+09:00", "2026-10-25T06:00:00Z", "at_or_after_post"),
    ("2026-10-25T06:00:01Z", "2026-10-25T06:00:00Z", "at_or_after_post"),
])
def test_timestamp_class_unknown_and_strict_before(event, post, expected):
    assert m.timestamp_class(event, post) == expected


@pytest.fixture
def race_data():
    race = dict(race_id="202605040907", race_date="2026-10-25", track_type="芝",
                post_time="2026-10-25T06:00:00Z", win_odds_observed_at=None,
                win_odds_official_at=None)
    horses = [dict(horse_id="2021100001", horse_number=1, entry_status="started", odds="2.4"),
              dict(horse_id="2021100002", horse_number=2, entry_status="started", odds="4.0")]
    results = [dict(horse_id="2021100001", finish_order=1, result_status="finished"),
               dict(horse_id="2021100002", finish_order=2, result_status="finished")]
    payouts = [dict(horse_number=1, payout_yen=240)]
    return race, horses, results, payouts


def test_complete_field_and_nonstarter_odds_do_not_enter_inverse_sum(race_data):
    race, horses, results, payouts = race_data
    horses.append(dict(horse_id="2021100003", horse_number=3, entry_status="excluded", odds="1.0"))
    record = m.inspect_race(race, horses, results, payouts)
    assert record["field_status"] == "complete"
    assert record["payout_status"] == "consistent"
    assert record["winner_price_status"] == "exact"
    assert record["n_entries"] == 3 and record["n_started"] == 2
    assert record["inverse_odds_sum"] == pytest.approx(1 / 2.4 + 1 / 4)
    assert record["underround"] is True


@pytest.mark.parametrize("status", ["stopped", "disqualified"])
def test_dnf_is_a_started_result_not_missing(race_data, status):
    race, horses, results, payouts = race_data
    results[1].update(finish_order=None, result_status=status)
    record = m.inspect_race(race, horses, results, payouts)
    assert record["field_status"] == "complete"
    assert record["winner_price_status"] == "exact"
    assert record["n_results"] == 2


def test_missing_result_excludes_entire_race_even_when_winner_and_payout_match(race_data):
    race, horses, results, payouts = race_data
    record = m.inspect_race(race, horses, results[:1], payouts)
    assert record["field_status"] == "inconsistent_or_incomplete"
    assert record["n_missing_results"] == 1
    assert record["winner_price_status"] == "field_not_complete"


def test_no_result_and_no_payout_are_explicit_missing(race_data):
    race, horses, _, _ = race_data
    record = m.inspect_race(race, horses, [], [])
    assert record["field_status"] == "no_results"
    assert record["payout_status"] == "missing"


def test_duplicate_starter_numbers_reject_field(race_data):
    race, horses, results, payouts = race_data
    horses[1]["horse_number"] = 1
    record = m.inspect_race(race, horses, results, payouts)
    assert record["duplicate_starter_numbers"] is True
    assert record["field_status"] == "inconsistent_or_incomplete"


@pytest.mark.parametrize("number", [None, float("nan"), 0, -1, 1.5])
def test_invalid_starter_number_rejects_field(race_data, number):
    race, horses, results, payouts = race_data
    horses[1]["horse_number"] = number
    record = m.inspect_race(race, horses, results, payouts)
    assert record["invalid_starter_number"] is True
    assert record["field_status"] == "inconsistent_or_incomplete"


def test_nk_prefix_does_not_silently_resolve_current_result_identity(race_data):
    race, horses, results, payouts = race_data
    results[1]["horse_id"] = "nk:" + results[1]["horse_id"]
    record = m.inspect_race(race, horses, results, payouts)
    assert record["n_missing_results"] == 1 and record["n_nonstarter_results"] == 1
    assert record["field_status"] == "inconsistent_or_incomplete"


@pytest.mark.parametrize("payouts", [
    [dict(horse_number=2, payout_yen=240)],
    [dict(horse_number=1, payout_yen=240), dict(horse_number=1, payout_yen=240)],
    [dict(horse_number=1, payout_yen=70)],
])
def test_payout_must_match_complete_winner_number_set(race_data, payouts):
    race, horses, results, _ = race_data
    record = m.inspect_race(race, horses, results, payouts)
    assert record["field_status"] == "complete"
    assert record["payout_status"] == "inconsistent"
    assert record["winner_price_status"] == "payout_inconsistent"


def test_dead_heat_requires_all_winning_payouts(race_data):
    race, horses, results, _ = race_data
    results[1]["finish_order"] = 1
    payouts = [dict(horse_number=1, payout_yen=120), dict(horse_number=2, payout_yen=200)]
    record = m.inspect_race(race, horses, results, payouts)
    assert record["n_winners"] == 2
    assert record["payout_status"] == "consistent"
    assert record["winner_price_status"] == "dead_heat_separate"
    assert m.inspect_race(race, horses, results, payouts[:1])["payout_status"] == "inconsistent"


def test_field_digest_is_order_and_duplicate_independent_but_identity_sensitive():
    expected = hashlib.sha256(b"2021100001\n2021100002").hexdigest()
    assert m.field_digest(["2021100002", "2021100001", "2021100001"]) == expected
    assert m.field_digest(["nk:2021100001", "2021100002"]) != expected
    assert m.field_digest([]) == hashlib.sha256(b"").hexdigest()


def test_only_explicit_mapped_netkeiba_identity_is_resolved():
    frame = pd.DataFrame([
        dict(source="netkeiba", source_id="raw1", canonical_id="canonical1", mapping_status="mapped"),
        dict(source="netkeiba", source_id="raw2", canonical_id="canonical2", mapping_status="conflict"),
        dict(source="netkeiba", source_id="raw3", canonical_id="canonical3", mapping_status="rejected"),
        dict(source="netkeiba", source_id="raw4", canonical_id="canonical4", mapping_status="unmapped"),
        dict(source="netkeiba", source_id="raw5", canonical_id=None, mapping_status="mapped"),
        dict(source="other", source_id="raw6", canonical_id="canonical6", mapping_status="mapped"),
    ])
    assert m.resolved_mapping(frame) == {"raw1": "canonical1"}
    with pytest.raises(ValueError, match="Ambiguous"):
        m.resolved_mapping(pd.concat([frame, frame.iloc[:1]], ignore_index=True))


def result_html(race_id="202605040907", order="1", payout=240):
    def row(status, number, odds):
        cells = [status, "1", str(number), f'<a href="/horse/202110000{number}/">horse</a>',
                 "牡3", "56", "rider", "1:33.3", "", str(number), odds]
        return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
    return (f'<html><head><link rel="canonical" href="https://race.netkeiba.com/race/result.html?race_id={race_id}">'
            '<title>2026年10月25日</title></head><body><table class="RaceTable01">'
            + row(order, 1, "2.40") + row("取消", 2, "---")
            + '</table><table class="Payout_Detail_Table"><tr class="Tansho"><th>単勝</th>'
            f'<td class="Result"><div><span>1</span></div></td><td class="Payout">{payout}円</td>'
            '</tr></table></body></html>')


def make_archive(tmp_path, monkeypatch, versions):
    monkeypatch.setattr(a, "ROOT", tmp_path)
    folder = tmp_path / "artifacts/scrape_archive/race.netkeiba.com/testhash"
    folder.mkdir(parents=True)
    (folder / "url.txt").write_text(
        "https://race.netkeiba.com/race/result.html?race_id=202605040907\n")
    for name, html in versions:
        (folder / name).write_bytes(gzip.compress(html.encode("utf-8")))
    return folder


def test_archive_preserves_all_versions_and_selects_latest_result_table(tmp_path, monkeypatch):
    latest = "20261025T061000000000Z.html.gz"
    make_archive(tmp_path, monkeypatch, [
        ("20261025T060000000000Z.html.gz", result_html(payout=250)),
        (latest, result_html()),
        ("20261025T062000000000Z.html.gz", '<html>not published</html>'),
    ])
    pages, horses, payouts = a.load_archives()
    assert len(pages) == 3 and pages.selected.sum() == 1
    chosen = pages.loc[pages.selected].iloc[0]
    assert chosen.archive_path.endswith(latest)
    assert chosen.body_race_id_matches and chosen.body_race_id_method == "canonical_or_og"
    assert chosen.fetched_at_source == "filename"
    assert chosen.html_utf8_sha256 == hashlib.sha256(result_html().encode()).hexdigest()
    assert len(horses) == 2 and horses.horse_id.tolist() == ["2021100001", "2021100002"]
    assert horses.iloc[0].final_odds_text == "2.40"
    assert horses.iloc[1].entry_status == "cancelled" and not horses.iloc[1].is_started
    assert payouts.payout_yen.tolist() == [240]


def test_unreadable_latest_result_table_is_not_replaced_by_older_agreement(tmp_path, monkeypatch):
    latest = "20261025T061000000000Z.html.gz"
    make_archive(tmp_path, monkeypatch, [
        ("20261025T060000000000Z.html.gz", result_html()),
        (latest, result_html(order="unknown")),
    ])
    pages, horses, _ = a.load_archives()
    chosen = pages.loc[pages.selected].iloc[0]
    assert chosen.archive_path.endswith(latest) and chosen.result_parse_status == "parse_error"
    assert horses.iloc[0].row_parse_error == "unknown_status"


def test_wrong_body_id_and_mtime_proxy_remain_explicit(tmp_path, monkeypatch):
    make_archive(tmp_path, monkeypatch, [
        ("observation.html.gz", result_html(race_id="202605040908", payout=70)),
    ])
    pages, _, payouts = a.load_archives()
    page = pages.iloc[0]
    assert page.selected and not page.body_race_id_matches
    assert page.fetched_at_source == "mtime_proxy" and page.selection_time_proxy
    assert pd.isna(page.archive_filename_fetched_at)
    assert page.payout_parse_status == "parse_error" and payouts.empty


def test_special_payout_text_flag_does_not_match_race_name_special(tmp_path, monkeypatch):
    html = result_html().replace("</body>", "特別競走 特別払戻 返還</body>")
    folder = make_archive(tmp_path, monkeypatch, [("20261025T061000000000Z.html.gz", html)])
    page, _, _ = a._load_page(folder / "20261025T061000000000Z.html.gz",
                              "202605040907", "https://race.netkeiba.com/race/result.html?race_id=202605040907")
    assert page["has_special_payout_text"] and page["has_refund_text"]
    html = result_html().replace("</body>", "特別競走</body>")
    (folder / "20261025T061000000000Z.html.gz").write_bytes(gzip.compress(html.encode()))
    page, _, _ = a._load_page(folder / "20261025T061000000000Z.html.gz",
                              "202605040907", "https://race.netkeiba.com/race/result.html?race_id=202605040907")
    assert not page["has_special_payout_text"] and not page["has_refund_text"]
