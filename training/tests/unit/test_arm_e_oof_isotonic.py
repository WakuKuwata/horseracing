"""Feature 085 arm E: full-history booster + strict-past OOF isotonic (spec §10).

The tests use a stub inner booster so the OOF sample construction, label population, score
space, sufficiency rules and state resets are exercised without training LightGBM.
"""

from __future__ import annotations

import datetime

import numpy as np
import pytest
from horseracing_eval.predictor import HorseEntry, RaceContext

from horseracing_training import calib_split as mod
from horseracing_training.calib_split import (
    CalibSplitFactory,
    InsufficientOofSample,
    OofCalibratedPredictor,
)
from horseracing_training.recipe import ModelRecipe

DAY0 = datetime.date(2025, 1, 1)


def _ctx(rid: str, day_offset: int, horses: list[str]) -> RaceContext:
    return RaceContext(
        race_id=rid,
        race_date=DAY0 + datetime.timedelta(days=day_offset),
        started_horses=tuple(HorseEntry(horse_id=h) for h in horses),
    )


def _races(n_days: int, per_day: int = 3, field: int = 4) -> list[RaceContext]:
    out = []
    for d in range(n_days):
        for r in range(per_day):
            rid = f"D{d:02d}R{r}"
            out.append(_ctx(rid, d, [f"{rid}h{i}" for i in range(field)]))
    return out


class _StubBase:
    """Stands in for LightGBMPredictor: records what it was fit on, returns fixed raw scores."""

    def __init__(self, sink: list):
        self.sink = sink
        self.trained_dates: list[datetime.date] = []

    def fit(self, races):
        self.trained_dates = [r.race_date for r in races]
        self.sink.append(list(self.trained_dates))
        return self

    def raw_win_probs(self, race: RaceContext):
        ids = [h.horse_id for h in race.started_horses]
        n = len(ids)
        # deterministic, race-normalized, distinct per position (a plausible softmax vector)
        w = np.asarray([(i + 1) for i in range(n)], dtype=float)
        return ids, w / w.sum()

    def predict_race(self, race: RaceContext):  # pragma: no cover - power path not used here
        raise AssertionError("arm E must not call predict_race for OOF samples")


def _make_pred(races, outcomes, *, require_sufficient=False, monkeypatch=None, n_oof=3):
    """OofCalibratedPredictor wired to stub boosters + a stub outcome loader."""
    fits: list = []
    pred = OofCalibratedPredictor(
        None, ModelRecipe(), method="isotonic",
        require_sufficient=require_sufficient, n_oof_blocks=n_oof,
    )
    pred._fits = fits  # type: ignore[attr-defined]
    monkeypatch.setattr(pred, "_make_base", lambda: _StubBase(fits))
    monkeypatch.setattr(mod, "_started_all_outcomes", lambda session, rids: outcomes)
    return pred


def _complete_outcomes(races, *, winners_per_race=1):
    """(n_result_rows, winner set) with full coverage and `winners_per_race` first places."""
    out = {}
    for ctx in races:
        ids = [h.horse_id for h in ctx.started_horses]
        out[ctx.race_id] = (len(ids), set(ids[:winners_per_race]))
    return out


# --- score space (§3.1) -----------------------------------------------------


def test_oof_rows_use_raw_softmax_not_predict_race(monkeypatch):
    """The stub raises if predict_race is used: arm E must fit on the UNCALIBRATED vector that
    serving's calibrator receives, not on post-clip/post-renormalization probabilities."""
    races = _races(12)
    pred = _make_pred(races, _complete_outcomes(races), monkeypatch=monkeypatch)
    scores, labels, info = pred._oof_isotonic_rows(races)
    assert len(scores) == len(labels) > 0
    assert info["n_oof_races"] > 0
    # stub returns 0.1/0.2/0.3/0.4 for a 4-horse race
    assert set(np.round(np.unique(scores), 6)) == {0.1, 0.2, 0.3, 0.4}


def test_inner_boosters_train_strictly_before_their_prediction_block(monkeypatch):
    races = _races(12)
    outcomes = _complete_outcomes(races)
    pred = _make_pred(races, outcomes, monkeypatch=monkeypatch)

    seen: list[tuple[list[datetime.date], datetime.date]] = []
    real = _StubBase.raw_win_probs

    def spy(self, race):
        seen.append((list(self.trained_dates), race.race_date))
        return real(self, race)

    monkeypatch.setattr(_StubBase, "raw_win_probs", spy)
    pred._oof_isotonic_rows(races)
    assert seen
    for trained, target in seen:
        assert max(trained) < target  # strict-past, same-day excluded


# --- label population (§3.2) ------------------------------------------------


def test_every_started_horse_emits_exactly_one_row_with_one_positive(monkeypatch):
    races = _races(12, per_day=2, field=5)
    pred = _make_pred(races, _complete_outcomes(races), monkeypatch=monkeypatch)
    scores, labels, info = pred._oof_isotonic_rows(races)
    assert len(scores) == info["n_oof_races"] * 5
    assert int(labels.sum()) == info["n_oof_races"]  # exactly one winner per race


def test_dead_heat_race_is_kept_with_both_winners_positive(monkeypatch):
    races = _races(12, per_day=2, field=4)
    outcomes = _complete_outcomes(races, winners_per_race=2)  # every race a dead heat
    pred = _make_pred(races, outcomes, monkeypatch=monkeypatch)
    scores, labels, info = pred._oof_isotonic_rows(races)
    assert info["n_oof_races"] > 0  # NOT dropped (unlike _single_winners)
    assert info["n_dead_heat_races"] == info["n_oof_races"]
    assert int(labels.sum()) == 2 * info["n_oof_races"]


def test_partial_result_coverage_is_excluded_not_labelled_all_zero(monkeypatch):
    races = _races(12, per_day=2, field=4)
    outcomes = _complete_outcomes(races)
    # starve one race: fewer result rows than started horses (partial ingest)
    victim = races[-1].race_id
    n, w = outcomes[victim]
    outcomes[victim] = (n - 1, w)
    pred = _make_pred(races, outcomes, monkeypatch=monkeypatch)
    _s, _y, info = pred._oof_isotonic_rows(races)
    assert info["n_incomplete_races"] >= 1
    full = _make_pred(races, _complete_outcomes(races), monkeypatch=monkeypatch)
    _s2, _y2, info2 = full._oof_isotonic_rows(races)
    assert info["n_oof_races"] == info2["n_oof_races"] - 1


def test_race_without_results_or_without_a_winner_is_excluded(monkeypatch):
    races = _races(12, per_day=2, field=4)
    outcomes = _complete_outcomes(races)
    missing = races[-1].race_id
    del outcomes[missing]                                   # no result rows at all
    no_winner = races[-2].race_id
    outcomes[no_winner] = (4, set())                        # rows but no finished 1st place
    pred = _make_pred(races, outcomes, monkeypatch=monkeypatch)
    _s, _y, info = pred._oof_isotonic_rows(races)
    assert info["n_incomplete_races"] >= 2


def test_prediction_started_mismatch_is_a_contract_error(monkeypatch):
    races = _races(12, per_day=2, field=4)
    pred = _make_pred(races, _complete_outcomes(races), monkeypatch=monkeypatch)

    def wrong(self, race):
        ids = [h.horse_id for h in race.started_horses][:-1]  # drop a horse
        return ids, np.full(len(ids), 1.0 / len(ids))

    monkeypatch.setattr(_StubBase, "raw_win_probs", wrong)
    with pytest.raises(RuntimeError, match="mismatch or non-finite"):
        pred._oof_isotonic_rows(races)


# --- sufficiency (§3.3) -----------------------------------------------------


def test_insufficient_sample_falls_back_to_identity_with_a_reason(monkeypatch):
    races = _races(12, per_day=1, field=4)  # far below MIN_OOF_RACES
    pred = _make_pred(races, _complete_outcomes(races), monkeypatch=monkeypatch)
    monkeypatch.setattr(pred, "_make_base", lambda: _StubBase(pred._fits))
    pred._base = _StubBase(pred._fits)
    pred._fit_oof_isotonic(races)
    assert pred.calibrator_ is None
    assert pred.oof_info_["sufficient"] is False
    assert pred.oof_info_["reason"].startswith("too_few_oof_races")


def test_insufficient_sample_fails_closed_when_strict(monkeypatch):
    races = _races(12, per_day=1, field=4)
    pred = _make_pred(
        races, _complete_outcomes(races), require_sufficient=True, monkeypatch=monkeypatch
    )
    with pytest.raises(InsufficientOofSample, match="too_few_oof_races"):
        pred._fit_oof_isotonic(races)


def test_sufficiency_floors_are_pre_registered_constants():
    # spec §3.3: fixed BEFORE any OOS run; a change is a new pre-registration.
    assert (mod.MIN_OOF_RACES, mod.MIN_OOF_ROWS) == (200, 2_000)
    assert (mod.MIN_OOF_POSITIVES, mod.MIN_OOF_DISTINCT_SCORES) == (200, 2)


def test_state_is_reset_so_a_previous_folds_isotonic_never_survives(monkeypatch):
    races = _races(12, per_day=1, field=4)
    pred = _make_pred(races, _complete_outcomes(races), monkeypatch=monkeypatch)
    # pretend an earlier fold fitted something
    import numpy as _np

    from horseracing_training.calibration import fit_calibrator
    pred.calibrator_ = fit_calibrator(
        _np.linspace(0.05, 0.95, 40), (_np.arange(40) % 4 == 0).astype(int), method="isotonic"
    )
    pred.n_oof_samples_ = 999
    pred.gamma_ = 1.5
    monkeypatch.setattr(pred, "_oof_isotonic_rows", lambda r: (np.empty(0), np.empty(0), {
        "n_oof_races": 0, "n_oof_rows": 0, "n_dead_heat_races": 0, "n_incomplete_races": 0,
    }))
    pred.fit(races)
    assert pred.calibrator_ is None
    assert pred.n_oof_samples_ == 0
    assert pred.gamma_ == 1.0
    assert pred.oof_info_["sufficient"] is False


# --- prediction path (§3.1 tail) --------------------------------------------


def test_predictions_sum_to_one_and_never_invert_rank(monkeypatch):
    # sized to clear every pre-registered floor: 40 OOF days x 9 races = 360 races,
    # 360 x 6 horses = 2,160 rows, 360 positives
    races = _races(60, per_day=9, field=6)
    pred = _make_pred(races, _complete_outcomes(races), monkeypatch=monkeypatch)
    pred._base = _StubBase(pred._fits)
    pred._fit_oof_isotonic(races)
    assert pred.oof_info_["sufficient"] is True, pred.oof_info_
    target = races[-1]
    ids, raw = pred._base.raw_win_probs(target)
    out = pred.predict_race(target)
    wins = np.asarray([out[h].win for h in ids])
    assert np.isclose(wins.sum(), 1.0)
    assert np.isfinite(wins).all()
    # isotonic is monotone and the renormalizer is a positive scalar -> no inversion
    for i in range(len(ids)):
        for j in range(len(ids)):
            if raw[i] < raw[j]:
                assert wins[i] <= wins[j] + 1e-12


# --- arm identity (§3.5) ----------------------------------------------------


def test_power_and_isotonic_arms_have_distinct_recipe_hashes():
    p = CalibSplitFactory(None, ModelRecipe(label="pl_topk:oof_power"), method="power")
    e = CalibSplitFactory(None, ModelRecipe(label="pl_topk:oof_isotonic"), method="isotonic")
    assert p.recipe_meta["arm"] == "oof_power"
    assert e.recipe_meta["arm"] == "oof_isotonic"
    assert p.recipe_hash != e.recipe_hash


def test_unknown_oof_method_is_rejected():
    with pytest.raises(ValueError, match="unknown OOF calibrator method"):
        OofCalibratedPredictor(None, ModelRecipe(), method="oof_isotonic")


def test_cli_routes_both_oof_specs_and_falls_back_for_holdout_arms():
    from horseracing_training.cli import _factory_from_spec
    from horseracing_training.recipe import RecipeFactory

    power = _factory_from_spec(None, "pl_topk:oof_power")
    iso = _factory_from_spec(None, "pl_topk:oof_isotonic")
    holdout = _factory_from_spec(None, "pl_topk:isotonic:0.3")
    assert isinstance(power, CalibSplitFactory) and power.method == "power"
    assert isinstance(iso, CalibSplitFactory) and iso.method == "isotonic"
    assert isinstance(holdout, RecipeFactory)
    assert power.recipe_hash != iso.recipe_hash


# --- 085 §5: shipping path -----------------------------------------------------------------------

def test_to_servable_refuses_an_unfitted_calibrator():
    """An insufficient OOF sample leaves an identity calibrator. Shipping THAT under the name
    strict_past_oof_isotonic_v1 would register an uncalibrated model as a calibrated one."""
    from horseracing_training.calib_split import ArmNotServable, OofCalibratedPredictor

    p = OofCalibratedPredictor.__new__(OofCalibratedPredictor)
    p.method = "isotonic"
    p._base = object()
    p.calibrator_ = object()
    p.oof_info_ = {"sufficient": False, "reason": "too_few_oof_races(3<50)"}
    with pytest.raises(ArmNotServable, match="unfitted calibrator"):
        p.to_servable()


def test_to_servable_rewrites_the_calibration_record_truthfully():
    """_make_base builds the booster with calibration="none" (correct — it carries no calibrator).
    Shipping that unchanged would record "no calibration" beside a fitted isotonic in
    calibrator.pkl. Nothing in serving reads the field, but the registry and auditors do."""
    from horseracing_training.calib_split import OofCalibratedPredictor

    class _Cal:
        identity = False

        def params_dict(self):
            return {"thresholds": [0.1, 0.5], "values": [0.2, 0.7]}

    class _Base:
        fit_info_ = {"calibration": "none", "calibration_split_unit": "race_count_v1",
                     "calib_from": "2020-01-01", "calib_through": "2026-07-12", "n_calib_rows": 0}
        calibrator_ = None

    p = OofCalibratedPredictor.__new__(OofCalibratedPredictor)
    p.method, p._base, p.calibrator_ = "isotonic", _Base(), _Cal()
    p.n_oof, p.n_oof_samples_ = 8, 12345
    p.oof_info_ = {"sufficient": True, "n_oof_rows": 12345, "n_oof_races": 900,
                   "n_positives": 900, "n_distinct_scores": 5000}

    out = p.to_servable()
    info = out.fit_info_
    assert info["calibration"] == "isotonic_strict_past_oof"
    assert info["calibration_split_unit"] is None
    assert info["calib_from"] is None and info["calib_through"] is None
    assert info["n_calib_rows"] == 12345
    proto = info["calibration_protocol"]
    assert proto["protocol"] == "strict_past_oof_isotonic_v1"
    assert proto["booster_calib_frac"] == 0.0
    assert proto["score_space"] == "raw_race_softmax"
    assert len(proto["threshold_checksum"]) == 64
    assert out.calibrator_ is p.calibrator_


def test_split_unit_guard_blocks_overwriting_a_holdout_model_with_a_protocol_model():
    """arm E has split_unit=None, which the guard maps to the legacy default. Without the protocol
    being considered, an OOF-calibrated model could silently overwrite a 70/30 holdout model under
    the same model_version — the exact substitution the guard exists to stop."""
    from horseracing_training.artifacts import assert_split_unit_compatible

    assert_split_unit_compatible("race_count_v1", "race_count_v1", model_version="m")  # no-op
    with pytest.raises(ValueError, match="must use a new"):
        assert_split_unit_compatible(
            "race_count_v1", None, model_version="m",
            new_protocol="strict_past_oof_isotonic_v1",
        )


def test_oof_span_covers_the_predicted_blocks_not_the_fitting_days():
    """085 §5 wants "which races produced the calibration sample" answerable from the shipped
    provenance. The span must be the PREDICTED blocks; recording the fitting days instead would
    claim the sample reaches back further than it does."""
    import datetime as _dt

    from horseracing_training.calib_split import OofCalibratedPredictor

    info = {"n_oof_races": 0, "oof_pred_from": None, "oof_pred_through": None}

    def _seen(day: str) -> None:
        if info["oof_pred_from"] is None or day < info["oof_pred_from"]:
            info["oof_pred_from"] = day
        if info["oof_pred_through"] is None or day > info["oof_pred_through"]:
            info["oof_pred_through"] = day

    # blocks arrive out of order; the span must still be min/max, not first/last
    for d in ("2021-06-01", "2020-01-05", "2026-07-12", "2023-03-03"):
        _seen(d)
    assert info["oof_pred_from"] == "2020-01-05"
    assert info["oof_pred_through"] == "2026-07-12"
    assert _dt.date.fromisoformat(info["oof_pred_from"]) < _dt.date.fromisoformat(
        info["oof_pred_through"]
    )
    assert hasattr(OofCalibratedPredictor, "to_servable")
