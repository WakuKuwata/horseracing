"""085 x 091: arm E must build its booster from the WHOLE recipe, not a hand-picked subset.

Arm E legitimately overrides the calibration fields — that is what the arm *is* (full-history
booster, `calib_frac=0.0`, calibration fitted out-of-fold instead of on a holdout). Everything
else has to survive, and the field that made this urgent is feature 091's weight mask.

Why this is not a style complaint: 091's own design note calls the mask "the mechanism, not a
tweak". Without it the same-day `weight` column shadows `prev_weight`, the model never learns
the path serving actually takes (97% of live races have no weight), and the fix silently does
nothing. A full-history booster built from the production recipe *minus* the mask would be
recorded as "arm E of this recipe" while being a different model — and the difference is
invisible in every metric that does not evaluate under the serving regime.
"""

from __future__ import annotations

import dataclasses

import pytest

from horseracing_training.calib_split import OofCalibratedPredictor
from horseracing_training.recipe import ModelRecipe

#: The live recipe at the time of writing (lgbm-091-wmask).
_MASKED = ModelRecipe(
    objective="pl_topk", calibration="isotonic", calib_frac=0.3, seed=42,
    weight_mask_rate=0.5, weight_mask_seed=20260810,
)


def _base(recipe: ModelRecipe, monkeypatch):
    """Build arm E's inner predictor without touching a database or fitting anything."""
    captured = {}

    class _Spy:
        def __init__(self, session, **kw):
            captured.update(kw)

    monkeypatch.setattr("horseracing_training.calib_split.LightGBMPredictor", _Spy)
    OofCalibratedPredictor(None, recipe, method="isotonic")._make_base()
    return captured


def test_the_weight_mask_reaches_the_booster(monkeypatch):
    """THE regression. If `_make_base` forwards a subset that omits the mask, the spec built here
    is None and arm E quietly trains the pre-091 model."""
    kw = _base(_MASKED, monkeypatch)

    spec = kw.get("fit_weight_mask")
    assert spec is not None, (
        "arm E dropped the weight mask: it would train a full-history booster that never learns "
        "the no-weight serving path"
    )
    assert spec.rate == 0.5
    assert spec.seed == 20260810
    assert spec.unit == "race"


def test_a_recipe_without_a_mask_still_passes_none(monkeypatch):
    """`None` means "this recipe predates masking" and must not become `rate=0.0`, which means
    "masking was deliberately switched off" — the recipe docstring keeps those distinct."""
    kw = _base(ModelRecipe(objective="pl_topk", calibration="isotonic", seed=42), monkeypatch)
    assert kw.get("fit_weight_mask") is None


def test_arm_e_overrides_only_the_calibration_fields(monkeypatch):
    """Guards the general defect rather than the one instance of it.

    Every recipe field is set to a non-default value; whatever arm E forwards must match the
    recipe, EXCEPT the calibration fields the arm is defined by. A future field added to
    ModelRecipe and forgotten in `_make_base` fails here — as long as the predictor accepts it
    under the same name, which the two known renames below make explicit.
    """
    recipe = dataclasses.replace(
        _MASKED,
        target_encode_cols=("jockey_id",),
        te_smoothing=33.0,
        seed=7,
        drop_features=("some_group",),
    )
    kw = _base(recipe, monkeypatch)

    # arm E IS these two overrides; asserting them pins the arm's definition.
    assert kw["calibration"] == "none"
    assert kw["calib_frac"] == 0.0

    for field, value in (
        ("objective", recipe.objective),
        ("target_encode_cols", recipe.target_encode_cols),
        ("te_smoothing", recipe.te_smoothing),
        ("seed", recipe.seed),
        ("drop_features", recipe.drop_features),
    ):
        assert kw[field] == value, f"arm E did not forward {field}"


@pytest.mark.parametrize("field", ["weight_mask_rate", "weight_mask_seed"])
def test_the_mask_pair_is_not_half_configured(field):
    """A half-set pair would build a mask with a None seed = non-deterministic masking, which
    would make the artifact irreproducible. The recipe rejects it at construction."""
    with pytest.raises(ValueError, match="weight_mask"):
        dataclasses.replace(_MASKED, **{field: None})


def test_a_new_recipe_field_fails_closed(monkeypatch):
    """The guard against the NEXT instance of this defect.

    The mask was dropped because the builder forwards a hand-picked subset, so a field added to
    the recipe afterwards is ignored by construction and nothing complains. Rather than trusting
    a future author to notice, an unaccounted-for field refuses to build.
    """
    from horseracing_training.calib_split import ArmNotServable

    pred = OofCalibratedPredictor(None, _MASKED, method="isotonic")
    monkeypatch.setattr(
        "horseracing_training.calib_split.dataclasses.fields",
        lambda _obj: [type("F", (), {"name": n})
                      for n in [*pred._RECIPE_FIELD_DISPOSITION, "newly_added_knob"]],
    )
    with pytest.raises(ArmNotServable, match="newly_added_knob"):
        pred._make_base()


def test_every_current_recipe_field_has_a_disposition():
    """States the invariant directly against the real dataclass, so it holds without mocking."""
    actual = {f.name for f in dataclasses.fields(ModelRecipe)}
    declared = set(OofCalibratedPredictor._RECIPE_FIELD_DISPOSITION)
    assert actual == declared, f"undeclared: {actual - declared}, stale: {declared - actual}"
