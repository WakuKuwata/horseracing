"""Feature 138: attention-condition registry (T004a definitions, T007 rules and levels)."""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import hashlib
import itertools
import random
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
                won=False, dead_heat=False, odds_used=25.0, stored_odds=24.0)
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
        "unsettled_horse": dict(horse_has_result=False),
        "dead_heat": dict(dead_heat=True, won=True),
    }
    for expected, kw in cases.items():
        assert ar.classify_pick(_facts(**kw), start_date=_START) == expected, expected
    assert ar.classify_pick(_facts(odds_observed_at=None), start_date=_START) == "observed_after_post"


def test_classification_is_exclusive_and_follows_precedence():
    flags = {
        "voided_scratched": dict(voided=True),
        "before_start": dict(computed_at=_ts(-30, 1)),
        "post_time_unknown": dict(post_time=None),
        "computed_after_post": dict(computed_at=_ts(0, 9)),
        "result_known_at_compute": dict(result_pending_at_compute=False),
        "observed_after_post": dict(odds_observed_at=_ts(0, 8)),
        "pending_result": dict(has_race_result=False),
        "unsettled_horse": dict(horse_has_result=False),
        "dead_heat": dict(dead_heat=True),
    }
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


def _picks(n: int, *, win_every: int, odds: float, per_day: int = 7, prefix: str = "p"):
    out = []
    for i in range(n):
        day, slot = divmod(i, per_day)
        out.append(_facts(pick_id=f"{prefix}{i:04d}", race_id=f"2026{day:04d}{slot:04d}",
                          horse_number=1 + slot, post_time=_ts(day, 3 + slot),
                          won=(i % win_every == 0), odds_used=odds))
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
    # set once at go-live (T045); moving it requires a new SELECTION_POLICY_VERSION
    assert ar.PROSPECTIVE_START_DATE == dt.date(2026, 10, 2)
    assert ar.SELECTION_POLICY_VERSION == "v1"
