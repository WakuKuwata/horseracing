"""Single implementation of the adoption gate (evaluation contract v3).

Before v3 the gate existed TWICE: ``paired._build_gate`` (068/069/070/088/090) and
``regime_paired``'s inline ``sub_gates`` (091). They disagreed — the regime path had no
recent-window guard at all, and the standard path had no ``min_effect_delta`` — so two runs
both labelled "adoption gate" applied different conditions. This module is the one definition
both call.

Two substantive v3 changes live here:

``min_effect_delta`` (was 091-only) — ``ci_upper < 0`` alone lets a mechanism that does nothing pass
on a hair's-width effect, so the point estimate must clear a pre-registered minimum.

``recent_guard`` (was a point-estimate sign test) — v2 compared the recent-window point estimates
with zero tolerance (``math.isclose`` at winner-NLL scale ≈ 2 admits 2e-9, i.e. none). Under the
null that test fails ~60% of the time, and ``decision.final_decision`` maps its failure to REJECT,
so pure sampling noise in a 3-year window produced "the candidate is worse". v3 asks the question
the guard was written to ask — "is the candidate CONFIDENTLY worse in the recent window?" — with the
same race-day cluster bootstrap and an explicit margin. The old behaviour is still reachable, but
only by naming it (``mode="legacy_point_estimate"``), because reproducing a v1/v2 verdict is a
deliberate act.

Naming, because the label has to match the mechanism: the recent guard is an EVIDENCE-OF-HARM veto
(``ci_low > margin``), not a non-inferiority test (``ci_high < margin``). Passing it means "no
degradation beyond the margin was detected", never "the recent window is fine" (codex).

Known and accepted: two nested windows each get a one-sided harm test, so the false-FAIL rate is
above a single window's — bounded by 2×2.5% and much lower in practice because the windows are
strongly correlated. It is not corrected, because at the recorded precisions (window SE ≈ 0.0014
against a 0.005 margin) a false FAIL under a true zero effect is a ~3.5-sigma event. A simultaneous
(max-T) construction is the fix if that margin/SE ratio ever narrows.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field

from .bootstrap import race_day_cluster_bootstrap_ci_v1
from .subgroups import FAIL, INCONCLUSIVE_LOW_PRECISION, residual_risk, three_way

#: Winner-NLL non-inferiority margin for the recent-window guard when the gate-config is silent.
#: Same metric and same scale as the race-level subgroup margin, so they are not set independently.
DEFAULT_RECENT_MARGIN = 0.005
DEFAULT_RECENT_WINDOWS = (3, 5)
MODE_NON_INFERIORITY = "non_inferiority"
MODE_LEGACY_POINT = "legacy_point_estimate"


@dataclass(frozen=True)
class CoreGate:
    """Sub-gate booleans + the conjunction, with every input echoed for audit."""

    sub_gates: dict
    adopted: bool
    reasons: dict = field(default_factory=dict)

    # --- adapters onto the legacy 5-field GateResult shape (unchanged consumers) ------------
    @property
    def primary(self) -> bool:
        return self.sub_gates["effect_beats_delta"]

    @property
    def stat_guard(self) -> bool:
        return self.sub_gates["ci_upper_below_zero"]

    @property
    def recent(self) -> bool:
        return self.sub_gates["recent_no_evidence_of_harm"]

    #: alias so a CoreGate satisfies the same duck-type as ``paired.GateResult`` and can be fed
    #: straight to ``decision.final_decision`` — one verdict mapping for both evaluation paths.
    @property
    def recent_guard(self) -> bool:
        return self.recent

    @property
    def top_noninferior(self) -> bool:
        return self.sub_gates["top2_noninferior"] and self.sub_gates["top3_noninferior"]

    @property
    def calibration(self) -> bool:
        return (
            self.sub_gates["calibration_noninferior"]
            and self.sub_gates["calibration_not_emergency"]
        )


def recent_margin(cfg: dict) -> float:
    rg = (cfg or {}).get("recent_guard", {}) or {}
    if "non_inferior_margin" in rg:
        return float(rg["non_inferior_margin"])
    sg = (cfg or {}).get("subgroup_guard", {}) or {}
    return float(sg.get("non_inferior_margin_winner_nll", DEFAULT_RECENT_MARGIN))


def recent_mode(cfg: dict) -> str:
    return str(((cfg or {}).get("recent_guard", {}) or {}).get("mode", MODE_NON_INFERIORITY))


def _window_start(max_date: datetime.date, years: int) -> datetime.date:
    """``max_date`` shifted back ``years``. Feb 29 has no counterpart in a common year, so it
    steps to Feb 28 rather than raising (v2 would have crashed on a leap-day window edge)."""
    try:
        return max_date.replace(year=max_date.year - years)
    except ValueError:
        return max_date.replace(year=max_date.year - years, day=max_date.day - 1)


def recent_window_guard(
    diffs_by_day: dict,
    *,
    cfg: dict,
    max_date: datetime.date | None = None,
) -> dict:
    """Recent-window evidence-of-harm guard over the SAME per-day paired diffs as the primary CI.

    ``diffs_by_day`` maps an ISO ``YYYY-MM-DD`` day key to that day's per-race paired diffs
    (candidate − active). Returns
    ``{"pass": bool, "mode": str, "margin": float, "windows": {...}}``.

    The windows are nested (3y ⊂ 5y) and share the bootstrap seed, so their CIs are strongly
    correlated — deliberately. The guard is a conjunction ("no window is confidently worse"), not
    an independent-evidence combination, so correlated windows cost nothing; what they must NOT
    do is drift apart by using different resampling units from the primary CI.
    """
    mode = recent_mode(cfg)
    margin = recent_margin(cfg)
    boot = (cfg or {}).get("bootstrap", {}) or {}
    b = int(boot.get("b", 2000))
    seed = int(boot.get("seed", 20260712))
    alpha = float(boot.get("alpha", 0.05))
    years_list = tuple(
        ((cfg or {}).get("recent_guard", {}) or {}).get("windows_years", DEFAULT_RECENT_WINDOWS)
    )

    days = sorted(diffs_by_day)
    if not days:
        return {"pass": True, "mode": mode, "margin": margin, "windows": {}, "empty": True}
    if max_date is None:
        max_date = datetime.date.fromisoformat(days[-1])

    windows: dict = {}
    passed = True
    for years in years_list:
        label = f"recent_{years}y"
        start = _window_start(max_date, int(years))
        # bounded on BOTH ends: an explicit max_date has to actually cap the window, or a day
        # after the reference date would slip in (2026-08 multi-codex review).
        sub = {
            d: v for d, v in diffs_by_day.items()
            if start <= datetime.date.fromisoformat(d) <= max_date
        }
        if not sub:
            windows[label] = {
                "n_races": 0, "empty": True, "decision": INCONCLUSIVE_LOW_PRECISION,
            }
            continue
        n_races = sum(len(v) for v in sub.values())
        point = sum(sum(v) for v in sub.values()) / n_races
        if mode == MODE_LEGACY_POINT:
            # v2 verbatim: zero-tolerance sign test on the point estimate.
            degraded = not (point < 0.0 or math.isclose(point, 0.0))
            windows[label] = {
                "diff": point, "n_races": n_races, "n_days": len(sub),
                "degraded": degraded, "decision": FAIL if degraded else "PASS",
            }
            passed = passed and not degraded
            continue
        ci = race_day_cluster_bootstrap_ci_v1(sub, b=b, seed=seed, alpha=alpha)
        decision = three_way(ci.ci_low, ci.ci_high, margin, point=ci.point)
        windows[label] = {
            "diff": ci.point, "ci_low": ci.ci_low, "ci_high": ci.ci_high,
            "n_races": n_races, "n_days": ci.n_days, "decision": decision,
            # kept so a v2-era reader still sees the old signal, now clearly marked as descriptive
            "point_estimate_degraded": ci.point > 0.0,
            # passing is "no harm DETECTED", so state what the interval still admits
            "residual_risk": residual_risk(ci.ci_high, decision),
        }
        passed = passed and decision != FAIL
    return {"pass": passed, "mode": mode, "margin": margin, "windows": windows}


def evaluate_core_gate(
    *,
    diff: float,
    ci_low: float | None,
    ci_high: float | None,
    recent: dict,
    top2_diff: float,
    top3_diff: float,
    cand_ece: float | None,
    act_ece: float | None,
    cfg: dict,
) -> CoreGate:
    """The pre-registered adoption conditions, evaluated once for every caller.

    ``diff`` is candidate − active on the PRIMARY metric (winner NLL), so negative is better.
    ``recent`` is a ``recent_window_guard`` result. All thresholds come from ``cfg`` (gate-config),
    never from a caller default that could drift between the two paths.

    ``min_effect_delta`` applies to the POINT ESTIMATE (``diff < -delta``), and the interval
    condition stays the separate ``ci_high < 0``. Spelled out because the two readings — a minimum
    on the estimate versus ``ci_high < -delta`` — differ materially and the config key alone does
    not say which (codex).
    """
    cfg = cfg or {}
    delta = float(cfg.get("min_effect_delta", 0.0))
    top = cfg.get("top_noninferior", {}) or {}
    cal = cfg.get("calibration", {}) or {}
    top2_tol = float(top.get("top2", 0.0005))
    top3_tol = float(top.get("top3", 0.0005))
    cal_width = float(cal.get("noninferior_width", 0.001))
    emergency_abs = float(cal.get("emergency_abs_ece", 0.05))

    # A missing ECE cannot be verified, so it fails closed rather than defaulting to "fine".
    ece_available = cand_ece is not None and act_ece is not None
    sub_gates = {
        "effect_beats_delta": bool(diff < -delta),
        "ci_upper_below_zero": bool(ci_high is not None and ci_high < 0.0),
        "recent_no_evidence_of_harm": bool(recent.get("pass", True)),
        "top2_noninferior": bool(top2_diff <= top2_tol),
        "top3_noninferior": bool(top3_diff <= top3_tol),
        "calibration_noninferior": bool(
            ece_available and (cand_ece - act_ece) <= cal_width
        ),
        "calibration_not_emergency": bool(ece_available and cand_ece < emergency_abs),
    }
    return CoreGate(
        sub_gates=sub_gates,
        adopted=all(sub_gates.values()),
        reasons={
            "winner_nll_diff": diff,
            "min_effect_delta": delta,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "top2_diff": top2_diff,
            "top3_diff": top3_diff,
            "cand_ece": cand_ece,
            "act_ece": act_ece,
            "ece_available": ece_available,
            "emergency_stop": bool(ece_available and cand_ece >= emergency_abs),
            "recent": recent,
        },
    )
