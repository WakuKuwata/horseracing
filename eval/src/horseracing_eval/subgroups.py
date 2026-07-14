"""Feature 069 US1: subgroup assignment + three-way intersection-union guard (FR-001/002/004).

Grain separation (codex C1): winner NLL is race-level (1 winner/race), so its subgroups use
RESULT-INDEPENDENT race attributes only (``2026_only``, ``2026_field_has_nk``). The ID-source /
coverage question is per-horse, so it is scored on the started-all per-horse loss with horse-level
attributes (``canonical``/``nk``/``2026_nk``/coverage bands). No winner-conditioned race selection.

Assignment reads ONLY injected attributes — race_date.year, horse_id ``nk:`` prefix, and (for
coverage bands) the strictly-before market-observation count — never a result label (FR-004). This
module does band assignment + the guard decision; overround/odds-quality auditing lives in
training's coverage-audit, not here (codex C7). No ``training`` import (020 boundary).
"""

from __future__ import annotations

NK_PREFIX = "nk:"
_TARGET_YEAR = 2026


def is_nk(horse_id: str) -> bool:
    return str(horse_id).startswith(NK_PREFIX)


def race_subgroup_labels(race_year: int, field_has_nk: bool) -> set[str]:
    """Race-level (winner-NLL) subgroups from result-independent race attributes.

    ``field_has_nk`` is rolled up by the caller from the started field's per-horse ``nk:`` prefixes
    (analyze U2) — no result label is read.
    """
    labels: set[str] = set()
    if race_year == _TARGET_YEAR:
        labels.add("2026_only")
        if field_has_nk:
            labels.add("2026_field_has_nk")
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


def horse_subgroup_labels(horse_id: str, race_year: int, obs_count=None) -> set[str]:
    """Horse-level (started-all per-horse) subgroups from per-horse attributes only."""
    nk = is_nk(horse_id)
    labels: set[str] = {"nk" if nk else "canonical"}
    if race_year == _TARGET_YEAR and nk:
        labels.add("2026_nk")
    band = coverage_band(obs_count)
    if band is not None:
        labels.add(band)
    return labels


def three_way(ci_low, ci_high, margin: float) -> str:
    """Three-way subgroup decision (codex C2): PASS if the CI upper bound is below the tolerated
    degradation ``margin``; FAIL if the CI lower bound is above it (confidently worse); else
    NO_DECISION (CI straddles the margin, or is undefined). ``diff`` sign convention: candidate −
    active, so smaller/negative is candidate-better."""
    if ci_low is None or ci_high is None:
        return "NO_DECISION"
    if ci_high < margin:
        return "PASS"
    if ci_low > margin:
        return "FAIL"
    return "NO_DECISION"


def subgroup_guard(decisions: dict[str, str], critical: list[str]) -> bool:
    """Intersection-union (codex C3): the guard passes iff EVERY critical subgroup is PASS.
    A NO_DECISION critical subgroup is not a veto but is not sufficient — it blocks adoption."""
    return all(decisions.get(c) == "PASS" for c in critical)
