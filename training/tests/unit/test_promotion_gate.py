"""The ACTIVE-promotion boundary (2026-08 multi-codex review).

Until this existed, ``adoption_status=active`` was decided by ``evaluate_gate`` alone: four
point-estimate comparisons against a stored baseline summary, with no paired design, no confidence
interval, no subgroup guard, no confirmatory contract and no artifact isolation. A candidate that
evaluation contract v3 would REJECT could go live by beating those four numbers.

The only test that touched the path asserted ``adoption_status in ("active", "candidate")`` — a
tautology — so the behaviour was effectively uncovered. These tests pin it.
"""

from __future__ import annotations

from horseracing_training.adoption import (
    AdoptionDecision,
    PromotionDecision,
    evaluate_promotion,
    normalize_verdict,
)

ADOPTED = AdoptionDecision(adopted=True, reasons={})
NOT_ADOPTED = AdoptionDecision(adopted=False, reasons={})


def _regime_report(status="ADOPT", assurance="full", kind="full_walk_forward", eligible=True):
    """A ``regime_paired.RegimeReport``-shaped dict (091 path)."""
    return {
        "artifact_kind": kind,
        "eligible_for_verdict": eligible,
        "verdict": {"status": status, "subgroup_assurance": assurance},
        "gate_config": {"evaluation_contract_version": "v3"},
    }


def _paired_report(status="ADOPT", assurance="full"):
    """A ``paired.PairedReport``-shaped dict (standard path — no artifact_kind concept)."""
    return {
        "decision": status,
        "decision_reason": {"subgroup_assurance": assurance},
        "evaluation_contract_version": "v3",
    }


def _promote(verdict, legacy=ADOPTED, **kw) -> PromotionDecision:
    return evaluate_promotion(legacy=legacy, verdict=verdict, **kw)


# --- the promotable case ----------------------------------------------------------------------

def test_active_requires_legacy_gate_and_a_full_assurance_v3_adopt():
    for report in (_regime_report(), _paired_report()):
        d = _promote(report)
        assert d.promotable is True
        assert d.status == "active"
        assert d.reasons["cause"] == "legacy_gate_and_v3_verdict_agree"


# --- everything that must NOT reach active ------------------------------------------------------

def test_legacy_gate_alone_can_no_longer_activate():
    """The regression this whole boundary exists for."""
    d = _promote(None)
    assert d.status == "candidate"
    assert d.reasons["cause"] == "no_v3_verdict_supplied"
    assert "paired-eval --confirmatory" in d.reasons["hint"]


def test_partial_subgroup_assurance_stays_a_candidate():
    """"no FAIL" is not "no harm": the run cannot speak about that population, so the model waits
    for current-regime evidence — which is what 085/091 did by hand."""
    d = _promote(_regime_report(assurance="partial"))
    assert d.status == "candidate"
    assert d.reasons["cause"] == "subgroup_assurance_not_full"


def test_non_adopt_verdicts_stay_candidates():
    for status in ("REJECT", "NO_DECISION"):
        d = _promote(_regime_report(status=status))
        assert d.status == "candidate"
        assert d.reasons["cause"] == "v3_verdict_not_adopt"


def test_ineligible_artifacts_cannot_promote():
    """acceptance / diagnostic / exploratory arms share folds with the confirmatory window."""
    for kind in ("acceptance", "diagnostic", "exploratory"):
        d = _promote(_regime_report(kind=kind))
        assert d.status == "candidate"
        assert d.reasons["cause"] == "verdict_artifact_not_eligible"
    # the flag alone is enough to refuse, even with the right kind
    d = _promote(_regime_report(eligible=False))
    assert d.reasons["cause"] == "verdict_artifact_not_eligible"


def test_a_v2_era_verdict_cannot_promote():
    report = _paired_report()
    report["evaluation_contract_version"] = "v2"
    d = _promote(report)
    assert d.status == "candidate"
    assert d.reasons["cause"] == "verdict_contract_too_old"


def test_legacy_gate_failure_short_circuits():
    d = _promote(_regime_report(), legacy=NOT_ADOPTED)
    assert d.status == "candidate"
    assert d.reasons["cause"] == "legacy_gate_not_adopted"


def test_register_as_candidate_wins_over_a_perfect_verdict():
    d = _promote(_regime_report(), register_as_candidate=True)
    assert d.status == "candidate"
    assert d.reasons["cause"] == "register_as_candidate_requested"


# --- shape normalisation ------------------------------------------------------------------------

def test_normalize_reads_both_report_shapes():
    a = normalize_verdict(_regime_report(status="REJECT", assurance="partial"))
    assert (a["status"], a["subgroup_assurance"], a["contract_version"]) == (
        "REJECT", "partial", "v3")
    assert a["artifact_kind"] == "full_walk_forward"

    b = normalize_verdict(_paired_report(status="NO_DECISION", assurance="partial"))
    assert (b["status"], b["subgroup_assurance"], b["contract_version"]) == (
        "NO_DECISION", "partial", "v3")
    assert b["artifact_kind"] is None  # standard path has no acceptance/diagnostic arms

    assert normalize_verdict(None) is None
    assert normalize_verdict({}) is None


def test_promotion_never_raises_so_a_trained_model_is_never_lost():
    """A run that cannot justify promotion still saves its artifact as a candidate."""
    for junk in ({}, {"verdict": {}}, {"decision": None}, {"artifact_kind": "full_walk_forward"}):
        d = _promote(junk)
        assert d.status == "candidate" and d.promotable is False


def test_current_eval_contract_can_promote():
    """eval が今出している契約版で実際に昇格できること。

    この 1 件が無いと、契約版を上げた瞬間に昇格経路が黙って死ぬ。実際に一度死んでいる: training が
    "v3" を等値で要求している間に eval が "v4" になり、**より強い証拠**(v4 は再学習分散を区間に
    畳み込む)を出した判定がすべて mismatch として candidate に落とされていた。ここは eval の生の
    定数を読むので、次に契約版が動いたとき training 側で気づける。
    """
    from horseracing_eval.decision import EVALUATION_CONTRACT_VERSION

    report = _paired_report()
    report["evaluation_contract_version"] = EVALUATION_CONTRACT_VERSION
    assert _promote(report).status == "active"


def test_unparseable_contract_fails_closed():
    """読めない契約版は「新しいかもしれない」ではなく昇格不可に倒す。"""
    for bad in (None, "", "bogus", "3", "v", "v3.1"):
        report = _paired_report()
        report["evaluation_contract_version"] = bad
        d = _promote(report)
        assert d.status == "candidate", bad
        assert d.reasons["cause"] == "verdict_contract_too_old", bad
