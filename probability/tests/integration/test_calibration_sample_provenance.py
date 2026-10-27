"""Feature 136: calibration material must prove its provenance, not inherit it from recency.

The pre-136 loaders pick the latest ``PredictionRun`` per race with no predicate but the race
date.  These tests pin the three routes that let material which does not belong into a fit:

  (a) another model generation,
  (b) another input condition (091 ``;wregime=``),
  (c) a historical race re-predicted by a model that trained on it.

They also pin what happens when qualified material runs out: an error, never a quiet identity
fit and never a broader query.

Default behaviour (``provenance=None``) is asserted unchanged, because every production caller
still relies on it.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.models import ModelVersion, PredictionRun, RacePrediction

from horseracing_probability.calibration_provenance import (
    REGIME_ABSENT,
    CalibrationProvenanceError,
    InsufficientQualifiedMaterial,
    MaterialProvenance,
    regime_of_logic_version,
)
from horseracing_probability.model_calibration import (
    fit_product_stage_discount,
    fit_product_stage_discount_strict,
    load_p_samples,
    load_topk_samples,
)
from tests._synth import seed_predicted_race

pytestmark = pytest.mark.integration

D1 = datetime.date(2008, 6, 1)
D2 = datetime.date(2008, 6, 2)
OLD = datetime.datetime(2008, 6, 1, 9, 0, tzinfo=datetime.UTC)
NEW = datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.UTC)


def _add_run(session, *, race_id, model_version, logic_version, win_probs, computed_at):
    """A second run for a race that already exists, with an explicit computed_at."""
    session.merge(ModelVersion(model_version=model_version, model_family="test"))
    session.flush()
    run = PredictionRun(
        race_id=race_id,
        model_version=model_version,
        logic_version=logic_version,
        computed_at=computed_at,
    )
    session.add(run)
    session.flush()
    for hid, wp in win_probs.items():
        d = Decimal(str(wp))
        session.add(
            RacePrediction(
                prediction_run_id=run.prediction_run_id, horse_id=hid,
                win_prob=d, top2_prob=d, top3_prob=d,
            )
        )
    session.commit()
    return run.prediction_run_id


def _seed_race_with_two_runs(session, *, race_id, race_date=D1, newer_model="m2",
                             newer_logic="v1;wregime=full_info"):
    """m1 (serving regime, older) and a newer run the default loader would prefer."""
    seed_predicted_race(
        session, race_id=race_id,
        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
        finish={"H1": 1, "H2": 2, "H3": 3}, race_date=race_date, model_version="m1",
    )
    run = session.query(PredictionRun).filter_by(race_id=race_id).one()
    run.logic_version = "v1;wregime=serving"
    run.computed_at = OLD
    session.commit()
    _add_run(session, race_id=race_id, model_version=newer_model, logic_version=newer_logic,
             win_probs={"H1": 0.1, "H2": 0.1, "H3": 0.8}, computed_at=NEW)


def _p_of(rows, race_id):
    return next(p for rid, _d, p, *_ in rows if rid == race_id)


# --- route (a): another model generation ---------------------------------------------
def test_latest_other_generation_does_not_replace_matching_run(session):
    _seed_race_with_two_runs(session, race_id="200806010101")

    default = load_p_samples(session, date_from=D1, date_to=D2)
    assert _p_of(default, "200806010101")["H3"] == pytest.approx(0.8), (
        "pre-136 behaviour changed: the newer foreign-generation run must still win by default"
    )

    pinned = load_p_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime="serving"),
    )
    # The race is kept and its OWN generation's run is used -- not dropped because a newer
    # ineligible run existed.  That is the point of filtering before the latest-run pick.
    assert _p_of(pinned, "200806010101")["H3"] == pytest.approx(0.2)


def test_generation_filter_applies_to_topk_loader_too(session):
    _seed_race_with_two_runs(session, race_id="200806010101")
    rows = load_topk_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime="serving"),
    )
    assert _p_of(rows, "200806010101")["H3"] == pytest.approx(0.2)


def test_model_version_is_required(session):
    with pytest.raises(CalibrationProvenanceError):
        MaterialProvenance(model_version="")


# --- route (b): another input condition ----------------------------------------------
def test_latest_wrong_input_regime_does_not_replace_matching_run(session):
    # Same generation both times; only the recorded weight regime differs.
    _seed_race_with_two_runs(session, race_id="200806010101", newer_model="m1",
                             newer_logic="v1;wregime=full_info")
    rows = load_p_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime="serving"),
    )
    assert _p_of(rows, "200806010101")["H3"] == pytest.approx(0.2)


def test_missing_regime_marker_is_ineligible_when_a_regime_is_required(session):
    seed_predicted_race(
        session, race_id="200806010101",
        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
        finish={"H1": 1, "H2": 2, "H3": 3}, race_date=D1, model_version="m1",
    )  # logic_version "v1" -- no marker at all
    rows = load_p_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime="serving"),
    )
    assert _p_of(rows, "200806010101") == {}, (
        "a markerless run must not be read as the requested regime"
    )


def test_regime_absent_selects_only_unmarked_runs(session):
    _seed_race_with_two_runs(session, race_id="200806010101", newer_model="m1",
                             newer_logic="v1;wregime=full_info")
    rows = load_p_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime=REGIME_ABSENT),
    )
    assert _p_of(rows, "200806010101") == {}, "both runs carry a marker; neither is 'absent'"


def test_regime_match_is_not_a_prefix_match(session):
    # ";wregime=serving_replay" must not satisfy a request for ";wregime=serving".
    _seed_race_with_two_runs(session, race_id="200806010101", newer_model="m1",
                             newer_logic="v1;wregime=serving_replay")
    run = session.query(PredictionRun).filter_by(logic_version="v1;wregime=serving").one()
    run.logic_version = "v1;wregime=serving_replay"
    session.commit()
    rows = load_p_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime="serving"),
    )
    assert _p_of(rows, "200806010101") == {}


def test_regime_of_logic_version():
    assert regime_of_logic_version("v1;wregime=serving") == "serving"
    assert regime_of_logic_version("v1;wregime=serving;calib=abc") == "serving"
    assert regime_of_logic_version("v1") is None
    assert regime_of_logic_version(None) is None


# --- route (c): in-sample material ----------------------------------------------------
def test_rebuilt_training_race_is_not_oos(session):
    """A race the producer trained on stays out even though race_date < before_date."""
    seed_predicted_race(
        session, race_id="200806010101",
        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
        finish={"H1": 1, "H2": 2, "H3": 3}, race_date=D1, model_version="m1",
    )
    run = session.query(PredictionRun).filter_by(race_id="200806010101").one()
    run.logic_version = "v1;wregime=serving"
    run.computed_at = NEW  # back-filled long after the fact
    session.commit()

    prov = MaterialProvenance(
        model_version="m1", input_regime="serving",
        producer_train_through=datetime.date(2008, 12, 31),
    )
    assert load_p_samples(session, date_from=D1, date_to=D2, provenance=prov) == []


def test_race_on_train_through_is_not_oos(session):
    """Out-of-sample is STRICTLY after the boundary; equality is in-sample."""
    seed_predicted_race(
        session, race_id="200806010101",
        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
        finish={"H1": 1, "H2": 2, "H3": 3}, race_date=D1, model_version="m1",
    )
    run = session.query(PredictionRun).filter_by(race_id="200806010101").one()
    run.logic_version = "v1;wregime=serving"
    session.commit()

    on_boundary = MaterialProvenance(model_version="m1", input_regime="serving",
                                     producer_train_through=D1)
    assert load_p_samples(session, date_from=D1, date_to=D2, provenance=on_boundary) == []

    day_before = MaterialProvenance(model_version="m1", input_regime="serving",
                                    producer_train_through=D1 - datetime.timedelta(days=1))
    assert len(load_p_samples(session, date_from=D1, date_to=D2, provenance=day_before)) == 1


def test_material_as_of_excludes_a_later_computed_run(session):
    """An as-of replay asks which predictions EXISTED then; race dates cannot answer that."""
    _seed_race_with_two_runs(session, race_id="200806010101", newer_model="m1",
                             newer_logic="v1;wregime=serving")
    as_of = MaterialProvenance(
        model_version="m1", input_regime="serving",
        material_as_of=datetime.datetime(2008, 6, 1, 12, 0, tzinfo=datetime.UTC),
    )
    rows = load_p_samples(session, date_from=D1, date_to=D2, provenance=as_of)
    assert _p_of(rows, "200806010101")["H3"] == pytest.approx(0.2)  # the OLD run

    rows_now = load_p_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime="serving"),
    )
    assert _p_of(rows_now, "200806010101")["H3"] == pytest.approx(0.8)  # the NEW run


# --- insufficiency must raise, not degrade -------------------------------------------
def test_strict_fit_requires_explicit_provenance(session):
    with pytest.raises(CalibrationProvenanceError):
        fit_product_stage_discount_strict(session, before_date=D2, provenance=None)


def test_insufficient_qualified_material_errors_instead_of_identity(session):
    """Plenty of rows, none of them qualified: an error, not a quiet identity calibrator."""
    for i in range(1, 6):
        _seed_race_with_two_runs(session, race_id=f"20080601010{i}")

    # The permissive path reports success with an identity fit...
    lenient = fit_product_stage_discount(session, before_date=D2, min_races=3)
    assert lenient.lambda2 == 1.0 and lenient.lambda3 == 1.0

    # ...the strict path refuses, and names what was missing.
    prov = MaterialProvenance(model_version="m1", input_regime="serving",
                              producer_train_through=D1 - datetime.timedelta(days=1))
    with pytest.raises(InsufficientQualifiedMaterial) as excinfo:
        fit_product_stage_discount_strict(session, before_date=D2, provenance=prov, min_races=99)
    assert excinfo.value.n_required == 99
    assert excinfo.value.n_available < 99


def test_strict_fit_rejects_a_fitter_fallback_not_just_a_short_sample(session):
    """Enough qualified races to pass the pre-check, but the fitter still refuses to fit.

    Every race here has a unique winner but no 2nd/3rd finisher, so the stage sample is empty
    while the race sample is not: the pre-check passes and the FITTER is what refuses.

    Note honestly what this does NOT prove.  In the current fitter every ``fallback=True``
    return also carries lambda2 == lambda3 == 1.0, so this test cannot distinguish reading
    ``sd.fallback`` from comparing the lambdas -- a mutation to the lambda comparison survives
    it.  ``test_strict_fit_reads_the_fitters_fallback_flag`` below is the test that kills it.
    """
    from horseracing_db.enums import ResultStatus
    from horseracing_db.models import RaceResult

    for i in range(1, 6):
        rid = f"20080601010{i}"
        seed_predicted_race(
            session, race_id=rid,
            win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
            finish={"H1": 1, "H2": 2, "H3": 3}, race_date=D1, model_version="m1",
        )
        run = session.query(PredictionRun).filter_by(race_id=rid).one()
        run.logic_version = "v1;wregime=serving"
        # keep only the winner as a FINISHED result -> stage 2/3 have nothing to label
        for row in session.query(RaceResult).filter_by(race_id=rid).all():
            if row.finish_order != 1:
                row.result_status = ResultStatus.STOPPED
                row.finish_order = None
        session.commit()

    prov = MaterialProvenance(model_version="m1", input_regime="serving",
                              producer_train_through=D1 - datetime.timedelta(days=1))
    with pytest.raises(InsufficientQualifiedMaterial) as excinfo:
        fit_product_stage_discount_strict(session, before_date=D2, provenance=prov, min_races=1)
    assert "fell back to identity" in str(excinfo.value)


def test_strict_fit_reads_the_fitters_fallback_flag(session, monkeypatch):
    """A refusal with non-identity lambdas must still be a refusal.

    ``fit_stage_discount`` currently happens to leave the lambdas at 1.0 whenever it sets
    ``fallback``, which makes the two signals indistinguishable on real data.  They are not
    the same contract: ``fallback`` is the fitter SAYING it declined, the lambdas are a value
    that a successful fit could in principle also produce.  This pins the wrapper to the flag,
    so a fitter that ever reports a declined fit with the last attempted lambdas cannot be
    read as a successful calibration.
    """
    from horseracing_eval.stage_discount import StageDiscount

    for i in range(1, 6):
        rid = f"20080601010{i}"
        seed_predicted_race(
            session, race_id=rid,
            win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
            finish={"H1": 1, "H2": 2, "H3": 3}, race_date=D1, model_version="m1",
        )
        run = session.query(PredictionRun).filter_by(race_id=rid).one()
        run.logic_version = "v1;wregime=serving"
    session.commit()

    declined = StageDiscount(lambda2=0.83, lambda3=0.71, n_races_l2=4, n_races_l3=4,
                             fallback=True)
    monkeypatch.setattr(
        "horseracing_eval.stage_discount.fit_stage_discount", lambda *a, **k: declined
    )
    prov = MaterialProvenance(model_version="m1", input_regime="serving",
                              producer_train_through=D1 - datetime.timedelta(days=1))
    with pytest.raises(InsufficientQualifiedMaterial) as excinfo:
        fit_product_stage_discount_strict(session, before_date=D2, provenance=prov, min_races=1)
    assert "fell back to identity" in str(excinfo.value)
    assert excinfo.value.n_available == 4


def test_strict_fit_does_not_widen_after_a_restricted_query_comes_up_short(session):
    """No fallback to another generation: the foreign rows exist and stay unused."""
    for i in range(1, 6):
        _seed_race_with_two_runs(session, race_id=f"20080601010{i}")
    absent = MaterialProvenance(model_version="does-not-exist", input_regime="serving",
                                producer_train_through=D1 - datetime.timedelta(days=1))
    with pytest.raises(InsufficientQualifiedMaterial) as excinfo:
        fit_product_stage_discount_strict(session, before_date=D2, provenance=absent, min_races=1)
    assert excinfo.value.n_available == 0


# --- the default path is unchanged ----------------------------------------------------
def test_default_path_is_byte_identical_without_provenance(session):
    _seed_race_with_two_runs(session, race_id="200806010101")
    seed_predicted_race(
        session, race_id="200806010102",
        win_probs={"H1": 0.6, "H2": 0.4}, finish={"H1": 1, "H2": 2},
        race_date=D1, model_version="m1",
    )
    assert load_p_samples(session, date_from=D1, date_to=D2) == load_p_samples(
        session, date_from=D1, date_to=D2, provenance=None
    )
    assert load_topk_samples(session, date_from=D1, date_to=D2) == load_topk_samples(
        session, date_from=D1, date_to=D2, provenance=None
    )


# --- gaps closed after the independent review --------------------------------------
def test_generation_alone_decides_when_the_regime_is_identical(session):
    """Isolate the generation predicate: both runs carry the SAME regime marker.

    The two-runs fixture changes generation AND regime at once, so the regime filter alone
    would satisfy it. Here only the model version differs, so dropping the model-version
    predicate makes the newer foreign run win.
    """
    _seed_race_with_two_runs(session, race_id="200806010101", newer_model="m2",
                             newer_logic="v1;wregime=serving")
    rows = load_p_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime="serving"),
    )
    assert _p_of(rows, "200806010101")["H3"] == pytest.approx(0.2)
    rows_topk = load_topk_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime="serving"),
    )
    assert _p_of(rows_topk, "200806010101")["H3"] == pytest.approx(0.2)


def test_regime_absent_positively_selects_an_unmarked_run(session):
    """The REGIME_ABSENT branch must SELECT, not only reject.

    Without this, an implementation that rejected every run would pass the negative test.
    """
    _seed_race_with_two_runs(session, race_id="200806010101", newer_model="m1",
                             newer_logic="v1")  # newer run has NO marker
    rows = load_p_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime=REGIME_ABSENT),
    )
    assert _p_of(rows, "200806010101")["H3"] == pytest.approx(0.8)


def test_regime_matches_when_another_segment_follows_it(session):
    """The non-trailing arm of the SQL predicate, exercised through the database.

    Serving appends ``;wregime=`` last today, but nothing in the schema guarantees that.
    Removing the ``contains(";wregime=<r>;")`` arm must fail here.
    """
    _seed_race_with_two_runs(session, race_id="200806010101", newer_model="m1",
                             newer_logic="v1;wregime=serving;calib=abcdef")
    run = session.query(PredictionRun).filter_by(logic_version="v1;wregime=serving").one()
    run.logic_version = "v1;wregime=full_info"  # the old run is now the ineligible one
    session.commit()
    rows = load_p_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime="serving"),
    )
    assert _p_of(rows, "200806010101")["H3"] == pytest.approx(0.8)


def test_regime_literal_is_escaped_not_a_like_pattern(session):
    """``_`` in "full_info" must be a literal underscore, not a LIKE wildcard.

    Without ``autoescape=True`` the predicate compiles to ``LIKE '%;wregime=full_info'`` and
    also matches ``fullXinfo``.
    """
    _seed_race_with_two_runs(session, race_id="200806010101", newer_model="m1",
                             newer_logic="v1;wregime=fullXinfo")
    rows = load_p_samples(
        session, date_from=D1, date_to=D2,
        provenance=MaterialProvenance(model_version="m1", input_regime="full_info"),
    )
    assert _p_of(rows, "200806010101") == {}, "LIKE wildcard leaked into the regime literal"


def test_default_path_equals_the_pre_bulk_per_race_reference(session):
    """Parity against the ORIGINAL per-race loader, not merely against ``provenance=None``.

    Comparing the two argument spellings would pass even if both had regressed together.
    ``_latest_run_predictions``/``_winner`` is the implementation the bulk loader replaced.
    """
    from horseracing_db.models import Race
    from sqlalchemy import select

    from horseracing_probability.model_calibration import _latest_run_predictions, _winner

    _seed_race_with_two_runs(session, race_id="200806010101")
    seed_predicted_race(
        session, race_id="200806010102",
        win_probs={"H1": 0.6, "H2": 0.4}, finish={"H1": 1, "H2": 2},
        race_date=D1, model_version="m1",
    )
    reference = [
        (rid, d, _latest_run_predictions(session, rid), *_winner(session, rid))
        for rid, d in session.execute(
            select(Race.race_id, Race.race_date)
            .where(Race.race_date >= D1).where(Race.race_date <= D2)
            .order_by(Race.race_date, Race.race_id)
        ).all()
    ]
    assert load_p_samples(session, date_from=D1, date_to=D2) == reference


def test_strict_fit_succeeds_and_reports_what_it_enforced(session):
    """The success path exists and its audit is honest about population sizes."""
    from horseracing_eval.stage_discount import StageDiscount

    for i in range(1, 6):
        rid = f"20080601010{i}"
        seed_predicted_race(
            session, race_id=rid,
            win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
            finish={"H1": 1, "H2": 2, "H3": 3}, race_date=D1, model_version="m1",
        )
        run = session.query(PredictionRun).filter_by(race_id=rid).one()
        run.logic_version = "v1;wregime=serving"
    # a race in the window with NO eligible prediction: it must not be counted as qualified
    seed_predicted_race(
        session, race_id="200806010109",
        win_probs={"H1": 0.5, "H2": 0.5}, finish={"H1": 1, "H2": 2},
        race_date=D1, model_version="m2",
    )
    session.commit()

    fitted = StageDiscount(lambda2=0.86, lambda3=0.72, n_races_l2=5, n_races_l3=5)
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    try:
        mp.setattr("horseracing_eval.stage_discount.fit_stage_discount", lambda *a, **k: fitted)
        prov = MaterialProvenance(model_version="m1", input_regime="serving",
                                  producer_train_through=D1 - datetime.timedelta(days=1))
        sd, audit = fit_product_stage_discount_strict(
            session, before_date=D2, provenance=prov, min_races=1
        )
    finally:
        mp.undo()
    assert (sd.lambda2, sd.lambda3) == (0.86, 0.72)
    assert audit["n_races_in_window"] == 6
    assert audit["n_qualified_races"] == 5, "a race with no eligible run must not count"
    assert audit["provenance"]["input_regime"] == "serving"
    assert audit["provenance"]["producer_train_through"] == str(D1 - datetime.timedelta(days=1))


def test_incomplete_contract_is_refused_unless_deliberately_waived(session):
    """A provenance stating neither regime nor training boundary disables both guards."""
    seed_predicted_race(
        session, race_id="200806010101",
        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
        finish={"H1": 1, "H2": 2, "H3": 3}, race_date=D1, model_version="m1",
    )
    bare = MaterialProvenance(model_version="m1")
    with pytest.raises(CalibrationProvenanceError) as excinfo:
        fit_product_stage_discount_strict(session, before_date=D2, provenance=bare, min_races=1)
    assert "input_regime" in str(excinfo.value)
    assert "producer_train_through" in str(excinfo.value)
    # the waiver reaches the material check instead of being refused up front
    with pytest.raises(InsufficientQualifiedMaterial):
        fit_product_stage_discount_strict(
            session, before_date=D2, provenance=bare, min_races=1, require_complete=False
        )


def test_datetime_training_boundary_and_naive_as_of_are_refused():
    """Mixed date/timestamp comparisons would depend on the session timezone."""
    with pytest.raises(CalibrationProvenanceError):
        MaterialProvenance(model_version="m1",
                           producer_train_through=datetime.datetime(2008, 6, 1, 12, 0))
    with pytest.raises(CalibrationProvenanceError):
        MaterialProvenance(model_version="m1",
                           material_as_of=datetime.datetime(2008, 6, 1, 12, 0))
    with pytest.raises(CalibrationProvenanceError):
        MaterialProvenance(model_version="m1", material_as_of=D1)
