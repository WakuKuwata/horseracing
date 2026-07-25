"""Regression tests for the 2026-07-25 multi-codex evaluation-correctness findings.

C#1 (training-history truncation) is CLI-side and covered by the folklore-probe guard test here
plus the paired-eval handler change (full-history load). C#2 = partial-ingest fail-closed.
C#3 = ADOPT unreachable when declared critical subgroups were not computed.
"""

from __future__ import annotations

import datetime

from horseracing_eval.dataset import EvalRace, ScoringLabel, population_masks
from horseracing_eval.decision import NO_DECISION, REJECT, final_decision
from horseracing_eval.predictor import HorseEntry, RaceContext


def _race(n_started: int, winner: int = 0, n_result_rows=None, n_finished: int | None = None):
    horses = tuple(HorseEntry(horse_id=f"h{i}") for i in range(n_started))
    n_fin = n_started if n_finished is None else n_finished
    labels = tuple(
        ScoringLabel(horse_id=f"h{i}", win=int(i == winner), top2=int(i <= 1), top3=int(i <= 2))
        for i in range(n_fin)
    )
    return EvalRace(
        context=RaceContext(
            race_id="202401010101", race_date=datetime.date(2024, 1, 1), started_horses=horses
        ),
        labels=labels,
        n_result_rows=n_result_rows,
    )


class TestPartialIngestFailClosed:
    """codex C#2: winner-row-only ingest must NOT count as an eligible race."""

    def test_partial_ingest_is_ineligible(self):
        # 4 started, winner + 2nd finished, but only 2 result rows exist (2 horses have NO row)
        er = _race(4, winner=0, n_result_rows=2, n_finished=2)
        pop = population_masks(er)
        assert pop.eligible is False
        assert pop.winner_horse_id is None

    def test_full_coverage_with_dnf_is_eligible(self):
        # 4 started, 3 finished + 1 stopped: 4 result rows exist -> DNF's 0-label is verifiable
        er = _race(4, winner=0, n_result_rows=4, n_finished=3)
        pop = population_masks(er)
        assert pop.eligible is True
        assert pop.winner_horse_id == "h0"

    def test_legacy_none_coverage_keeps_old_behaviour(self):
        # constructors that don't know coverage (tests / legacy) -> unchanged semantics
        er = _race(4, winner=0, n_result_rows=None, n_finished=2)
        pop = population_masks(er)
        assert pop.eligible is True


class _Gate:
    """Minimal GateResult stand-in (duck-typed by final_decision)."""

    def __init__(self, adopted=True, primary=True, stat_guard=True, recent_guard=True,
                 top_noninferior=True, calibration=True):
        self.adopted = adopted
        self.primary = primary
        self.stat_guard = stat_guard
        self.recent_guard = recent_guard
        self.top_noninferior = top_noninferior
        self.calibration = calibration
        self.reasons = {}


_CFG = {
    "eval_window": {"min_eval_days": 10},
    "subgroup_guard": {"critical_subgroups": ["2026_only", "nk", "2026_nk"]},
}


class TestAdoptRequiresComputedSubgroups:
    """codex C#3: config-declared critical subgroups can never be silently skipped into ADOPT."""

    def test_adopt_blocked_when_subgroups_not_computed(self):
        decision, reason = final_decision(_Gate(adopted=True), None, n_days=700, cfg=_CFG)
        assert decision == NO_DECISION
        assert reason["cause"] == "critical_subgroups_not_computed"

    def test_hard_failure_still_rejects_without_subgroups(self):
        gate = _Gate(adopted=False, primary=False)
        decision, _ = final_decision(gate, None, n_days=700, cfg=_CFG)
        assert decision == REJECT

    def test_adopt_allowed_when_no_critical_declared(self):
        cfg = {"eval_window": {"min_eval_days": 10}, "subgroup_guard": {"critical_subgroups": []}}
        decision, _ = final_decision(_Gate(adopted=True), None, n_days=700, cfg=cfg)
        assert decision == "ADOPT"

    def test_adopt_allowed_when_subgroups_computed_and_pass(self):
        sg = {"subgroup_decisions": {"2026_only": "PASS", "nk": "PASS", "2026_nk": "PASS"}}
        decision, _ = final_decision(_Gate(adopted=True), sg, n_days=700, cfg=_CFG)
        assert decision == "ADOPT"
