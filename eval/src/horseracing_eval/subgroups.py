"""Feature 069 US1: subgroup assignment + three-way intersection-union guard (FR-001/002/004).

Grain separation (codex C1): winner NLL is race-level (1 winner/race), so its subgroups use
RESULT-INDEPENDENT race attributes only (``recent_year_only``, ``recent_year_field_has_nk``). The
ID-source / coverage question is per-horse, so it is scored on the started-all per-horse loss with
horse-level attributes (``canonical``/``nk``/``recent_year_nk``/coverage bands). No
winner-conditioned race selection.

Assignment reads ONLY injected attributes — race_date.year, horse_id ``nk:`` prefix, and (for
coverage bands) the strictly-before market-observation count — never a result label (FR-004). This
module does band assignment + the guard decision; overround/odds-quality auditing lives in
training's coverage-audit, not here (codex C7). No ``training`` import (020 boundary).

Contract v3 changes two things here (see contracts/adoption-gate.md):

1. The "recent year" is no longer the literal constant 2026. Every label is emitted under BOTH a
   stable name (``recent_year_only``) and the year-stamped name (``2026_only``), so pre-registered
   gate-configs naming a year keep working while the guard itself follows the eval window.
2. ``three_way`` distinguishes INCONCLUSIVE_LOW_PRECISION from NO_DECISION. When the CI's upper arm
   (``ci_high − point``) is at least the margin, PASS is only reachable by showing outright
   superiority (``point < margin − upper_arm ≤ 0``) — at zero true effect the test CANNOT conclude
   non-inferiority. Measured on the 088 record: ``2026_only`` had an upper arm of ~0.0071 against a
   0.005 margin, so PASS required a 0.0021 improvement in 66 race-days — a stronger demand than the
   whole 821-day primary test. Calling that outcome "the candidate failed a guard" is false; it is
   "this subgroup could not be tested at this margin".

   The test uses the UPPER ARM, not the half-width: these are percentile bootstrap intervals and
   are not symmetric about the point estimate, and it is the upper arm that decides PASS (codex).
   The state is named for what is observed after the fact — insufficient precision — rather than
   "underpowered", which is a property of a pre-specified design (codex).
"""

from __future__ import annotations

NK_PREFIX = "nk:"

#: Fallback "recent year" for callers that do not derive it from the eval window (unit tests,
#: legacy call sites). Production callers pass ``target_year`` explicitly — see
#: ``paired.resolve_target_year``.
_TARGET_YEAR = 2026

PASS = "PASS"
FAIL = "FAIL"
NO_DECISION = "NO_DECISION"
#: No conclusion, AND the interval was too wide to have concluded PASS at zero true effect.
INCONCLUSIVE_LOW_PRECISION = "INCONCLUSIVE_LOW_PRECISION"
#: Outcomes that are neither a non-inferiority claim nor evidence of harm.
NOT_PROVEN_STATES = (NO_DECISION, INCONCLUSIVE_LOW_PRECISION)

#: Guard-level roll-ups (``subgroup_guard_status``).
GUARD_PASS = "PASS"            # every critical subgroup established non-inferiority
GUARD_FAIL = "FAIL"            # at least one critical subgroup is confidently worse
GUARD_NOT_PROVEN = "NOT_PROVEN"  # nothing confidently worse, but not everything proven
GUARD_MISSING = "MISSING"      # a critical subgroup was never computed (wiring fault)


def is_nk(horse_id: str) -> bool:
    return str(horse_id).startswith(NK_PREFIX)


def race_subgroup_labels(
    race_year: int, field_has_nk: bool, *, target_year: int = _TARGET_YEAR
) -> set[str]:
    """Race-level (winner-NLL) subgroups from result-independent race attributes.

    ``field_has_nk`` is rolled up by the caller from the started field's per-horse ``nk:`` prefixes
    (analyze U2) — no result label is read. Each subgroup carries a stable name and a year-stamped
    alias so a gate-config frozen against either name resolves.
    """
    labels: set[str] = set()
    if race_year == target_year:
        labels.update(("recent_year_only", f"{target_year}_only"))
        if field_has_nk:
            labels.update(
                ("recent_year_field_has_nk", f"{target_year}_field_has_nk")
            )
    return labels


def coverage_band(obs_count) -> str | None:
    """Coverage band from the strictly-before market-observation count (F02 obs_count).

    Returns None when obs_count is not injected (US1 MVP runs without F02 — critical subgroups do
    not need it, analyze U1)."""
    if obs_count is None:
        return None
    n = int(obs_count)
    if n == 0:
        return "cov_0"
    if n <= 2:
        return "cov_1_2"
    return "cov_3plus"


def horse_subgroup_labels(
    horse_id: str, race_year: int, obs_count=None, *, target_year: int = _TARGET_YEAR
) -> set[str]:
    """Horse-level (started-all per-horse) subgroups from per-horse attributes only."""
    nk = is_nk(horse_id)
    labels: set[str] = {"nk" if nk else "canonical"}
    if race_year == target_year and nk:
        labels.update(("recent_year_nk", f"{target_year}_nk"))
    band = coverage_band(obs_count)
    if band is not None:
        labels.add(band)
    return labels


def three_way(ci_low, ci_high, margin: float, *, point=None) -> str:
    """Four-state non-inferiority decision. Sign convention: candidate − active, so
    smaller/negative is candidate-better.

    - ``PASS``: the CI upper bound is below the tolerated degradation ``margin`` — non-inferiority
      is established. Unchanged from v2 and valid at ANY CI width: a test that concluded did
      conclude.
    - ``FAIL``: the CI lower bound is above the margin — confidently worse. Unchanged from v2.
    - ``INCONCLUSIVE_LOW_PRECISION``: no conclusion, and the interval's upper arm
      (``ci_high − point``) is at least the margin, so PASS was reachable only by proving outright
      superiority. An undefined CI (fewer than two race-days) lands here too.
    - ``NO_DECISION``: no conclusion from a test that COULD have concluded — the estimate genuinely
      sits near the margin.

    ``point`` is the point estimate. These are PERCENTILE bootstrap intervals, which are not
    symmetric about it, so precision is judged on the upper arm rather than the half-width; when
    ``point`` is not supplied the interval midpoint is used as an approximation.
    """
    if ci_low is None or ci_high is None:
        return INCONCLUSIVE_LOW_PRECISION
    if ci_high < margin:
        return PASS
    if ci_low > margin:
        return FAIL
    centre = (ci_low + ci_high) / 2.0 if point is None else float(point)
    upper_arm = ci_high - centre
    return INCONCLUSIVE_LOW_PRECISION if upper_arm >= margin else NO_DECISION


def residual_risk(ci_high, decision: str) -> float | None:
    """The largest degradation still inside the 95% interval for a subgroup that did not conclude.

    Disclosure requirement (codex): "no FAIL" must never be reported as "no harm". For an
    inconclusive subgroup this number is the honest statement of what remains possible — on the 088
    record ``2026_only`` still admitted +0.0083 winner NLL of degradation.
    """
    if decision in NOT_PROVEN_STATES and ci_high is not None:
        return float(ci_high)
    return None


def subgroup_guard(decisions: dict[str, str], critical: list[str]) -> bool:
    """Strict intersection-union (codex C3): True iff EVERY critical subgroup is PASS.

    Kept as the audit field — it records whether FULL assurance was achieved. It is no longer the
    adoption veto on its own; ``subgroup_guard_status`` is (contract v3).
    """
    return all(decisions.get(c) == PASS for c in critical)


def subgroup_guard_status(decisions: dict[str, str], critical: list[str]) -> str:
    """Tri-value roll-up of the critical subgroups (contract v3).

    - ``FAIL``: at least one critical subgroup is confidently worse than the margin → REJECT.
    - ``MISSING``: a critical subgroup was never computed. That is a wiring fault, not a statistical
      outcome, so it stays fail-closed (a silently skipped pre-registered guard is exactly what the
      069 design exists to prevent).
    - ``PASS``: every critical subgroup established non-inferiority.
    - ``NOT_PROVEN``: nothing is confidently worse, but at least one subgroup could not conclude.
      Adoption is NOT blocked; the report discloses reduced assurance.

    Rationale for not blocking on NOT_PROVEN: under v2 an untestable subgroup produced the same
    outcome as a harmful one, which turned "we cannot see" into "the candidate is bad". Only
    evidence of harm should veto.

    What this COSTS, stated plainly (codex): adoption no longer carries the intersection-union
    claim "non-inferiority holds in every critical subgroup". ``PASS`` still does, and is reported
    as ``subgroup_assurance="full"``; ``NOT_PROVEN`` means only "no degradation beyond the margin
    was DETECTED". At the recorded precisions the FAIL arm is itself weak — a true +0.010
    degradation in ``2026_only`` is detected only ~28% of the time — so ``residual_risk`` must be
    read alongside it rather than treated as reassurance.
    """
    if not critical:
        return GUARD_PASS
    states = [decisions.get(c, GUARD_MISSING) for c in critical]
    if any(s == FAIL for s in states):
        return GUARD_FAIL
    if any(s == GUARD_MISSING for s in states):
        return GUARD_MISSING
    if all(s == PASS for s in states):
        return GUARD_PASS
    return GUARD_NOT_PROVEN
