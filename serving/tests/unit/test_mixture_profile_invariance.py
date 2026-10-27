"""The real shipped bundle must keep loading and predicting EXACTLY as it does today.

Feature 136 generalises the mixture manifest so a bundle can declare a member set other than
the six of ``125_new_joint_mixed6_v1``.  That generalisation touches the same four places that
currently hard-code six: ``validate_manifest``, ``_load_member`` (its ``PARAMS`` check),
``predict_mixture``'s identity check, and ``average_member_predictions``.

These tests are written against the SHIPPED artefacts, not a synthetic manifest, and they are
meant to be run before and after the change with identical results.  ``mix-129-nj6`` is the
production ACTIVE model and feature 133's prospective window is open on it, so a behavioural
change here is not a regression in a research path -- it is a change to what is being served
and to what the 133 comparison records.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from horseracing_serving import mixture_model as m

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "artifacts/129-candidate-mixture-serving/bundle.json"
#: From specs/133-prospective-paired-comparison/preregistration.json (new_bundle_manifest_sha256).
REGISTERED_SHA = "28f39715a9c1dba13755ee44f3a439099d5aaffd94685ac11e4d23166b388471"

pytestmark = pytest.mark.skipif(not BUNDLE.is_file(), reason="shipped bundle not present")


def _manifest():
    return json.loads(BUNDLE.read_text())


def test_shipped_manifest_is_the_one_133_registered():
    assert hashlib.sha256(BUNDLE.read_bytes()).hexdigest() == REGISTERED_SHA


def test_shipped_manifest_validates():
    m.validate_manifest(_manifest())


def test_shipped_manifest_shape_is_unchanged():
    d = _manifest()
    assert d["profile"] == m.PROFILE
    assert len(d["members"]) == 6
    assert [x["id"] for x in d["members"]] == [i["id"] for i in m.IDENTITIES]
    assert [x["weight"] for x in d["members"]] == [1 / 6] * 6
    assert sum(x["weight"] for x in d["members"]) == pytest.approx(1.0, abs=1e-12)
    for item, identity in zip(d["members"], m.IDENTITIES, strict=True):
        assert item["branch"] == identity["branch"] and item["seed"] == identity["seed"]
        joint = identity["branch"] == "pruning"
        assert item["correction"]["terms"] == (m.JOINT_TERMS if joint else ["gap_log"])
        assert item["correction"]["source_study"] == (125 if joint else 118)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d["members"].pop(), id="five_members"),
        pytest.param(lambda d: d["members"].append(dict(d["members"][0])), id="seven_members"),
        pytest.param(lambda d: d["members"][0].update(weight=1 / 5), id="wrong_weight"),
        pytest.param(lambda d: d["members"].reverse(), id="wrong_order"),
        pytest.param(lambda d: d["members"][0].update(seed=99), id="wrong_seed"),
        pytest.param(lambda d: d["members"][0].update(branch="anchor"), id="wrong_branch"),
        pytest.param(lambda d: d.update(profile="something-else"), id="unknown_profile"),
        pytest.param(lambda d: d["members"][0]["correction"].update(source_study=118),
                     id="wrong_source_study"),
        pytest.param(lambda d: d["members"][0]["correction"].update(valid_year=2027),
                     id="wrong_valid_year"),
        pytest.param(lambda d: d.update(can_adopt=True), id="can_adopt"),
        pytest.param(lambda d: d.update(train_through="2026-08-24"), id="wrong_train_through"),
    ],
)
def test_mutating_the_shipped_manifest_is_still_rejected(mutate):
    """Every rejection the current validator makes must still be made.

    A generalisation that accepted any of these would be a widened hole, not a widened shape.
    """
    d = _manifest()
    mutate(d)
    with pytest.raises(ValueError):
        m.validate_manifest(d)


def test_unweighted_average_still_requires_exactly_six():
    """The legacy averaging contract, pinned so a weights= parameter cannot change it."""
    from horseracing_serving.mixture_correction import Prediction, average_member_predictions

    # Heads must satisfy the domain contract: with two started horses the win column sums to 1
    # and both top2/top3 sum to min(k, n) = 2, i.e. every horse is certain to be in the top 2.
    member = {"h1": Prediction(0.6, 1.0, 1.0), "h2": Prediction(0.4, 1.0, 1.0)}
    with pytest.raises(ValueError):
        average_member_predictions([member] * 5)
    out = average_member_predictions([member] * 6)
    assert out["h1"].win == pytest.approx(0.6) and out["h2"].win == pytest.approx(0.4)


# --- the generalisation itself ----------------------------------------------------------
CANDIDATE = ROOT / "artifacts/136-candidate-bundle/bundle.json"


def test_equal_weights_match_the_unweighted_mean_but_not_bit_for_bit():
    """Equal weights are mathematically the plain mean, but NOT the same floating-point sum.

    ``x.mean(axis=0)`` sums pairwise and divides once; ``tensordot(w, x)`` accumulates
    ``w_i * x_i``. They differ in the last ULP. Measured here so the difference is a recorded
    fact rather than a surprise: it is precisely why ``predict_mixture`` keeps routing the
    six-member profile to the unweighted call. Sending the shipped bundle through the weighted
    path would move its served probabilities by ~1e-16 for no reason, while feature 133 is
    recording paired comparisons against it.
    """
    import numpy as np
    from horseracing_training.predictor import assemble_predictions

    from horseracing_serving.mixture_correction import average_member_predictions

    rng = np.random.default_rng(20260922)
    ids = [f"h{i}" for i in range(8)]
    # Build the heads the way the pipeline does, so they satisfy the domain contract
    # (monotone across heads, column sums min(k, n)) instead of being hand-rolled.
    members = [assemble_predictions(ids, rng.dirichlet(np.ones(len(ids))), eps=0.)
               for _ in range(6)]
    plain = average_member_predictions(members)
    weighted = average_member_predictions(members, [1 / 6] * 6)
    worst = max(abs(getattr(plain[h], k) - getattr(weighted[h], k))
                for h in ids for k in ("win", "top2", "top3"))
    assert worst < 1e-15, "equal weights must agree with the plain mean to floating-point noise"
    assert worst > 0, (
        "if these ever became bit-identical, the routing below would be untestable -- but it "
        "would also mean the concern that motivates it had gone away; re-read the routing then"
    )


def test_six_member_profile_is_routed_to_the_unweighted_average():
    """The shipped profile must not acquire a weights= argument it never had.

    Kill-test for the routing in ``average_for_profile``: drop the branch and the production
    bundle goes through the weighted accumulation, moving its served numbers in the last ULP
    while feature 133 is recording paired comparisons against it.
    """
    import numpy as np
    from horseracing_training.predictor import assemble_predictions

    from horseracing_serving.mixture_correction import average_member_predictions

    rng = np.random.default_rng(20260922)
    ids = [f"h{i}" for i in range(8)]
    members = [assemble_predictions(ids, rng.dirichlet(np.ones(len(ids))), eps=0.)
               for _ in range(6)]
    equal = [1 / 6] * 6

    routed = m.average_for_profile(m.PROFILE, members, equal)
    plain = average_member_predictions(members)
    weighted = average_member_predictions(members, equal)

    for h in ids:
        for head in ("win", "top2", "top3"):
            assert getattr(routed[h], head) == getattr(plain[h], head)
    assert any(getattr(plain[h], head) != getattr(weighted[h], head)
               for h in ids for head in ("win", "top2", "top3")), (
        "the two paths happen to agree exactly here, so this test cannot detect the routing; "
        "pick a case where they differ before trusting it"
    )


def test_other_profiles_are_routed_to_the_weighted_average():
    import numpy as np
    from horseracing_training.predictor import assemble_predictions

    from horseracing_serving.mixture_correction import average_member_predictions

    rng = np.random.default_rng(1)
    ids = [f"h{i}" for i in range(6)]
    members = [assemble_predictions(ids, rng.dirichlet(np.ones(len(ids))), eps=0.)
               for _ in range(4)]
    weights = [0.4, 0.3, 0.2, 0.1]
    routed = m.average_for_profile("some-other-profile", members, weights)
    expected = average_member_predictions(members, weights)
    for h in ids:
        assert routed[h].win == expected[h].win


@pytest.mark.parametrize(
    "weights",
    [
        pytest.param([1 / 6] * 5, id="wrong_length"),
        pytest.param([0.0] + [0.2] * 5, id="zero_weight"),
        pytest.param([-0.1] + [0.22] * 5, id="negative_weight"),
        pytest.param([0.2] * 6, id="does_not_sum_to_one"),
        pytest.param([float("nan")] + [0.2] * 5, id="nan_weight"),
    ],
)
def test_invalid_weights_are_refused(weights):
    from horseracing_serving.mixture_correction import Prediction, average_member_predictions

    member = {"h1": Prediction(0.6, 1.0, 1.0), "h2": Prediction(0.4, 1.0, 1.0)}
    with pytest.raises(ValueError):
        average_member_predictions([member] * 6, weights)


@pytest.mark.skipif(not CANDIDATE.is_file(), reason="candidate bundle not assembled")
def test_candidate_bundle_loads_through_the_production_loader():
    """A twelve-member bundle with unequal member weights must load, not just validate."""
    bundle = m.load_mixture_bundle(CANDIDATE)
    specs = m.profile_members(bundle.manifest["profile"])
    assert bundle.manifest["profile"] == m.CANDIDATE_PROFILE
    assert len(bundle.members) == 12
    weights = [item["weight"] for item in bundle.manifest["members"]]
    assert sum(weights) == pytest.approx(1.0, abs=1e-12)
    by_branch: dict[str, float] = {}
    for spec, w in zip(specs, weights, strict=True):
        by_branch[spec["branch"]] = by_branch.get(spec["branch"], 0.0) + w
    # the branch allocation the pair pre-registration measured, preserved exactly
    assert by_branch == pytest.approx(
        {"pruning": 0.375, "anchor": 0.375,
         "extra_trees_joint": 0.125, "leaves63_child100_joint": 0.125}, abs=1e-12)


@pytest.mark.skipif(not CANDIDATE.is_file(), reason="candidate bundle not assembled")
def test_candidate_members_carry_joint42_coefficients_not_their_own_seed():
    """The transfer convention C1/C2 were measured under (member_kill_test.py:216)."""
    d = json.loads(CANDIDATE.read_text())
    by_id = {item["id"]: item["correction"] for item in d["members"]}
    reference = by_id["joint-42"]["coefficients"]
    for name in ("extra_trees_joint", "leaves63_child100_joint"):
        for seed in (42, 43, 44):
            assert by_id[f"{name}-{seed}"]["coefficients"] == reference
            assert by_id[f"{name}-{seed}"]["terms"] == m.JOINT_TERMS
    # the production members keep their OWN coefficients; joint-43 must differ from joint-42
    assert by_id["joint-43"]["coefficients"] != reference


@pytest.mark.skipif(not CANDIDATE.is_file(), reason="candidate bundle not assembled")
def test_candidate_bundle_cannot_claim_adoption():
    d = json.loads(CANDIDATE.read_text())
    assert d["can_adopt"] is False and d["eligible_for_verdict"] is False and d["mode"] == "shadow"
