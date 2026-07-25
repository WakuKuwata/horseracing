"""Feature 083 fix: bundle reuse must key on MODEL RECIPE identity, not the repo HEAD.

Observed 2026-07-25: 7 unrelated commits between bundle generation and reuse changed
``code_sha``, so the full attestation digest no longer matched and a byte-identical-recipe
bundle was rejected. Recipe identity excludes code_sha; code_sha stays in provenance.
"""

from __future__ import annotations

from horseracing_training.segment_accuracy_run import recipe_identity


def _att(**over):
    base = {
        "base_model_version": "lgbm-064-f02acc",
        "objective": "pl_topk",
        "feature_version": "features-018",
        "ordered_feature_columns": ["a", "b"],
        "seed": 42,
        "code_sha": "aaaaaaaa",
        "attestation_digest": "digest-aaa",
    }
    base.update(over)
    return base


def test_identity_is_stable_across_unrelated_commits():
    a = _att(code_sha="aaaaaaaa", attestation_digest="digest-aaa")
    b = _att(code_sha="bbbbbbbb", attestation_digest="digest-bbb")
    assert recipe_identity(a) == recipe_identity(b)


def test_identity_changes_when_the_recipe_changes():
    base = _att()
    assert recipe_identity(base) != recipe_identity(_att(seed=43))
    assert recipe_identity(base) != recipe_identity(_att(base_model_version="lgbm-999"))
    assert recipe_identity(base) != recipe_identity(_att(feature_version="features-017"))
    assert recipe_identity(base) != recipe_identity(_att(ordered_feature_columns=["a"]))


def test_identity_is_deterministic():
    assert recipe_identity(_att()) == recipe_identity(_att())
