"""Feature 138 (T026): pure assembly of the 注目条件 responses (no DB).

Covers the race branches (not_computed / odds_unavailable / available, scan with zero judged
horses), the chip and its stage (failed-only horses keep a chip, S2 as main chip has no sub-chip,
S5 never), the display sources (``judged`` = frozen pick, ``current`` = displayed version's latest
row, single seed only from the same run), ``chip_now``, the levels, the read-time tally (exclusive
classification, Σ reconciliation, stage from the records), the memo and the single definition of
the displayed model version.
"""

from __future__ import annotations

import ast
import datetime
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_eval import attention_rules as ar

from horseracing_api import attention
from horseracing_api.attention import (
    TallyMemo,
    build_attention,
    build_day_items,
    build_rule_tallies,
    build_rules_response,
)
from horseracing_api.schemas import (
    AttentionAvailable,
    AttentionExclusionCounts,
    AttentionUnavailable,
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "horseracing_api"
_UTC = datetime.UTC
_RACE = "202610030511"
_START = datetime.date(2026, 10, 1)
_COMPUTED = datetime.datetime(2026, 10, 3, 0, 0, tzinfo=_UTC)    # JST 09:00
_POST = datetime.datetime(2026, 10, 3, 6, 0, tzinfo=_UTC)
_OBSERVED = datetime.datetime(2026, 10, 2, 23, 55, tzinfo=_UTC)
_RUN = uuid.UUID("11111111-1111-1111-1111-111111111111")


# --- builders ------------------------------------------------------------------------------------


def _entry(n, *, odds=30.0, status=EntryStatus.STARTED, hid=None):
    return SimpleNamespace(
        horse_id=hid or f"H{n}", horse_number=n, entry_status=status,
        odds=None if odds is None else Decimal(str(odds)), horse_name=f"name{n}",
        race_id=_RACE,
    )


def _field(n=6, **odds_over):
    return [_entry(i, odds=odds_over.get(f"H{i}", 30.0)) for i in range(1, n + 1)]


def _digest(entries):
    return ar.field_digest(e.horse_id for e in entries if e.entry_status == EntryStatus.STARTED)


def _pick(hid, rule_id, *, n, ens=1.35, single=1.25, odds=30.0, gap=40, digest, kind="pick",
          voids=None, pick_id=None):
    return SimpleNamespace(
        pick_id=pick_id or uuid.uuid4(), race_id=_RACE, horse_id=hid, horse_number=n,
        rule_id=rule_id, kind=kind, voids_pick_id=voids,
        ens_expected_return=Decimal(str(ens)), single_expected_return=Decimal(str(single)),
        odds_used=Decimal(str(odds)), odds_observed_at=_OBSERVED,
        days_since_last=Decimal(str(gap)) if gap is not None else None, field_digest=digest,
        run_id=_RUN, computed_at=_COMPUTED,
    )


def _picks_for(hid, n, rules, *, digest, **kw):
    return [_pick(hid, r, n=n, digest=digest, **kw) for r in rules]


def _void(target):
    return SimpleNamespace(
        pick_id=uuid.uuid4(), race_id=_RACE, horse_id=target.horse_id, horse_number=None,
        rule_id=target.rule_id, kind="void", voids_pick_id=target.pick_id,
        ens_expected_return=None, single_expected_return=None, odds_used=None,
        odds_observed_at=None, days_since_last=None, field_digest=target.field_digest,
        run_id=_RUN, computed_at=_COMPUTED,
    )


def _ev_rows(entries, *, ev=1.0, overrides=None, run_id=_RUN, computed_at=_COMPUTED,
             observed=_OBSERVED):
    overrides = overrides or {}
    rows = []
    for e in entries:
        if e.entry_status != EntryStatus.STARTED:
            continue
        o = overrides.get(e.horse_id, {})
        odds = Decimal(str(o.get("odds", e.odds)))
        rows.append(SimpleNamespace(
            race_id=_RACE, horse_id=e.horse_id, horse_number=e.horse_number,
            model_version="m", expected_return=Decimal(str(o.get("ev", ev))), odds_used=odds,
            odds_observed_at=observed, result_pending_at_compute=True, logic_version="lv",
            computed_at=computed_at, run_id=run_id,
        ))
    return rows


def _scan():
    return SimpleNamespace(race_id=_RACE, computed_at=_COMPUTED)


def _stage(stage="researching", checkpoint=None, pending=False):
    from horseracing_api.schemas import StageDetail

    return StageDetail(stage=stage, checkpoint=checkpoint, checkpoint_pending=pending)


def _tallies(**stages):
    """RuleTally for every rule (fresh, no picks) with optional stage overrides."""
    base = build_rule_tallies(ar.RULE_IDS, [], {}, [])
    out = {}
    for rule_id, t in base.items():
        if rule_id in stages:
            st = stages[rule_id]
            out[rule_id] = attention.RuleTally(
                rule_id=rule_id, stage=st, point_roi_frozen=t.point_roi_frozen,
                prospective=t.prospective.model_copy(update={"stage": st.stage}),
            )
        else:
            out[rule_id] = t
    return out


def _build(entries, picks, *, ens=None, single=None, scan=True, tallies=None, post=_POST,
           has_results=False):
    return build_attention(
        race_id=_RACE, post_time=post, has_results=has_results,
        scan=_scan() if scan else None, picks=picks, entries=entries,
        ens_rows=_ev_rows(entries) if ens is None else ens,
        single_rows=_ev_rows(entries) if single is None else single,
        tallies=tallies or _tallies(),
    )


def _horse(resp, hid):
    return next(h for h in resp.horses if h.horse_id == hid)


# --- race branches -------------------------------------------------------------------------------


def test_no_scan_is_not_computed():
    resp = _build(_field(), [], scan=False)
    assert isinstance(resp, AttentionUnavailable)
    assert resp.reason == "not_computed"


def test_no_scan_with_a_started_horse_without_odds_is_odds_unavailable():
    resp = _build(_field(H2=None), [], scan=False, ens=[], single=[])
    assert resp.reason == "odds_unavailable"


@pytest.mark.parametrize(
    ("entries", "reason"),
    [(_field(), "not_computed"), (_field(H2=None), "odds_unavailable")],
)
def test_uncomputed_race_reads_only_the_entries(monkeypatch, entries, reason):
    # Most future races have no first computation: the router answers them from the entries
    # alone — no tally (memo-key query / bootstrap recompute), picks or market-ev reads.
    from horseracing_api.routers import attention as router

    def must_not_load(*_args, **_kwargs):
        raise AssertionError("loaded for a race without a first computation")

    monkeypatch.setattr(router, "get_race",
                        lambda _s, rid: SimpleNamespace(race_id=rid, post_time=_POST))
    monkeypatch.setattr(router, "attention_scan_for_race", lambda _s, _rid: None)
    monkeypatch.setattr(router, "entries_for_races", lambda _s, _ids: entries)
    for name in ("rule_tallies", "attention_memo_key", "attention_tally_rows",
                 "attention_picks_for_race", "market_ev_rows", "race_has_results"):
        monkeypatch.setattr(router, name, must_not_load)
    monkeypatch.setattr(attention.TALLY_MEMO, "get", must_not_load)
    resp = router.race_attention(_RACE, session=object())
    assert isinstance(resp, AttentionUnavailable)
    assert (resp.race_id, resp.reason) == (_RACE, reason)


def test_scan_with_zero_judged_horses_is_available_with_empty_applicable():
    entries = _field()
    resp = _build(entries, [])
    assert isinstance(resp, AttentionAvailable)
    assert resp.judged_at == _COMPUTED
    assert resp.rule_set_version == ar.RULE_SET_VERSION
    assert resp.selection_policy_version == ar.SELECTION_POLICY_VERSION
    assert [h.horse_number for h in resp.horses] == [1, 2, 3, 4, 5, 6]
    for h in resp.horses:
        assert h.applicable == [] and h.chip_rule is None and h.chip_now is None
        assert h.judged is None and h.levels is None and h.stages == {}
        assert set(h.pick_status.values()) == {"none"}


# --- chip and stages -----------------------------------------------------------------------------


def test_s1_horse_lists_inclusion_and_one_chip():
    entries = _field()
    d = _digest(entries)
    picks = _picks_for("H3", 3, ("S1", "S3", "S4", "S5"), digest=d, ens=1.25)
    h = _horse(_build(entries, picks), "H3")
    assert h.applicable == ["S1", "S3", "S4", "S5"]
    assert h.chip_rule == "S1" and h.chip_s2 is False
    assert h.chip_stage.stage == "researching"
    assert set(h.stages) == {"S1", "S3", "S4", "S5"}
    assert h.pick_status == {"S1": "pick", "S2": "none", "S3": "pick", "S4": "pick", "S5": "pick"}


def test_s2_horse_gets_s1_chip_with_s2_sub_chip():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S2", "S3", "S4"), digest=_digest(entries))
    h = _horse(_build(entries, picks), "H3")
    assert h.chip_rule == "S1" and h.chip_s2 is True


def test_failed_s1_moves_chip_to_s3():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries))
    tallies = _tallies(S1=_stage("failed", 300), S3=_stage("observing"))
    h = _horse(_build(entries, picks, tallies=tallies), "H3")
    assert h.chip_rule == "S3" and h.chip_stage.stage == "observing"
    assert h.stages["S1"].stage == "failed" and h.stages["S1"].checkpoint == 300


def test_failed_s1_with_live_s2_makes_s2_the_main_chip_without_sub_chip():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S2", "S3", "S4"), digest=_digest(entries))
    tallies = _tallies(S1=_stage("failed", 300), S2=_stage("observing"))
    h = _horse(_build(entries, picks, tallies=tallies), "H3")
    assert h.chip_rule == "S2" and h.chip_s2 is False


def test_all_failed_horse_keeps_its_chip_with_the_stage():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries))
    tallies = _tallies(S1=_stage("failed", 300), S3=_stage("undecided", 600),
                       S4=_stage("failed", 300))
    h = _horse(_build(entries, picks, tallies=tallies), "H3")
    assert h.chip_rule == "S1"
    assert h.chip_stage.stage == "failed" and h.chip_stage.checkpoint == 300
    assert h.levels.prospective == 1


def test_s5_only_horse_has_no_chip():
    entries = _field()
    picks = _picks_for("H2", 2, ("S5",), digest=_digest(entries), ens=1.0, single=1.3, odds=3.0)
    h = _horse(_build(entries, picks), "H2")
    assert h.applicable == ["S5"]
    assert h.chip_rule is None and h.chip_now is None and h.levels is None
    assert h.judged is not None and h.judged.single_expected_return == 1.3


def test_voided_picks_leave_no_chip_and_keep_the_scratched_horse_listed():
    entries = _field()
    d = _digest(entries)
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=d)
    picks += [_void(p) for p in picks]
    scratched = [e if e.horse_id != "H3" else _entry(3, status=EntryStatus.CANCELLED)
                 for e in entries]
    resp = _build(scratched, picks, ens=_ev_rows(scratched), single=_ev_rows(scratched))
    h = _horse(resp, "H3")
    assert h.applicable == [] and h.chip_rule is None
    assert h.pick_status["S1"] == "void:scratched" and h.pick_status["S2"] == "none"
    assert h.field_changed_after_pick is True


# --- display sources -----------------------------------------------------------------------------


def test_judged_comes_from_the_pick_and_current_from_the_displayed_latest_row():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries), ens=1.31,
                       single=1.284, odds=32.5)
    later = datetime.datetime(2026, 10, 3, 5, 30, tzinfo=_UTC)
    new_run = uuid.uuid4()
    ens = _ev_rows(entries, run_id=new_run, computed_at=later, observed=later,
                   overrides={"H3": {"ev": 1.246, "odds": 30.8}})
    single = _ev_rows(entries, ev=1.26, run_id=new_run, computed_at=later, observed=later)
    h = _horse(_build(entries, picks, ens=ens, single=single), "H3")
    assert h.judged.ens_expected_return == 1.31 and h.judged.odds == 32.5
    assert h.judged.computed_at == _COMPUTED and h.judged.run_id == _RUN
    assert h.current.ens_expected_return == 1.246 and h.current.odds == 30.8
    assert h.current.single_expected_return == 1.26
    assert h.current.odds_observed_at == later and h.current.run_id == new_run
    assert h.judged.is_pseudo is True and h.current.is_pseudo is True
    assert h.chip_now == "matches"


def test_a_later_single_seed_only_recompute_does_not_move_current():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries))
    ens = _ev_rows(entries, overrides={"H3": {"ev": 1.3}})
    same = _horse(_build(entries, picks, ens=ens, single=_ev_rows(entries, ev=1.1)), "H3")
    later = datetime.datetime(2026, 10, 3, 5, 0, tzinfo=_UTC)
    newer_single = _ev_rows(entries, ev=0.5, run_id=uuid.uuid4(), computed_at=later,
                            observed=later, overrides={"H3": {"ev": 0.5, "odds": 45.0}})
    moved = _horse(_build(entries, picks, ens=ens, single=newer_single), "H3")
    assert same.current.single_expected_return == 1.1
    assert moved.current.single_expected_return is None
    assert moved.current.ens_expected_return == same.current.ens_expected_return
    assert moved.current.odds == same.current.odds == 30.0
    assert moved.current.odds_observed_at == same.current.odds_observed_at
    assert moved.current.computed_at == same.current.computed_at
    assert moved.chip_now == same.chip_now == "matches"


def test_chip_now_no_longer_when_the_current_odds_left_the_band():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries), odds=32.5)
    ens = _ev_rows(entries, overrides={"H3": {"ev": 1.3, "odds": 45.0}})
    h = _horse(_build(entries, picks, ens=ens), "H3")
    assert h.current.odds == 45.0
    assert h.chip_rule == "S1" and h.chip_now == "no_longer"


def test_field_change_makes_current_null_and_chip_now_unknown():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries))
    ens = _ev_rows(entries)
    changed = entries + [_entry(7)]
    h = _horse(_build(changed, picks, ens=ens, single=ens), "H3")
    assert h.current is None and h.chip_now == "unknown"
    assert h.field_changed_after_pick is True
    assert h.chip_rule == "S1"            # the chip itself stays (judged at the first compute)
    assert _horse(_build(changed, picks, ens=ens, single=ens), "H7").field_changed_after_pick \
        is False                          # no pick → nothing to flag


def test_missing_odds_after_the_compute_makes_current_null():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries))
    ens = _ev_rows(entries)
    resp = _build(_field(H5=None), picks, ens=ens, single=ens)
    assert isinstance(resp, AttentionAvailable)   # the scan exists: judged values still shown
    h = _horse(resp, "H3")
    assert h.current is None and h.chip_now == "unknown"
    assert h.field_changed_after_pick is False


def test_no_displayed_rows_gives_unknown_current():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries))
    h = _horse(_build(entries, picks, ens=[]), "H3")
    assert h.current is None and h.chip_now == "unknown"


def test_levels_follow_the_registry_for_the_chip_rule():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries))
    h = _horse(_build(entries, picks), "H3")
    s1 = ar.rule("S1")
    assert h.levels.backtest == ar.backtest_level(s1)
    assert h.levels.price_noise == ar.price_noise_level(s1)
    assert h.levels.prospective == 1        # researching at the start


# --- day items -----------------------------------------------------------------------------------


def test_day_items_are_post_ordered_with_unknown_post_time_last():
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries))
    picks += _picks_for("H1", 1, ("S3",), digest=_digest(entries), odds=3.0)
    picks += _picks_for("H2", 2, ("S5",), digest=_digest(entries), odds=3.0)
    resp = _build(entries, picks)
    early = SimpleNamespace(race_id="A", post_time=_POST - datetime.timedelta(hours=1),
                            venue_code="05", race_number=1)
    late = SimpleNamespace(race_id="B", post_time=_POST, venue_code="05", race_number=2)
    unknown = SimpleNamespace(race_id="C", post_time=None, venue_code="05", race_number=3)
    out = build_day_items(
        date=datetime.date(2026, 10, 3),
        races={"C": unknown, "B": late, "A": early},
        responses={"C": resp, "B": resp, "A": resp},
        horse_names={("A", "H3"): "name3"},
    )
    assert [(i.race_id, i.horse_number) for i in out.items] == [
        ("A", 1), ("A", 3), ("B", 1), ("B", 3), ("C", 1), ("C", 3),
    ]
    assert out.items[1].horse_name == "name3" and out.items[0].horse_name is None
    assert {i.chip_rule for i in out.items} == {"S1", "S3"}   # S5-only horse is not listed
    assert out.items[1].current_odds_observed_at == _OBSERVED


def test_day_items_leave_out_a_horse_scratched_after_its_pick():
    # spec Edge Case: a 取消 horse gets no chip, even before the next computation appends its void
    entries = _field()
    picks = _picks_for("H3", 3, ("S1", "S3", "S4"), digest=_digest(entries))
    picks += _picks_for("H1", 1, ("S3",), digest=_digest(entries), odds=3.0)
    resp = _build(entries, picks)
    race = SimpleNamespace(race_id="A", post_time=_POST, venue_code="05", race_number=1)
    out = build_day_items(
        date=datetime.date(2026, 10, 3), races={"A": race}, responses={"A": resp},
        horse_names={}, non_starters=frozenset({("A", "H3")}),
    )
    assert [i.horse_id for i in out.items] == ["H1"]


# --- read-time tally -----------------------------------------------------------------------------


def _row(rule_id="S3", *, race_id="202610030511", n=1, computed=_COMPUTED, post=_POST,
         observed=_OBSERVED, pending=True, voided=False, has_result=True, horse_result=True,
         finish=2, status=ResultStatus.FINISHED, n_winners=1, odds=10.0, stored=10.0,
         digest=None):
    return SimpleNamespace(
        pick_id=uuid.uuid4(), race_id=race_id, horse_number=n, rule_id=rule_id,
        computed_at=computed, post_time=post, odds_observed_at=observed,
        result_pending_at_compute=pending, odds_used=Decimal(str(odds)),
        field_digest=digest or ar.field_digest(["X1", "X2"]), voided=voided,
        has_race_result=has_result, horse_has_result=horse_result,
        finish_order=finish if horse_result else None,
        result_status=status if horse_result else None,
        n_winners=n_winners, stored_odds=None if stored is None else Decimal(str(stored)),
    )


def _one_of_each_class():
    early = datetime.datetime(2026, 9, 29, 0, 0, tzinfo=_UTC)
    return [
        _row(voided=True),                                      # voided_scratched
        _row(computed=early),                                   # before_start
        _row(post=None),                                        # post_time_unknown
        _row(computed=_POST),                                   # computed_after_post
        _row(pending=False),                                    # result_known_at_compute
        _row(observed=_POST),                                   # observed_after_post
        _row(has_result=False, horse_result=False, n_winners=0),  # pending_result
        _row(horse_result=False),                               # unsettled_horse
        _row(n_winners=2, finish=1),                            # dead_heat
        _row(finish=1, odds=12.0, stored=11.0),                 # counted (won)
        _row(finish=3),                                         # counted (lost)
    ]


def test_tally_classes_are_exclusive_and_reconcile(monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", _START)
    rows = _one_of_each_class()
    started = {"202610030511": ["X1", "X2", "X3"]}      # field changed → every live pick flagged
    t = build_rule_tallies(["S3"], rows, started, [])["S3"]
    p = t.prospective
    assert p.counts.model_dump() == dict.fromkeys(ar.EXCLUSION_ORDER, 1)
    assert p.n_counted == 2 and p.n_hits == 1
    assert p.n_counted + sum(p.counts.model_dump().values()) == p.n_picks_total == len(rows)
    assert p.flags.field_changed_after_pick == len(rows) - 1          # not part of the Σ
    assert p.frozen.roi == pytest.approx(12.0 * 100 / 200)
    assert p.stored.roi == pytest.approx(11.0 * 100 / 200)
    assert (p.stored.n, p.stored.n_missing_stored_odds) == (2, 0)
    assert p.frozen.valuation_basis == "frozen_pick_odds"
    assert p.stored.valuation_basis == "stored_odds_mutable"
    assert p.start_date == _START and p.policy_version == ar.SELECTION_POLICY_VERSION
    assert p.stage == "researching" and p.next_checkpoint == 300
    assert p.remaining_to_next == 298
    assert p.bootstrap.b == ar.BOOTSTRAP["b"] and p.bootstrap.seed == ar.BOOTSTRAP["seed"]
    assert p.by_judged_freshness.gt_60m.n == 2
    assert p.odds_drift.n == 2


def test_stored_basis_leaves_out_picks_without_stored_odds(monkeypatch):
    # A counted winner whose stored odds disappeared (re-ingested as null / 067 re-key) is never
    # settled at its judged odds under the ``stored_odds_mutable`` label: it leaves numerator and
    # denominator of the stored basis alike and shows up as a count. The frozen basis keeps it.
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", _START)
    rows = [
        _row(finish=1, odds=12.0, stored=None),       # winner, stored odds gone
        _row(finish=1, odds=8.0, stored=9.0),         # winner, stored odds present
        _row(finish=3, odds=10.0, stored=None),       # loser, stored odds gone
        _row(finish=2, odds=10.0, stored=0.5),        # loser, invalid stored odds (< 1.0)
        _row(finish=2, odds=10.0, stored=10.0),       # loser, stored odds present
    ]
    p = build_rule_tallies(["S3"], rows, {}, [])["S3"].prospective
    assert p.n_counted == 5 and p.n_hits == 2
    assert p.frozen.roi == pytest.approx((12.0 + 8.0) * 100 / 500)
    assert (p.stored.n, p.stored.n_missing_stored_odds) == (2, 3)
    assert p.stored.n + p.stored.n_missing_stored_odds == p.n_counted
    assert p.stored.roi == pytest.approx(9.0 * 100 / 200)        # not (12 + 9) / 5 or 21 / 2
    assert p.odds_drift.n == 2

    gone = [_row(finish=1, odds=12.0, stored=None), _row(finish=4, stored=None)]
    p = build_rule_tallies(["S3"], gone, {}, [])["S3"].prospective
    assert p.n_counted == 2 and p.frozen.roi == pytest.approx(6.0)
    assert (p.stored.n, p.stored.n_missing_stored_odds) == (0, 2)
    assert p.stored.roi is None and p.stored.ci is None
    assert p.odds_drift.n == 0


def test_tally_before_go_live_counts_everything_as_before_start(monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", None)
    rows = [_row(), _row(finish=1), _row(voided=True)]
    p = build_rule_tallies(["S3"], rows, {}, [])["S3"].prospective
    assert p.n_counted == 0 and p.counts.before_start == 2 and p.counts.voided_scratched == 1
    assert p.stage == "researching" and p.frozen.roi is None and p.frozen.ci is None


def test_exclusion_count_fields_are_the_registry_classes():
    assert tuple(AttentionExclusionCounts.model_fields) == ar.EXCLUSION_ORDER


def _record(checkpoint=300, decision="failed", start=_START, rule_id="S3"):
    return SimpleNamespace(
        rule_id=rule_id, checkpoint=checkpoint, decision=decision, n_counted=checkpoint,
        n_hits=10, roi_frozen=Decimal("0.8"), ci_low=Decimal("0.6"), ci_high=Decimal("0.95"),
        bootstrap={"impl": "x", "b": 20000, "seed": 20260905, "block_universe": "u"},
        counted_pick_ids_sha256="a" * 64, settlement_cutoff=_COMPUTED,
        prospective_start_date=start, skipped_pending_before_last=0, decided_at=_COMPUTED,
    )


def test_stage_follows_the_record_not_the_live_points(monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", _START)
    winners = [_row(finish=1, odds=30.0) for _ in range(5)]      # live ROI far above 100%
    t = build_rule_tallies(["S3"], winners, {}, [_record()])["S3"]
    assert t.stage.stage == "failed" and t.stage.checkpoint == 300
    assert t.stage.checkpoint_pending is False
    assert [d.decision for d in t.prospective.decisions] == ["failed"]
    assert t.prospective.decisions[0].ci == (0.6, 0.95)
    assert t.prospective.next_checkpoint is None and t.prospective.remaining_to_next is None


def test_record_under_another_start_date_is_not_used_and_pending(monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", _START)
    stale = _record(decision="passed", start=datetime.date(2026, 9, 1))
    t = build_rule_tallies(["S3"], [_row()], {}, [stale])["S3"]
    assert t.stage.stage == "researching" and t.stage.checkpoint is None
    assert t.stage.checkpoint_pending is True
    assert t.prospective.decisions[0].prospective_start_date == datetime.date(2026, 9, 1)


def test_continue_then_600_record(monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", _START)
    records = [_record(300, "continue"), _record(600, "undecided")]
    t = build_rule_tallies(["S3"], [_row()], {}, records)["S3"]
    assert t.stage.stage == "undecided" and t.stage.checkpoint == 600


def test_count_reaching_300_without_a_record_is_pending(monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", _START)
    rows = [_row(race_id=f"20261003{i:04d}") for i in range(300)]
    t = build_rule_tallies(["S3"], rows, {}, [])["S3"]
    assert t.stage.stage == "observing" and t.stage.checkpoint_pending is True
    assert t.prospective.remaining_to_next == 0


def test_rules_response_is_rank_ordered_with_frozen_values():
    resp = build_rules_response(_tallies())
    assert [r.id for r in resp.items] == ["S1", "S2", "S3", "S4", "S5"]
    s1 = resp.items[0]
    assert s1.backtest.all.roi == ar.rule("S1").backtest_all.roi
    assert s1.backtest.valuation_basis == "closing_odds_approx"
    assert s1.posthoc is True and resp.items[4].control is True
    assert s1.levels.backtest == ar.backtest_level(ar.rule("S1"))
    assert [n.sigma for n in s1.price_noise] == [0.1, 0.2, 0.3]
    assert "win_prob" not in resp.model_dump_json() and "p_hat" not in resp.model_dump_json()


def test_backtest_bootstrap_is_the_registry_freeze_provenance():
    # plan 0.5: ``backtest.bootstrap`` carries the provenance of the frozen CIs/p, taken from the
    # registry (never restated in the api, never borrowed from the prospective ``ar.BOOTSTRAP``).
    resp = build_rules_response(_tallies())
    frozen = ar.FROZEN_BOOTSTRAP
    expected = {k: frozen[k] for k in ("impl", "b", "seed", "block", "block_universe")}
    for item in resp.items:
        assert item.backtest.bootstrap is not None
        assert item.backtest.bootstrap.model_dump() == expected


# --- memo ----------------------------------------------------------------------------------------


def test_memo_recomputes_only_rules_whose_key_moved():
    memo = TallyMemo()
    loads: list[list[str]] = []
    base = _tallies()

    def load(rule_ids):
        loads.append(list(rule_ids))
        return {r: base[r] for r in rule_ids}

    keys = {r: (r, 0) for r in ar.RULE_IDS}
    memo.get(keys, load)
    memo.get(dict(keys), load)
    memo.get({**keys, "S3": ("S3", 1)}, load)
    assert loads == [list(ar.RULE_IDS), ["S3"]]
    memo.clear()
    memo.get(keys, load)
    assert len(loads) == 3


# --- single definitions / boundary ---------------------------------------------------------------


def test_displayed_model_version_is_not_redeclared_in_the_api():
    for f in _SRC.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    assert not (isinstance(t, ast.Name) and t.id in {
                        "DISPLAYED_MARKET_EV_MODEL_VERSION", "SINGLE_SEED_MODEL_VERSION",
                        "RULE_SET_VERSION", "PROSPECTIVE_START_DATE",
                    }), f"{f.name}: re-declares {t.id}"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in {
                    ar.DISPLAYED_MARKET_EV_MODEL_VERSION, ar.SINGLE_SEED_MODEL_VERSION,
                    ar.RULE_SET_VERSION,
                }, f"{f.name}: hard-codes {node.value!r}"


def test_market_ev_router_uses_the_registry_constant():
    from horseracing_api import market_ev

    assert market_ev.DISPLAYED_MARKET_EV_MODEL_VERSION is ar.DISPLAYED_MARKET_EV_MODEL_VERSION


def test_prospective_start_date_is_never_copied_at_import():
    # read at call time (``attention_rules.PROSPECTIVE_START_DATE``) so a go-live constant is
    # honoured; a ``from ... import PROSPECTIVE_START_DATE`` would freeze it at import time.
    for f in _SRC.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "PROSPECTIVE_START_DATE" not in {a.name for a in node.names}, f.name


def test_new_modules_are_inside_the_no_write_scan():
    scanned = {p.relative_to(_SRC).as_posix() for p in _SRC.rglob("*.py")}
    assert {"attention.py", "routers/attention.py"} <= scanned
