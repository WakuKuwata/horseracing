"""Feature 138: attention-condition registry (T004a definitions, T007 rules and levels)."""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import hashlib
import itertools
import math
import random
import typing
from pathlib import Path

import numpy as np
import pytest

from horseracing_eval import attention_rules as ar

_SRC = Path(ar.__file__)
_UTC = dt.UTC


# ---------------------------------------------------------------- definitions (T004a)


@pytest.mark.parametrize(
    ("odds", "gap", "ev", "expected"),
    [
        (19.99, 40, 1.25, False),
        (20.0, 40, 1.25, True),
        (39.99, 40, 1.25, True),
        (40.0, 40, 1.25, False),
        (30.0, 13, 1.25, False),
        (30.0, 14, 1.25, True),
        (30.0, 112, 1.25, True),
        (30.0, 113, 1.25, False),
        (30.0, float("nan"), 1.25, False),
        (30.0, None, 1.25, False),
        (30.0, 40, 1.2, False),  # strictly greater
        (30.0, 40, 1.2 + 1e-12, True),
        (30.0, 40, None, False),
    ],
)
def test_s1_boundaries(odds, gap, ev, expected):
    s1 = ar.definition("S1")
    assert ar.matches(s1, ens_ev=ev, single_ev=None, odds=odds, days_since_last=gap) is expected


def test_scalar_and_vector_agree_on_a_grid():
    odds = [1.5, 19.99, 20.0, 25.0, 39.99, 40.0, 80.0, np.nan]
    gaps = [np.nan, 0, 13, 14, 60, 112, 113, 400]
    evs = [np.nan, 0.9, 1.1, 1.1 + 1e-9, 1.2, 1.25, 1.3, 1.31, 2.0]
    grid = np.array(list(itertools.product(odds, gaps, evs, evs)), dtype=float)
    for d in ar.RULE_DEFINITIONS:
        vec = ar.match_mask(d, ens_ev=grid[:, 2], single_ev=grid[:, 3], odds=grid[:, 0],
                            days_since_last=grid[:, 1])
        scal = [ar.matches(d, ens_ev=r[2], single_ev=r[3], odds=r[0], days_since_last=r[1])
                for r in grid]
        assert vec.tolist() == scal, d.id


def test_inclusion_s2_s1_s4_and_s1_s3():
    rng = np.random.default_rng(0)
    n = 20000
    ens = rng.uniform(0.8, 1.6, n)
    odds = rng.uniform(1.0, 80.0, n)
    gap = rng.integers(0, 200, n).astype(float)
    m = {d.id: ar.match_mask(d, ens_ev=ens, single_ev=ens, odds=odds, days_since_last=gap)
         for d in ar.RULE_DEFINITIONS}
    assert m["S2"].any() and not (m["S2"] & ~m["S1"]).any()
    assert not (m["S1"] & ~m["S4"]).any()
    assert not (m["S1"] & ~m["S3"]).any()
    applic = ar.applicable_rules(ens_ev=1.35, single_ev=1.0, odds=30.0, days_since_last=40)
    assert applic == ("S1", "S2", "S3", "S4")


def test_s5_uses_the_single_seed_value_only():
    s5 = ar.definition("S5")
    assert ar.matches(s5, ens_ev=None, single_ev=1.21, odds=None, days_since_last=None)
    assert not ar.matches(s5, ens_ev=5.0, single_ev=1.19, odds=30, days_since_last=40)
    assert ar.applicable_rules(ens_ev=1.0, single_ev=1.25, odds=3.0, days_since_last=7) == ("S5",)


def test_rule_set_constants():
    assert ar.RULE_IDS == ("S1", "S2", "S3", "S4", "S5")
    assert [d.rank for d in ar.RULE_DEFINITIONS] == [1, 2, 3, 4, 5]
    assert [d.posthoc for d in ar.RULE_DEFINITIONS] == [True, True, False, False, False]
    assert [d.control for d in ar.RULE_DEFINITIONS] == [False, False, False, False, True]
    assert ar.DISPLAYED_MARKET_EV_MODEL_VERSION == "mev-ens15-v1"
    assert ar.SINGLE_SEED_MODEL_VERSION == "mev-binary-v2"
    with pytest.raises(ValueError):
        ar.definition("S9")


def test_definitions_hash_is_stable_and_sensitive(monkeypatch):
    h = ar.definitions_sha256()
    assert len(h) == 64 and h == ar.definitions_sha256()
    changed = tuple(dataclasses.replace(d, odds_band=(20.0, 41.0)) if d.id == "S1" else d
                    for d in ar.RULE_DEFINITIONS)
    monkeypatch.setattr(ar, "RULE_DEFINITIONS", changed)
    assert ar.definitions_sha256() != h


def test_field_digest_is_order_and_duplicate_invariant():
    a = ar.field_digest(["h3", "h1", "h2"])
    assert a == ar.field_digest(["h1", "h2", "h3", "h1"])
    assert a == hashlib.sha256(b"h1\nh2\nh3").hexdigest()
    assert a != ar.field_digest(["h1", "h2"])


_ALLOWED_IMPORTS = {"__future__", "datetime", "hashlib", "json", "math", "collections.abc",
                    "dataclasses", "typing", "zoneinfo", "numpy", "horseracing_eval.bootstrap"}


def test_imports_are_whitelisted():
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            used.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            used.add(node.module or "")
    assert used <= _ALLOWED_IMPORTS, used - _ALLOWED_IMPORTS


def test_no_leak_guard_tokens_in_source():
    body = _SRC.read_text(encoding="utf-8")
    for token in ("market_ev_predictions", "MarketEvPrediction", "horseracing_training",
                  "attention_picks", "AttentionPick", "attention_race_scans", "AttentionRaceScan",
                  "attention_checkpoints", "AttentionCheckpoint", "horseracing_db", "sqlalchemy",
                  "pandas"):
        assert token not in body, token


# ---------------------------------------------------------------- prospective rules (T007)


def _ts(day: int, hour: int = 6) -> dt.datetime:
    return dt.datetime(2026, 11, 1, hour, tzinfo=_UTC) + dt.timedelta(days=day)


def _facts(**kw) -> ar.PickFacts:
    base = dict(pick_id="p", race_id="202611010101", horse_number=1, voided=False,
                computed_at=_ts(0, 1), post_time=_ts(0, 6), odds_observed_at=_ts(0, 0),
                result_pending_at_compute=True, has_race_result=True, horse_has_result=True,
                won=False, dead_heat=False, odds_used=25.0, stored_odds=24.0,
                official_payout_yen=None, race_payout_known=True, race_payout_consistent=True)
    base.update(kw)
    return ar.PickFacts(**base)


_START = dt.date(2026, 10, 15)


def test_counted_baseline_and_each_exclusion():
    assert ar.classify_pick(_facts(), start_date=_START) == "counted"
    cases = {
        "voided_scratched": dict(voided=True),
        "before_start": dict(computed_at=_ts(-30, 1), post_time=_ts(-30, 6),
                             odds_observed_at=_ts(-30, 0)),
        "post_time_unknown": dict(post_time=None),
        "computed_after_post": dict(computed_at=_ts(0, 7)),
        "result_known_at_compute": dict(result_pending_at_compute=False),
        "observed_after_post": dict(odds_observed_at=_ts(0, 6)),
        "pending_result": dict(has_race_result=False, horse_has_result=False),
        "payout_race_missing": dict(race_payout_known=False),
        "payout_inconsistent": dict(won=True),  # a winner without an official payout
        "unsettled_horse": dict(horse_has_result=False),
        "dead_heat": dict(dead_heat=True, won=True, official_payout_yen=410.0),
    }
    assert set(cases) == set(ar.EXCLUSION_ORDER)
    for expected, kw in cases.items():
        assert ar.classify_pick(_facts(**kw), start_date=_START) == expected, expected
    assert ar.classify_pick(_facts(odds_observed_at=None), start_date=_START) == "observed_after_post"
    # the other inconsistent branch: a non-winner with a payout
    assert ar.classify_pick(_facts(official_payout_yen=1230.0), start_date=_START) == (
        "payout_inconsistent")
    # a winner with its payout is counted
    assert ar.classify_pick(_facts(won=True, official_payout_yen=2450.0), start_date=_START) == (
        "counted")
    # race level (R1): a race whose payout rows disagree with its result leaves EVERY pick of it
    # out — a loser without a payout included — never only the picks the disagreement touches
    assert ar.classify_pick(_facts(race_payout_consistent=False), start_date=_START) == (
        "payout_inconsistent")
    assert ar.classify_pick(
        _facts(won=True, official_payout_yen=2450.0, race_payout_consistent=False),
        start_date=_START,
    ) == "payout_inconsistent"


def test_exclusion_order_is_the_policy_v2_order():
    assert ar.EXCLUSION_ORDER == (
        "voided_scratched", "before_start", "post_time_unknown", "computed_after_post",
        "result_known_at_compute", "observed_after_post", "pending_result",
        "payout_race_missing", "payout_inconsistent", "unsettled_horse", "dead_heat",
    )
    assert typing.get_args(ar.PickClass) == (*ar.EXCLUSION_ORDER, "counted")


def test_payout_facts_default_to_unknown():
    # a caller that does not load payouts can never count a pick (fail-closed defaults)
    f = ar.PickFacts(pick_id="p", race_id="r", horse_number=1, voided=False,
                     computed_at=_ts(0, 1), post_time=_ts(0, 6), odds_observed_at=_ts(0, 0),
                     result_pending_at_compute=True, has_race_result=True,
                     horse_has_result=True, won=False, dead_heat=False, odds_used=25.0)
    assert f.official_payout_yen is None and f.race_payout_known is False
    assert f.race_payout_consistent is False
    assert ar.classify_pick(f, start_date=_START) == "payout_race_missing"
    # a caller that sets only "known" still cannot count: consistency defaults to False
    known = dataclasses.replace(f, race_payout_known=True)
    assert ar.classify_pick(known, start_date=_START) == "payout_inconsistent"


@pytest.mark.parametrize(
    ("winners", "paid", "expected"),
    [
        ([1], [1], True),
        ([1, 2], [2, 1], True),          # a dead heat: both winners paid
        ([1], [2], False),               # the row names another horse
        ([1, 2], [1], False),            # one dead-heat winner unpaid
        ([1], [1, 2], False),            # a non-winner paid
        ([], [1], False),                # no winner but a payout
        ([None], [1], False),            # a winner whose 馬番 is unknown never matches
        ([None, 1], [1], False),
        ([], [], True),                  # nothing either side (payout_race_missing comes first)
    ],
)
def test_race_payout_consistent_is_set_equality(winners, paid, expected):
    assert ar.race_payout_consistent(winners, paid) is expected


def test_classification_is_exclusive_and_follows_precedence():
    flags = {
        "voided_scratched": dict(voided=True),
        "before_start": dict(computed_at=_ts(-30, 1)),
        "post_time_unknown": dict(post_time=None),
        "computed_after_post": dict(computed_at=_ts(0, 9)),
        "result_known_at_compute": dict(result_pending_at_compute=False),
        "observed_after_post": dict(odds_observed_at=_ts(0, 8)),
        "pending_result": dict(has_race_result=False),
        "payout_race_missing": dict(race_payout_known=False),
        "payout_inconsistent": dict(race_payout_consistent=False),  # race level
        "unsettled_horse": dict(horse_has_result=False),
        "dead_heat": dict(dead_heat=True),
    }
    assert tuple(flags) == ar.EXCLUSION_ORDER
    names = list(flags)
    for mask in range(1 << len(names)):
        on = [n for i, n in enumerate(names) if mask >> i & 1]
        if "before_start" in on and "computed_after_post" in on:
            continue  # both set computed_at; precedence covered separately
        kw: dict = {}
        for n in on:
            kw.update(flags[n])
        got = ar.classify_pick(_facts(**kw), start_date=_START)
        want = next((n for n in ar.EXCLUSION_ORDER if n in on), "counted")
        assert got == want, (on, got)


def test_start_date_none_means_everything_is_before_start(monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", None)  # before go-live
    assert ar.classify_pick(_facts()) == "before_start"
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", _START)
    assert ar.classify_pick(_facts()) == "counted"  # module constant read at call time


def test_start_date_uses_japan_time():
    start = dt.date(2026, 11, 2)
    before = dt.datetime(2026, 11, 1, 14, 59, tzinfo=_UTC)  # 23:59 JST on 11/1
    after = dt.datetime(2026, 11, 1, 15, 0, tzinfo=_UTC)  # 00:00 JST on 11/2
    post = dt.datetime(2026, 11, 2, 6, tzinfo=_UTC)
    f = dict(post_time=post, odds_observed_at=before - dt.timedelta(minutes=5))
    assert ar.classify_pick(_facts(computed_at=before, **f), start_date=start) == "before_start"
    assert ar.classify_pick(_facts(computed_at=after, **f), start_date=start) == "counted"
    assert ar.day_key(after) == "2026-11-02" and ar.day_key(before) == "2026-11-01"
    with pytest.raises(ValueError):
        ar.day_key(dt.datetime(2026, 11, 1, 6))


def _picks(n: int, *, win_every: int, odds: float, per_day: int = 7, prefix: str = "p",
           payout: float | None = None):
    """Counted picks; a winner's official payout defaults to the judged odds x 100."""
    out = []
    for i in range(n):
        day, slot = divmod(i, per_day)
        won = i % win_every == 0
        pay = (100.0 * odds if payout is None else payout) if won else None
        out.append(_facts(pick_id=f"{prefix}{i:04d}", race_id=f"2026{day:04d}{slot:04d}",
                          horse_number=1 + slot, post_time=_ts(day, 3 + slot),
                          won=won, odds_used=odds, official_payout_yen=pay))
    return out


def test_decide_checkpoint_pass_fail_continue_undecided():
    passed, rec = ar.decide_checkpoint(_picks(300, win_every=2, odds=3.0), 300, b=500)
    assert passed == "passed" and rec["roi_frozen"] == pytest.approx(1.5)
    assert rec["n_counted"] == 300 and rec["n_hits"] == 150
    failed, _ = ar.decide_checkpoint(_picks(300, win_every=10, odds=5.0), 300, b=500)
    assert failed == "failed"
    cont, rec = ar.decide_checkpoint(_picks(300, win_every=20, odds=20.0), 300, b=500)
    assert cont == "continue" and rec["ci_low"] < 1.0 < rec["ci_high"]
    undec, _ = ar.decide_checkpoint(_picks(600, win_every=20, odds=20.0), 600, b=500)
    assert undec == "undecided"
    with pytest.raises(ValueError):
        ar.decide_checkpoint(_picks(299, win_every=2, odds=3.0), 300, b=500)
    with pytest.raises(ValueError):
        ar.decide_checkpoint(_picks(400, win_every=2, odds=3.0), 400, b=500)


def test_decide_checkpoint_sorts_itself_and_cuts_mid_day():
    picks = _picks(301, win_every=20, odds=20.0)  # 7 per day → the 300th pick sits mid-day
    ref = ar.decide_checkpoint(picks, 300, b=400)
    for k in range(5):
        shuffled = picks[:]
        random.Random(k).shuffle(shuffled)
        got = ar.decide_checkpoint(shuffled, 300, b=400)
        assert got == ref
    assert ref[1]["last_pick_id"] == "p0299"
    ids = "\n".join(f"p{i:04d}" for i in range(300))
    assert ref[1]["counted_pick_ids_sha256"] == hashlib.sha256(ids.encode()).hexdigest()
    assert ref[1]["bootstrap"]["n_days"] == 43  # days 0..42 (pick 299 is on day 42)


def test_official_vs_frozen_settlement():
    won = _facts(won=True, odds_used=3.0, official_payout_yen=250.0, stored_odds=2.4)
    lost = _facts(won=False, odds_used=3.0, official_payout_yen=None)
    assert ar.official_payout(won) == 250.0 and ar.frozen_payout(won) == 300.0
    assert ar.official_payout(lost) == 0.0 and ar.frozen_payout(lost) == 0.0
    # the v1 reference reads only the frozen judged odds: stored/official odds cannot move it
    moved = dataclasses.replace(won, stored_odds=9.9, official_payout_yen=990.0)
    assert ar.frozen_payout(moved) == ar.frozen_payout(won)
    with pytest.raises(ValueError):
        ar.official_payout(_facts(won=True, official_payout_yen=None))
    assert ar.settlement_basis(ar.official_payout) == "official_win_payout"
    assert ar.settlement_basis(ar.frozen_payout) == "frozen_pick_odds"
    with pytest.raises(ValueError):
        ar.settlement_basis(lambda f: 0.0)


def test_decide_checkpoint_settles_at_the_official_payout_by_default():
    # judged odds 3.0 but the pool paid 250 yen: v2 = 1.25, the v1 reference = 1.5
    picks = _picks(300, win_every=2, odds=3.0, payout=250.0)
    decision, rec = ar.decide_checkpoint(picks, 300, b=500)
    assert rec["roi_frozen"] == pytest.approx(1.25) and rec["n_hits"] == 150
    assert rec["bootstrap"]["settlement"] == "official_win_payout"
    assert decision == "passed"
    _, v1 = ar.decide_checkpoint(picks, 300, b=500, payout_of=ar.frozen_payout)
    assert v1["roi_frozen"] == pytest.approx(1.5)
    assert v1["bootstrap"]["settlement"] == "frozen_pick_odds"
    # same picks, same order, same days: only the settlement differs
    for k in ("counted_pick_ids_sha256", "last_pick_id", "n_counted", "n_hits"):
        assert rec[k] == v1[k], k
    assert rec["bootstrap"]["day_keys_sha256"] == v1["bootstrap"]["day_keys_sha256"]
    # the official payout can turn a judged-odds pass into a fail
    low = _picks(300, win_every=2, odds=3.0, payout=150.0)
    assert ar.decide_checkpoint(low, 300, b=500)[0] == "failed"
    assert ar.decide_checkpoint(low, 300, b=500, payout_of=ar.frozen_payout)[0] == "passed"
    with pytest.raises(ValueError):
        ar.decide_checkpoint(picks, 300, b=500, payout_of=lambda f: 0.0)


def test_stage_from_records_are_the_truth():
    assert ar.stage_from({}, 0) == ("researching", None, False)
    assert ar.stage_from({}, 120) == ("observing", None, False)
    assert ar.stage_from({}, 305) == ("observing", None, True)  # reached 300, no record yet
    assert ar.stage_from({300: "failed"}, 250) == ("failed", 300, False)  # record wins
    assert ar.stage_from({300: "passed"}, 900) == ("passed", 300, False)
    assert ar.stage_from({300: "continue"}, 450) == ("observing", 300, False)
    assert ar.stage_from({300: "continue"}, 650) == ("observing", 300, True)
    assert ar.stage_from({300: "continue", 600: "undecided"}, 700) == ("undecided", 600, False)
    with pytest.raises(ValueError):
        ar.stage_from({600: "passed"}, 700)
    assert ar.next_checkpoint("observing", None) == 300
    assert ar.next_checkpoint("observing", 300) == 600
    assert ar.next_checkpoint("failed", 300) is None


def test_prospective_level():
    assert ar.prospective_level("passed", 1.3) == 3
    assert ar.prospective_level("observing", 1.05) == 2
    assert ar.prospective_level("observing", 0.97) == 1
    assert ar.prospective_level("observing", None) == 1
    for s in ("researching", "failed", "undecided"):
        assert ar.prospective_level(s, 1.5) == 1


def test_chip_rule():
    alive = {r: "researching" for r in ar.RULE_IDS}
    assert ar.chip_rule(("S1", "S2", "S3", "S4", "S5"), alive) == ("S1", True, "researching")
    assert ar.chip_rule(("S1", "S3", "S4"), alive) == ("S1", False, "researching")
    s1_failed = alive | {"S1": "failed"}
    assert ar.chip_rule(("S1", "S2", "S3", "S4"), s1_failed) == ("S2", False, "researching")
    s3_obs = alive | {"S1": "failed", "S2": "undecided", "S3": "observing"}
    assert ar.chip_rule(("S1", "S2", "S3", "S4"), s3_obs) == ("S3", False, "observing")
    dead = {r: "failed" for r in ar.RULE_IDS}
    assert ar.chip_rule(("S1", "S2", "S3", "S4"), dead) == ("S1", False, "failed")
    s2_failed = alive | {"S2": "failed"}
    assert ar.chip_rule(("S1", "S2", "S3", "S4"), s2_failed) == ("S1", False, "researching")
    assert ar.chip_rule(("S5",), alive) == (None, False, None)
    assert ar.chip_rule((), alive) == (None, False, None)


def test_chip_now():
    s1 = ar.definition("S1")
    kw = dict(current_single_ev=1.0, days_since_last=40)
    assert ar.chip_now(s1, current_ens_ev=1.25, current_odds=30.0, **kw) == "matches"
    assert ar.chip_now(s1, current_ens_ev=1.25, current_odds=45.0, **kw) == "no_longer"
    assert ar.chip_now(s1, current_ens_ev=None, current_odds=30.0, **kw) == "unknown"
    assert ar.chip_now(s1, current_ens_ev=1.25, current_odds=None, **kw) == "unknown"
    s5 = ar.definition("S5")
    assert ar.chip_now(s5, current_ens_ev=None, current_single_ev=None, current_odds=3.0,
                       days_since_last=None) == "unknown"


# ---------------------------------------------------------------- levels (spec tables)


def test_levels_follow_the_spec_tables():
    got = {r.id: (ar.backtest_level(r), ar.price_noise_level(r)) for r in ar.RULES}
    assert got == {"S1": (3, 2), "S2": (3, 2), "S3": (2, 2), "S4": (2, 2), "S5": (1, 1)}
    # the price-noise axis never reaches 3 with the frozen values → emphasis 3 is unreachable
    assert max(ar.price_noise_level(r) for r in ar.RULES) == 2


def test_rules_are_in_rank_order_and_complete():
    assert [r.id for r in ar.RULES] == list(ar.RULE_IDS)
    for r in ar.RULES:
        assert [n.sigma for n in r.price_noise] == [0.1, 0.2, 0.3]
        assert r.selected_all.n == r.backtest_all.n and r.selected_c.n == r.backtest_c.n
        assert r.selected_all.realized_roi == pytest.approx(r.backtest_all.roi, abs=1e-4)


# ---------------------------------------------------------------- frozen evidence (T006/T008)

_EVIDENCE = (Path(__file__).resolve().parents[3] / "specs" / "138-attention-conditions" / "evidence"
             / "rules_S1_S5_freeze.json")


def _evidence() -> dict:
    import json

    return json.loads(_EVIDENCE.read_text(encoding="utf-8"))


def test_registry_definitions_are_the_frozen_ones():
    ev = _evidence()
    assert ev["definitions_sha256"] == ar.definitions_sha256()
    assert ev["rule_set_version"] == ar.RULE_SET_VERSION
    assert ev["definition_semantics"] == ar.DEFINITION_SEMANTICS
    assert ev["adoption_gate"]["passed"] is True  # D15: otherwise the display switch is not shipped
    assert ar.FROZEN_SOURCE.startswith("production-ens15-refreeze")


def test_registry_statistics_match_the_evidence_to_3_decimals():
    ev = _evidence()["rules"]
    for r in ar.RULES:
        e = ev[r.id]
        for w, fs in (("ALL", r.backtest_all), ("C", r.backtest_c)):
            assert round(fs.roi, 3) == round(e[w]["roi"], 3), (r.id, w)
            assert round(fs.ci_low, 3) == round(e[w]["ci95"][0], 3), (r.id, w)
            assert round(fs.ci_high, 3) == round(e[w]["ci95"][1], 3), (r.id, w)
            assert round(fs.p_one_sided, 3) == round(e[w]["p_one_sided"], 3), (r.id, w)
            assert (fs.n, fs.hits) == (e[w]["n"], e[w]["hits"]), (r.id, w)
        assert r.bets_2024_25_26 == tuple(e["bets_by_year"][y] for y in ("2024", "2025", "2026"))
        for pn in r.price_noise:
            x = e["price_noise_ALL"][str(pn.sigma)]
            assert (round(pn.roi, 3), pn.n, round(pn.overlap, 3)) == (
                round(x["roi"], 3), x["n"], round(x["overlap_with_unperturbed"], 3))
        for w, sc in (("ALL", r.selected_all), ("C", r.selected_c)):
            x = e["selected_calibration"][w]
            assert (sc.n, round(sc.mean_ev, 3), round(sc.realized_roi, 3)) == (
                x["n"], round(x["mean_ev"], 3), round(x["realized_roi"], 3))


def test_frozen_bootstrap_matches_the_evidence():
    ev = _evidence()["bootstrap"]
    for k in ("impl", "b", "seed", "block", "block_universe", "day_key_order", "rng"):
        assert ar.FROZEN_BOOTSTRAP[k] == ev[k], k


def test_order_key_follows_checkpoint_order(monkeypatch):
    f = _facts(pick_id="z", race_id="r2", horse_number=3)
    assert ar.order_key(f) == (f.post_time, "r2", 3, "z")
    monkeypatch.setattr(ar, "CHECKPOINT_ORDER", ("race_id", "pick_id"))
    assert ar.order_key(f) == ("r2", "z")


def test_go_live_date_is_pinned():
    # policy v2 (139 D12) counts from its own start; moving it requires a new policy version.
    # v1 ran 2026-10-02..04 (JST) and settled at the judged odds.
    assert ar.SELECTION_POLICY_VERSION == "v2"
    assert ar.PROSPECTIVE_START_DATE == dt.date(2026, 10, 5)
    assert ar.V1_START_DATE == dt.date(2026, 10, 2)
    assert ar.V1_START_DATE < ar.PROSPECTIVE_START_DATE


def test_v1_period_picks_are_before_start_under_v2():
    post = dt.datetime(2026, 10, 5, 6, tzinfo=_UTC)
    last_v1 = dt.datetime(2026, 10, 4, 14, 59, tzinfo=_UTC)  # 23:59 JST on 10/4
    first_v2 = dt.datetime(2026, 10, 4, 15, 0, tzinfo=_UTC)  # 00:00 JST on 10/5
    f = dict(post_time=post, odds_observed_at=last_v1 - dt.timedelta(minutes=5))
    assert ar.classify_pick(_facts(computed_at=last_v1, **f)) == "before_start"
    assert ar.classify_pick(_facts(computed_at=first_v2, **f)) == "counted"


# ---------------------------------------------------------------- buy-time expectation (139)


def test_buy_time_expectation_shape():
    """v2 (r02_verification.md): a 5% range with its CI envelope per rule, S2 only "included in
    S1" (its own interval is invalid), every number a plain float."""
    bte = ar.BUY_TIME_EXPECTATION
    assert tuple(bte) == ar.RULE_IDS
    for rid, exp in bte.items():
        assert isinstance(exp, ar.BuyTimeExpectation), rid
        if exp.included_in is not None:
            continue
        for v in (exp.range_low, exp.range_high, exp.ci_low, exp.ci_high):
            assert isinstance(v, float) and 0.0 < v < 2.0, rid
        assert exp.ci_low <= exp.range_low <= exp.range_high <= exp.ci_high, rid
    assert bte["S2"] == ar.BuyTimeExpectation(None, None, None, None, "S1")
    src = ar.BUY_TIME_EXPECTATION_SOURCE
    for key in ("report", "period", "pairs", "races", "race_days", "method", "status",
                "verification"):
        assert key in src, key
    assert src["report"].startswith("docs/roi-missed-patterns-20261004/report.md")
    assert (src["pairs"], src["races"], src["race_days"]) == (564, 444, 17)
    assert src["version"] == ar.BUY_TIME_EXPECTATION_VERSION == "buy-time-v2"
    assert src["verification"] == (
        "specs/139-official-payout-settlement/evidence/r02_verification.md")
    # the verification the status names is a file of the repository
    assert (Path(__file__).resolve().parents[3] / src["verification"]).is_file()


def test_buy_time_expectation_values_v2():
    """The exact served numbers (r02_verification.md): S1 0.85-0.90 [0.73, 1.09], S2 in S1,
    S3 0.85 [0.76, 0.98], S4 0.80-0.85 [0.72, 0.95], S5 0.80-0.85 [0.68, 0.92]."""
    got = {rid: (e.range_low, e.range_high, e.ci_low, e.ci_high, e.included_in)
           for rid, e in ar.BUY_TIME_EXPECTATION.items()}
    assert got == {
        "S1": (0.85, 0.90, 0.73, 1.09, None),
        "S2": (None, None, None, None, "S1"),
        "S3": (0.85, 0.85, 0.76, 0.98, None),
        "S4": (0.80, 0.85, 0.72, 0.95, None),
        "S5": (0.80, 0.85, 0.68, 0.92, None),
    }


def test_buy_time_interval_includes_one_only_for_s1():
    # S1's interval reaches 100%; S3/S4/S5's upper ends are below it; S2 shows no value of its own
    bte = ar.BUY_TIME_EXPECTATION
    assert bte["S1"].interval_includes_one is True
    assert [bte[r].interval_includes_one for r in ("S3", "S4", "S5")] == [False] * 3
    assert bte["S2"].interval_includes_one is None
    assert ar.BuyTimeExpectation(0.95, 1.0, 0.9, 1.0, None).interval_includes_one is True


def test_buy_time_expectation_s2_is_included_in_s1_which_is_its_superset():
    """S2 ⊂ S1 — same odds band and gap, a stricter EV threshold, same EV series — so every S2
    horse is an S1 horse and S1's value covers it."""
    s1, s2 = ar.definition("S1"), ar.definition("S2")
    assert s1.odds_band == s2.odds_band and s1.gap_days == s2.gap_days
    assert s1.uses_ensemble == s2.uses_ensemble and s2.ev_gt > s1.ev_gt
    assert ar.BUY_TIME_EXPECTATION["S2"].included_in == "S1"
    assert ar.BUY_TIME_EXPECTATION["S1"].included_in is None


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((0.85, 0.90, 0.73, None, None), "whole"),  # partial numbers
        ((None, None, None, None, None), "included_in"),  # no numbers and no rule
        ((None, None, None, None, "S9"), "unknown rule"),
        ((0.85, 0.90, 0.73, 1.09, "S1"), "no included_in"),  # numbers AND included_in
        ((0.90, 0.85, 0.73, 1.09, None), "range_low"),  # reversed range
        ((0.85, 0.90, 0.86, 1.09, None), "contain"),  # interval above the range's low end
        ((0.85, 0.90, 0.73, 0.89, None), "contain"),  # interval below the range's high end
        ((0.85, 0.87, 0.73, 1.09, None), "multiples"),  # not on the 5% grid
        ((0.895, 0.895, 0.73, 1.09, None), "multiples"),  # the v1 3-digit point is refused
        ((0.85, math.inf, 0.73, math.inf, None), "finite"),
    ],
)
def test_buy_time_expectation_invariants(args, message):
    with pytest.raises(ValueError, match=message):
        ar.BuyTimeExpectation(*args)


def test_buy_time_expectation_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ar.BUY_TIME_EXPECTATION["S1"].range_low = 0.95  # type: ignore[misc]


_BUY_TIME_EVIDENCE = (Path(__file__).resolve().parents[3] / "specs"
                      / "139-official-payout-settlement" / "evidence"
                      / "buy_time_expectation.json")


def _buy_time_evidence() -> dict:
    import json

    return json.loads(_BUY_TIME_EVIDENCE.read_text(encoding="utf-8"))["versions"]


def test_buy_time_expectation_is_pinned_to_its_version():
    """D13: the registry's numbers are exactly the evidence entry of its version — a changed
    number without a new version (and a new evidence entry) fails here."""
    entry = _buy_time_evidence()[ar.BUY_TIME_EXPECTATION_VERSION]
    assert {rid: dataclasses.asdict(e) for rid, e in ar.BUY_TIME_EXPECTATION.items()} == (
        entry["values"])
    src = {k: v for k, v in ar.BUY_TIME_EXPECTATION_SOURCE.items()
           if k not in ("version", "status")}
    for key, value in src.items():
        assert entry["source"][key] == value, key
    assert entry["source"].get("version", ar.BUY_TIME_EXPECTATION_VERSION) == (
        ar.BUY_TIME_EXPECTATION_VERSION)
    # the served/hidden state agrees with the evidence (D6)
    assert entry["verified"] is ar.BUY_TIME_EXPECTATION_VERIFIED
    assert (entry["verification"] is None) == (not ar.BUY_TIME_EXPECTATION_VERIFIED)
    assert entry["verification"].startswith(ar.BUY_TIME_EXPECTATION_SOURCE["verification"])


def _round_to_step(x: float, step: float) -> float:
    return round(round(x / step) * step, 10)


def test_buy_time_v2_values_follow_from_the_recorded_estimators():
    """The served range/interval are a mechanical function of the two recorded estimators:
    range = each point rounded to the nearest 0.05, interval = the envelope of both CIs rounded
    outward to 0.01. A rule whose estimators lack an interval shows no value (S2)."""
    entry = _buy_time_evidence()["buy-time-v2"]
    for rid in ar.RULE_IDS:
        est = entry["estimators"][rid]
        exp = ar.BUY_TIME_EXPECTATION[rid]
        if any(v is None for pair in est.values() for v in pair):
            assert exp.included_in is not None, rid
            continue
        points = sorted(_round_to_step(e[0], 0.05) for e in est.values())
        lo = min(e[1] for e in est.values())
        hi = max(e[2] for e in est.values())
        assert (exp.range_low, exp.range_high) == pytest.approx(tuple(points), abs=1e-12), rid
        assert exp.ci_low == pytest.approx(math.floor(lo * 100 + 1e-9) / 100, abs=1e-12), rid
        assert exp.ci_high == pytest.approx(math.ceil(hi * 100 - 1e-9) / 100, abs=1e-12), rid


#: buy-time-v1 as first recorded (single 3-digit point + CI) — append-only: kept in the evidence,
#: never served again
_BUY_TIME_V1 = {
    "S1": [0.895, 0.735, 1.088],
    "S2": [0.891, None, None],
    "S3": [0.856, 0.763, 0.977],
    "S4": [0.831, 0.726, 0.948],
    "S5": [0.786, 0.687, 0.898],
}


def test_every_recorded_buy_time_version_keeps_its_numbers():
    """Versions are append-only: two versions never share a name, each names one value set, and
    v1 keeps its numbers (superseded, never served)."""
    versions = _buy_time_evidence()
    assert ar.BUY_TIME_EXPECTATION_VERSION in versions
    for name, entry in versions.items():
        assert name.startswith("buy-time-v"), name
        assert set(entry["values"]) == set(ar.RULE_IDS), name
        if entry["verified"]:
            assert entry["verification"], name
    v1 = versions["buy-time-v1"]
    assert v1["values"] == _BUY_TIME_V1
    assert v1["verified"] is False
    assert v1["verification"].startswith("superseded by buy-time-v2")


def test_buy_time_v1_numbers_are_no_longer_served():
    assert ar.BUY_TIME_EXPECTATION_VERSION != "buy-time-v1"
    v1_numbers = {v for triple in _BUY_TIME_V1.values() for v in triple if v is not None}
    for rid in ar.RULE_IDS:
        served = ar.buy_time_expectation(rid)
        assert served is not None, rid
        shown = {served.range_low, served.range_high, served.ci_low, served.ci_high} - {None}
        assert not (shown & v1_numbers), rid


def test_buy_time_expectation_is_served_once_verified(monkeypatch):
    """D6: v2 passed the independent verification — served for every rule, as registered."""
    assert ar.BUY_TIME_EXPECTATION_VERIFIED is True
    assert ar.BUY_TIME_EXPECTATION_SOURCE["status"].startswith("verified")
    assert all(ar.buy_time_expectation(rid) is ar.BUY_TIME_EXPECTATION[rid]
               for rid in ar.RULE_IDS)
    with pytest.raises(ValueError):
        ar.buy_time_expectation("S9")
    # unverified -> nothing is served, for every rule
    monkeypatch.setattr(ar, "BUY_TIME_EXPECTATION_VERIFIED", False)
    assert all(ar.buy_time_expectation(rid) is None for rid in ar.RULE_IDS)
    # the flag alone, without a verified status, is refused (the two never disagree)
    monkeypatch.setattr(ar, "BUY_TIME_EXPECTATION_VERIFIED", True)
    monkeypatch.setitem(ar.BUY_TIME_EXPECTATION_SOURCE, "status", "provisional")
    with pytest.raises(ValueError):
        ar.buy_time_expectation("S1")
