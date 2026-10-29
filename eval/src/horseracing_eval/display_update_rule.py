"""Pre-registered rule for changing the DISPLAYED expected return (C14, decided 2026-10-29).

What the ROI view shows — the displayed model version (``mev-ens15-v1``), the 120% mark
(``MARKET_EV_THRESHOLD=1.2`` in the API) and the market-mix 100% mark (141) — is only ever changed
on the strength of a PROSPECTIVE record, never on a read-back-period point estimate (142〜153 were
all ``can_adopt=false`` for that reason). The record is the 174 ``decision_quotes`` table: one
frozen T−3 quote per race, with the current production predictions stored next to it, so a
candidate can be settled on the SAME race at the SAME price as the incumbent.

The rule is frozen in ``specs/174-near-post-decision-quotes/display-update-rule.json`` (sha256
alongside) and this module is the single code copy; ``tests/unit/test_display_update_rule.py``
asserts the two agree. Changing either without the other is a test failure by design.

Not covered (by decision): the yearly coefficient/booster refresh (D17) is a maintenance step that
keeps the displayed model's definition, not a replacement, and runs outside this rule.
"""

from __future__ import annotations

from typing import Literal

from horseracing_eval import attention_rules as ar

RULE_VERSION = "display-update-rule-v1"
REGISTERED_ON = "2026-10-29"
DECIDED_BY = "kuwatawaku"  # remaining task C14, option B1 (recommended) chosen as-is

#: the only admissible evidence: 174 decision quotes (T−3 min, one row per race, append-only)
EVIDENCE_SOURCE = "decision_quotes"
#: incumbent and candidate are settled on the same race at the same frozen quote; the stake and
#: the settlement are those of B8 (100 yen per marked horse, official win payout)
SETTLEMENT = "official_win_payout_100yen_per_marked_horse"
#: minimum number of settled points (marked horses) before any verdict — same as the first 138
#: checkpoint and B8's 判断保留 floor
MIN_POINTS = int(ar.CHECKPOINTS[0])
#: the interval: race-day block bootstrap, identical impl/b/seed to 138 and B8
BOOTSTRAP = dict(ar.BOOTSTRAP)
#: replacement requires the lower bound of the 95% interval of
#: (candidate recovery rate − incumbent recovery rate) to be strictly above zero
MIN_DIFF_CI_LOW = 0.0

Verdict = Literal["insufficient", "keep", "replace"]


def decide(*, n_points: int, diff_ci_low: float | None) -> Verdict:
    """Apply the frozen rule. ``diff_ci_low`` is the lower 95% bound of candidate − incumbent.

    * fewer than ``MIN_POINTS`` settled points → ``insufficient`` (display unchanged, no verdict);
    * enough points and lower bound > ``MIN_DIFF_CI_LOW`` → ``replace``;
    * otherwise → ``keep`` (display unchanged; the candidate is not re-tried on the same points).
    """
    if n_points < MIN_POINTS:
        return "insufficient"
    if diff_ci_low is None:
        raise ValueError("diff_ci_low is required once n_points reaches MIN_POINTS")
    return "replace" if diff_ci_low > MIN_DIFF_CI_LOW else "keep"


def frozen_payload() -> dict:
    """The exact object written to the frozen JSON (keys sorted by the writer)."""
    return {
        "rule_version": RULE_VERSION,
        "registered_on": REGISTERED_ON,
        "decided_by": DECIDED_BY,
        "evidence_source": EVIDENCE_SOURCE,
        "settlement": SETTLEMENT,
        "min_points": MIN_POINTS,
        "bootstrap": BOOTSTRAP,
        "min_diff_ci_low": MIN_DIFF_CI_LOW,
        "display_fixed_unless_rule_met": [
            "displayed market-ev model version (mev-ens15-v1)",
            "120% mark (MARKET_EV_THRESHOLD=1.2)",
            "market-mix 100% mark (141)",
        ],
        "outside_this_rule": ["yearly coefficient/booster refresh (D17)"],
    }
