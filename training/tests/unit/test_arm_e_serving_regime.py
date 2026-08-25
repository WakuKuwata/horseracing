"""085 x 091: arm E must be *measurable* under the serving regime, and refuse what it cannot honour.

Three defects found in the 2026-08-25 arm E audit are pinned here.

1. `OofCalibratedPredictor` exposed neither `set_predict_weight_mask` nor `raw_win_probs`, so
   `eval.foldfit.predict_over_folds_multi` failed closed (`RegimeUnsupported`) for every
   non-default regime. Failing closed was correct — a silently ignored mask would have labelled a
   full-information comparison "serving" — but it meant arm E could never be scored on the input
   path serving actually takes (~97% of live races publish no same-day weight).

2. The hook must NOT reach the OOF calibration sample. That the isotonic is fitted on
   full-information scores while the booster trains on a masked mixture is a real open question,
   but changing it silently would redefine the arm and orphan the models registered under it.
   The boundary is therefore asserted, not assumed.

3. Recipe fields that change the booster but are not wired here (`ev_weight`) were labelled
   "not-applicable" and silently ignored.
"""

from __future__ import annotations

import datetime

import numpy as np
import pytest
from horseracing_eval.predictor import HorseEntry, RaceContext

from horseracing_training import calib_split as mod
from horseracing_training.calib_split import ArmNotServable, OofCalibratedPredictor
from horseracing_training.recipe import ModelRecipe

DAY0 = datetime.date(2025, 1, 1)
SPEC = object()  # opaque to eval and to this test, exactly as the real spec is to foldfit


def _races(n_days: int, per_day: int = 3, field: int = 4) -> list[RaceContext]:
    out = []
    for d in range(n_days):
        for r in range(per_day):
            rid = f"D{d:02d}R{r}"
            out.append(
                RaceContext(
                    race_id=rid,
                    race_date=DAY0 + datetime.timedelta(days=d),
                    started_horses=tuple(
                        HorseEntry(horse_id=f"{rid}h{i}") for i in range(field)
                    ),
                )
            )
    return out


class _StubBase:
    """Records the predict regime it was handed, like LightGBMPredictor does."""

    def __init__(self, registry: list):
        self.predict_weight_mask = None
        registry.append(self)

    def fit(self, races):
        return self

    def set_predict_weight_mask(self, spec):
        self.predict_weight_mask = spec

    def raw_win_probs(self, race: RaceContext):
        ids = [h.horse_id for h in race.started_horses]
        w = np.asarray([i + 1 for i in range(len(ids))], dtype=float)
        return ids, w / w.sum()


def _pred(monkeypatch, races):
    """arm E wired to stub boosters; returns (predictor, list-of-created-stubs)."""
    made: list[_StubBase] = []
    p = OofCalibratedPredictor(
        None, ModelRecipe(), method="isotonic", require_sufficient=False, n_oof_blocks=3
    )
    monkeypatch.setattr(p, "_make_base", lambda: _StubBase(made))
    monkeypatch.setattr(
        mod, "_started_all_outcomes",
        lambda session, rids: {
            c.race_id: (len(c.started_horses), {c.started_horses[0].horse_id}) for c in races
        },
    )
    return p, made


def test_foldfit_regime_contract_is_satisfied():
    """`predict_over_folds_multi` probes both by `hasattr`; absence = RegimeUnsupported."""
    for hook in ("set_predict_weight_mask", "raw_win_probs"):
        assert hasattr(OofCalibratedPredictor, hook), f"arm E lost the {hook} hook"


def test_regime_set_after_fit_reaches_the_booster(monkeypatch):
    races = _races(12)
    p, made = _pred(monkeypatch, races)
    p.fit(races)
    p.set_predict_weight_mask(SPEC)
    assert p._base.predict_weight_mask is SPEC


def test_regime_set_before_fit_survives_the_refit(monkeypatch):
    """The factory rebuilds `_base` every outer fold, so a regime set once up front would
    otherwise be silently dropped from the second fold onwards."""
    races = _races(12)
    p, made = _pred(monkeypatch, races)
    p.set_predict_weight_mask(SPEC)
    p.fit(races)
    assert p._base.predict_weight_mask is SPEC


def test_clearing_the_regime_restores_full_information(monkeypatch):
    races = _races(12)
    p, _ = _pred(monkeypatch, races)
    p.fit(races)
    p.set_predict_weight_mask(SPEC)
    p.set_predict_weight_mask(None)
    assert p._base.predict_weight_mask is None


def test_the_oof_calibration_sample_stays_full_information(monkeypatch):
    """THE boundary. The inner boosters that produce the calibration rows must never be handed
    the predict regime: doing so would change what the isotonic is fitted on, i.e. redefine the
    arm, and the models already registered under it would no longer be reproducible."""
    races = _races(12)
    p, made = _pred(monkeypatch, races)
    p.set_predict_weight_mask(SPEC)
    p.fit(races)

    inner = [b for b in made if b is not p._base]
    assert inner, "expected inner OOF boosters to have been built"
    assert all(b.predict_weight_mask is None for b in inner), (
        "an inner OOF booster received the predict regime: the isotonic would be fitted on a "
        "different score distribution than the arm was measured with"
    )


def test_raw_win_probs_delegates_to_the_booster(monkeypatch):
    """091's calibration-reversal diagnostic: raw (uncalibrated) scores must be reachable."""
    races = _races(12)
    p, _ = _pred(monkeypatch, races)
    p.fit(races)
    ids, raw = p.raw_win_probs(races[0])
    assert ids == [h.horse_id for h in races[0].started_horses]
    assert raw == pytest.approx([0.1, 0.2, 0.3, 0.4])


def test_a_recipe_field_that_changes_the_booster_but_is_unwired_is_refused(monkeypatch):
    """`ev_weight` alters the fit (per-race weights) but its frozen OOF-p source never reaches
    this builder, so it was accepted and ignored. Silent divergence between the recipe and the
    model it names is the exact defect `_RECIPE_FIELD_DISPOSITION` exists to prevent."""
    recipe = ModelRecipe(objective="pl_topk", ev_weight=True)
    p = OofCalibratedPredictor(None, recipe, method="isotonic")
    with pytest.raises(ArmNotServable, match="ev_weight"):
        p._make_base()


def test_rejected_fields_are_declared_as_such():
    """Keeps the vocabulary honest: a field that changes the booster is never 'not-applicable'."""
    disposition = OofCalibratedPredictor._RECIPE_FIELD_DISPOSITION
    for name in OofCalibratedPredictor._REJECTED_FIELD_DEFAULTS:
        assert disposition[name] == "reject", f"{name} must be declared 'reject'"
