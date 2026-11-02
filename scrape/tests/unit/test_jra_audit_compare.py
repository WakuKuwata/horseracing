"""176 T080: the audit comparison functions are pure and keep exclusions out of the denominator."""

from __future__ import annotations

import datetime

from horseracing_db.enums import EntryStatus

from horseracing_scrape.jra.audit import (
    AuditReport,
    StoredHorse,
    StoredRace,
    compare_entries,
    compare_results,
)
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.parse.results import parse_results_day
from tests.jra_fixtures import jra_text

D = datetime.date(2026, 11, 1)


def _entry(rid="202605041111"):
    page = parse_entries_day(jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)
    return next(b.payload for b in page.published() if b.race_id == rid)


def _stored_from(entry, *, nk_first=False, jockey_override=None, prov=None, mapped=None):
    sr = StoredRace(entry.race.race_id, horse_prov=prov or {}, mapped=mapped or {})
    for i, h in enumerate(entry.horses):
        hid = f"nk:{h.horse_id}" if (nk_first and i == 0) else h.horse_id
        jockey = jockey_override if (jockey_override and i == 1) else h.jockey_id
        sr.horses[hid] = StoredHorse(
            hid,
            h.horse_number,
            h.frame,
            jockey,
            h.trainer_id,
            h.jockey_weight,
            h.entry_status,
            None,
            None,
            None,
        )
    return sr


def test_identical_store_matches_every_comparable_field():
    e = _entry()
    rep = AuditReport()
    compare_entries(e, _stored_from(e), rep)
    for f in ("horse_id", "jockey_id", "trainer_id", "frame", "jockey_weight", "entry_status"):
        assert rep.f(f).comparable == len(e.horses) == rep.f(f).matched, f
    # body weights were not yet published on the morning page → excluded, not mismatched
    assert rep.f("body_weight").comparable == 0
    assert rep.f("body_weight").excluded_by_reason == {"not_published": len(e.horses)}
    # no provenance in the store: compared, but counted as unknown source
    assert rep.f("jockey_id").stored_source_unknown == len(e.horses)


def test_mismatch_example_carries_source_or_none():
    e = _entry()
    second = e.horses[1]
    rep = AuditReport()
    compare_entries(
        e,
        _stored_from(e, jockey_override="99999", prov={(second.horse_id, "jockey"): "netkeiba"}),
        rep,
    )
    fs = rep.f("jockey_id")
    assert fs.mismatched == 1 and fs.matched == len(e.horses) - 1
    ex = fs.examples[0]
    assert (
        ex["stored"] == "99999"
        and ex["official"] == second.jockey_id
        and ex["stored_source"] == "netkeiba"
    )


def test_surrogate_rows_are_excluded_and_become_merge_candidates():
    e = _entry()
    rep = AuditReport()
    compare_entries(e, _stored_from(e, nk_first=True), rep)
    first = e.horses[0].horse_id
    assert rep.merge_candidates == {(f"nk:{first}", first)}
    assert rep.f("jockey_id").excluded_by_reason == {"unresolved_id": 1}
    assert rep.f("jockey_id").comparable == len(e.horses) - 1
    # an approved mapping whose rows were never re-keyed is reported separately
    rep2 = AuditReport()
    compare_entries(e, _stored_from(e, nk_first=True, mapped={first: first}), rep2)
    assert (
        rep2.approved_mapping_pending_rekey == {(f"nk:{first}", first)}
        and not rep2.merge_candidates
    )


def test_missing_race_is_out_of_the_denominator():
    e = _entry()
    rep = AuditReport()
    compare_entries(e, None, rep)
    assert rep.f("frame").comparable == 0
    assert rep.f("frame").excluded_by_reason == {"race_not_in_db": len(e.horses)}


def test_results_against_a_pre_result_store_are_stage_differs():
    page = parse_results_day(
        jra_text("results_tokyo_20261031.sjis.html"), expected_date=datetime.date(2026, 10, 31)
    )
    res = page.published()[0].payload
    sr = StoredRace(res.race.race_id)
    for r in res.rows:
        sr.horses[r.horse_id] = StoredHorse(
            r.horse_id,
            r.horse_number,
            r.frame,
            r.jockey_id,
            r.trainer_id,
            r.jockey_weight,
            EntryStatus.STARTED,
            None,
            None,
            3,
        )
    rep = AuditReport()
    compare_results(res, sr, rep)
    assert rep.f("finish_order").comparable == 0
    assert rep.f("finish_order").excluded_by_reason == {"stage_differs": len(res.rows)}
    # once results exist, popularity is compared and its disagreement is the finding
    for r in res.rows:
        sr.results[r.horse_id] = (r.finish_order, r.result_status, None, r.last_3f)
    rep = AuditReport()
    compare_results(res, sr, rep)
    assert rep.f("finish_order").matched == len(res.rows)
    assert rep.f("popularity").comparable == len(res.rows)
    assert rep.f("popularity").mismatched == sum(1 for r in res.rows if r.popularity != 3)
