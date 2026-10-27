"""136 bundle driver: pin the things that would silently build the WRONG member.

This driver works by REBINDING globals in ``candidate_mixture_build``. That is powerful and
quiet: if a rebinding silently failed, the 129 builder's own functions would happily fit the six
PRODUCTION members into the 129 artifact tree instead, and every downstream check would pass
because they would all be self-consistent. These tests exist mostly to make that impossible.

No fitting, no database, no artifact is written.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

C = pytest.importorskip("candidate_bundle_136")
import candidate_mixture_build as B  # noqa: E402

KILLTEST_CACHE = (
    ROOT / "artifacts/136-eight-model-candidate/preserved-killtest-2cced7b2/s2/cache/joint125"
)
PROD_JOINT = ROOT / "artifacts/129-candidate-mixture-serving/members/joint-42/metadata.json"


# --- the rebinding actually took effect ------------------------------------------------
def test_rebinding_redirected_the_129_builder():
    assert B.MEMBERS is C.MEMBERS
    assert B.make_recipe is C.make_recipe
    assert B.build_config is C.build_config
    assert B.source_hash is C.source_hash
    assert B.upstream_state is C.upstream_state


def test_work_dir_is_outside_the_129_tree_and_lock_visible():
    """A lock nested deeper than one level is invisible to ``assert_no_other_training``."""
    assert B.WORK == C.WORK
    assert "129-candidate-mixture-serving" not in str(C.WORK)
    assert C.WORK.parent == ROOT / "artifacts", (
        "the running.lock must sit at artifacts/<dir>/running.lock or the one-level glob in "
        "assert_no_other_training cannot see it, and two trainings could run at once"
    )


def test_member_set_is_two_candidates_by_three_seeds():
    assert len(C.MEMBERS) == 6
    assert {m["branch"] for m in C.MEMBERS} == {"extra_trees_joint", "leaves63_child100_joint"}
    assert {m["seed"] for m in C.MEMBERS} == {42, 43, 44}
    assert len({m["id"] for m in C.MEMBERS}) == 6
    # six is the superset of both configurations under discussion; it must not be trimmed to
    # one seed per candidate, which would bake in the un-decided configuration choice.
    assert all(len([m for m in C.MEMBERS if m["branch"] == b]) == 3
               for b in {m["branch"] for m in C.MEMBERS})


# --- the recipe is the kill-test's, at the bundle's boundary ---------------------------
@pytest.mark.skipif(not KILLTEST_CACHE.is_dir(), reason="preserved kill-test cache unavailable")
def test_recipe_matches_every_killtest_member_year_receipt():
    """The new members must BE the kill-test candidates, not merely resemble them."""
    checked = 0
    for member in C.MEMBERS:
        mine = json.loads(json.dumps(C.make_recipe(member).meta(), default=str))
        for year in range(2020, 2027):
            receipt = json.loads(
                (KILLTEST_CACHE / f"{member['branch']}-s{member['seed']}-{year}.json").read_text()
            )
            assert receipt["recipe_meta"] == mine, (
                f"{member['id']} {year}: recipe differs from the fitted member-year"
            )
            checked += 1
    assert checked == 42


@pytest.mark.skipif(not PROD_JOINT.is_file(), reason="production joint member unavailable")
def test_only_the_lightgbm_params_differ_from_the_shipped_joint_member():
    shipped = json.loads(PROD_JOINT.read_text())["recipe"]
    mine = json.loads(json.dumps(C.make_recipe(C.MEMBERS[0]).meta(), default=str))
    differing = {k for k in set(shipped) | set(mine) if shipped.get(k) != mine.get(k)}
    # arm / n_oof_blocks are added by CalibSplitFactory, not by the recipe.
    assert differing <= {"params", "arm", "n_oof_blocks"}, differing
    assert shipped["drop_features"] == list(C.JOINT_DROPS)
    assert len(C.JOINT_DROPS) == 16


@pytest.mark.skipif(not PROD_JOINT.is_file(), reason="production joint member unavailable")
def test_drops_are_read_from_the_shipped_member_not_retyped():
    """Retyping the 16 columns would let the two definitions drift apart unnoticed."""
    shipped = json.loads(PROD_JOINT.read_text())["recipe"]["drop_features"]
    assert list(C.JOINT_DROPS) == shipped


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("extra_trees_joint", (("extra_trees", True), ("n_estimators", 900))),
        ("leaves63_child100_joint",
         (("min_child_samples", 100), ("n_estimators", 900), ("num_leaves", 63))),
    ],
)
def test_candidate_params(branch, expected):
    member = next(m for m in C.MEMBERS if m["branch"] == branch and m["seed"] == 42)
    assert C.make_recipe(member).params == expected


def test_smoke_shortens_only_the_rounds():
    member = C.MEMBERS[0]
    full, smoke = C.make_recipe(member), C.make_recipe(member, smoke=True)
    assert dict(full.params)["n_estimators"] == 900
    assert dict(smoke.params)["n_estimators"] == 5
    assert {k: v for k, v in full.params if k != "n_estimators"} == {
        k: v for k, v in smoke.params if k != "n_estimators"
    }
    assert full.seed == smoke.seed and full.drop_features == smoke.drop_features


def test_recipe_refuses_an_unregistered_member():
    with pytest.raises(ValueError):
        C.make_recipe({"id": "extra_trees_joint-99", "branch": "extra_trees_joint", "seed": 99})
    with pytest.raises(ValueError):
        C.make_recipe(dict(C.MEMBERS[0], seed=1234))


def test_seed_reaches_the_recipe():
    """A seed that never reaches the recipe would give three identical 'seeds'."""
    seeds = {C.make_recipe(m).seed for m in C.MEMBERS if m["branch"] == "extra_trees_joint"}
    assert seeds == {42, 43, 44}


# --- provenance -------------------------------------------------------------------------
def test_source_hash_covers_the_code_that_fits():
    paths = {str(p.relative_to(ROOT)) for p in C._fit_code_paths()}
    assert "scripts/candidate_bundle_136.py" in paths
    assert "scripts/candidate_mixture_build.py" in paths
    assert "serving/src/horseracing_serving/model_loader.py" in paths
    for package in ("db", "features", "training", "eval", "probability"):
        assert any(p.startswith(f"{package}/src/") for p in paths), package
    assert len(C.source_hash()) == 64


def test_source_hash_is_not_the_129_value():
    """It must not silently inherit the 125 chain it deliberately does not bind."""
    assert C.source_hash() != B.s.p.stable_hash({})


@pytest.mark.skipif(not KILLTEST_CACHE.is_dir(), reason="preserved kill-test cache unavailable")
def test_upstream_state_binds_the_selection_evidence():
    state = C.upstream_state()
    assert len(state["killtest_member_year_receipts"]) == 42
    assert set(state["killtest_preregistrations"]) == {
        "member_prereg_v2.json", "member_prereg_freeze.json",
        "member_build_addendum.json", "member_build_addendum_freeze.json",
        "pair_prereg.json", "pair_prereg_freeze.json",
    }
    assert len(state["snapshot_sha256"]) == 64
    assert len(state["production_joint_member_metadata_sha256"]) == 64
    assert "123/124/125" in state["not_bound_to"]


def test_config_records_the_candidate_parameters_and_is_not_the_129_profile():
    cfg = C.build_config()
    assert cfg["profile"] == "136_candidate_members_v1"
    assert cfg["members"] == C.MEMBERS
    assert cfg["candidate_params"] == {
        "extra_trees_joint": {"extra_trees": True},
        "leaves63_child100_joint": {"num_leaves": 63, "min_child_samples": 100},
    }
    assert cfg["train_through"] == "2026-08-23"
    assert cfg["num_threads"] == 1 and cfg["max_workers"] == 2
    assert cfg["can_adopt"] is False and cfg["eligible_for_verdict"] is False
    assert cfg["db_registration"] is False


def test_build_source_paths_does_not_recurse():
    paths = C.build_source_paths()
    assert Path(__file__).resolve().parents[1] / "candidate_bundle_136.py" in paths
    assert len(paths) == len(set(paths))
