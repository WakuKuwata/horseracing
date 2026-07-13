"""Feature 068 US2 driver (T026/T027): A/B/C/D calibration-split comparison.

The four arms share feature_version / objective / seed and differ ONLY in the calibration split
(FR-010):

- A: 70/30 isotonic (current) — train-internal calib holdout
- B: 90/10 isotonic — smaller holdout
- C: full-history refit + OOF temperature
- D: full-history refit + OOF race-normalized power

For a softmax objective temperature and power are the same family (T020), so C and D collapse to
one OOF-power arm; the driver labels it "C/D" and runs it once.

Two-phase, on DISJOINT windows so a screening fold never re-enters the final CI (FR-014, III):
1. SCREENING — each candidate arm vs the A reference on the screening window; go/no-go by the
   pre-registered screening rule (non-inferior winner-NLL, or NO_DECISION when the CI straddles
   zero). This is where arms are selected.
2. CONFIRMATION — only arms that passed screening are paired vs A on a separate confirmation
   window, producing the reported CI and gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from horseracing_eval.dataset import load_eval_races
from horseracing_eval.paired import PairedReport, paired_eval
from sqlalchemy.orm import Session


@dataclass
class ArmSpec:
    name: str
    spec: str  # recipe spec understood by _factory_from_spec (e.g. "pl_topk:isotonic:0.3")


def default_arms(objective: str) -> list[ArmSpec]:
    return [
        ArmSpec("A", f"{objective}:isotonic:0.3"),
        ArmSpec("B", f"{objective}:isotonic:0.1"),
        ArmSpec("C/D", f"{objective}:oof_power"),
    ]


@dataclass
class ArmResult:
    name: str
    spec: str
    screen_diff: float | None = None
    screen_ci: dict | None = None
    go: bool = False
    go_reason: str = ""
    confirm: PairedReport | None = None


@dataclass
class CalibSplitReport:
    reference: str
    objective: str
    seed: int
    screen_window: tuple
    confirm_window: tuple
    arms: list[ArmResult] = field(default_factory=list)


def _screen_decision(rep: PairedReport, margin: float) -> tuple[bool, str]:
    """Pre-registered go/no-go (gate-config screening): promote a candidate to confirmation when
    it is NON-INFERIOR to A (winner-NLL diff <= margin) or the screening CI straddles zero
    (NO_DECISION — worth confirming). A candidate that is clearly worse (CI lower bound > margin)
    is dropped."""
    diff = rep.periods["all"]["diff"]
    ci = rep.bootstrap_ci
    if ci.get("no_decision"):
        return True, "no_decision_ci (promote to confirm)"
    lo, hi = ci.get("ci_low"), ci.get("ci_high")
    if lo is not None and lo > margin:
        return False, f"clearly worse (CI low {lo:.4f} > margin {margin})"
    if diff <= margin:
        return True, f"non-inferior (diff {diff:+.4f} <= margin {margin})"
    if lo is not None and hi is not None and lo <= 0.0 <= hi:
        return True, "CI straddles zero (promote to confirm)"
    return False, f"worse (diff {diff:+.4f} > margin, CI [{lo},{hi}])"


def run_calib_split_eval(
    session: Session,
    *,
    make_factory,
    objective: str,
    screen_window: tuple,
    confirm_window: tuple,
    gate_config: dict | None = None,
    seed: int = 20260712,
    bootstrap_b: int = 1000,
    num_threads: int | None = None,
) -> CalibSplitReport:
    """Run A/B/C/D screening then confirmation. ``make_factory(spec)`` builds a PredictorFactory
    (injected so this module stays eval-boundary clean via the CLI)."""
    arms = default_arms(objective)
    ref = arms[0]
    screen_margin = (gate_config or {}).get("screening", {}).get("margin", 0.0)

    screen_races = load_eval_races(
        session, start_date=screen_window[0], end_date=screen_window[1]
    )
    confirm_races = load_eval_races(
        session, start_date=confirm_window[0], end_date=confirm_window[1]
    )
    # Each window validates only its FINAL year (train on everything earlier in the window), so
    # screening and confirmation validation sets are DISJOINT by end-year — a screening fold never
    # re-enters the confirmation CI (FR-014, III). Give windows with different end years.
    screen_fvy = screen_window[1].year
    confirm_fvy = confirm_window[1].year

    results: list[ArmResult] = [ArmResult(ref.name, ref.spec, go=True, go_reason="reference")]
    for arm in arms[1:]:
        # Phase 1: screening (candidate vs A) on the screening window only.
        srep = paired_eval(
            make_factory(arm.spec), make_factory(ref.spec), screen_races,
            gate_config=gate_config, first_valid_year=screen_fvy,
            bootstrap_seed=seed, bootstrap_b=bootstrap_b, num_threads=num_threads,
        )
        go, reason = _screen_decision(srep, screen_margin)
        r = ArmResult(
            arm.name, arm.spec,
            screen_diff=srep.periods["all"]["diff"], screen_ci=srep.bootstrap_ci,
            go=go, go_reason=reason,
        )
        # Phase 2: confirmation on the DISJOINT window (only if screening said go).
        if go:
            r.confirm = paired_eval(
                make_factory(arm.spec), make_factory(ref.spec), confirm_races,
                gate_config=gate_config, first_valid_year=confirm_fvy,
                bootstrap_seed=seed, bootstrap_b=bootstrap_b, num_threads=num_threads,
            )
        results.append(r)

    return CalibSplitReport(
        reference=ref.name, objective=objective, seed=seed,
        screen_window=screen_window, confirm_window=confirm_window, arms=results,
    )
