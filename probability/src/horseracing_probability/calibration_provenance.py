"""Feature 136: a provenance contract for calibration MATERIAL.

Why
---
``load_p_samples`` / ``load_topk_samples`` pick, for every race in a date window, the
*latest* stored ``PredictionRun``.  The only predicate is the race date.  That leaves three
routes by which material that does not belong can enter a fit:

  (a) **another model generation** -- a newer run of model B displaces model A's run;
  (b) **another input condition** -- a newer full-information run displaces the
      serving/pre-publication run the fit is supposed to describe (feature 091 records the
      condition in ``logic_version`` as ``;wregime=serving|full_info``);
  (c) **in-sample material** -- a historical race re-predicted afterwards by a model that
      *trained on that race* still satisfies ``race_date < before_date``, so a backfill can
      turn training races into calibration material.

``race_date < application_date`` does not establish (c): it constrains the race, not the
producer.  The producer's own training boundary does.

What this module adds
---------------------
A :class:`MaterialProvenance` the caller must state explicitly, and predicates that are
applied **before** the latest-run selection -- so an ineligible newer run is never chosen
in place of an eligible older one, rather than being filtered out after it has already won.

Insufficient qualified material raises :class:`InsufficientQualifiedMaterial`.  It never
falls back to a broader query, another generation, another regime, or an identity
calibrator presented as a successful fit.  The pure fitters keep their identity fallback
for the research callers that rely on it; the strict path inspects the result and raises.

Not wired into production
-------------------------
Every consumer keeps ``provenance=None`` by default, which reproduces the pre-136 queries
exactly.  Turning a production path on is a separate, deliberate change; feature 133's
frozen display lambda is not affected.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from horseracing_db.models import PredictionRun

__all__ = [
    "MaterialProvenance",
    "CalibrationProvenanceError",
    "InsufficientQualifiedMaterial",
    "REGIME_ABSENT",
    "regime_of_logic_version",
]

#: Sentinel for "this model has no weight-regime distinction, and the absence is expected".
#: Feature 091 omits the marker entirely for models without ``prev_weight``; requiring it
#: would make their material permanently ineligible.  Asking for ``REGIME_ABSENT`` selects
#: exactly the runs that carry no marker -- it never matches a marked run.
REGIME_ABSENT = "__absent__"

_MARKER = ";wregime="


class CalibrationProvenanceError(ValueError):
    """The requested material cannot be shown to satisfy the provenance contract."""


class InsufficientQualifiedMaterial(CalibrationProvenanceError):
    """Qualified material exists but is too small to fit on.

    Deliberately distinct from a plain shortage of rows: the caller must not react by
    widening the query.  There is no broader query that is still the same estimand.
    """

    def __init__(self, what: str, *, n_available: int, n_required: int, shortage: bool = True):
        detail = (
            f"{n_available} qualified races < {n_required} required"
            if shortage
            else f"{n_available} qualified races, minimum {n_required} "
            "(the fitter declined for its own reason, not only for a shortage)"
        )
        super().__init__(
            f"{what}: {detail}; "
            "refusing to widen the material or to report an identity fit as a calibration"
        )
        self.what = what
        self.n_available = n_available
        self.n_required = n_required
        self.shortage = shortage


def regime_of_logic_version(logic_version: str | None) -> str | None:
    """The 091 weight regime recorded in a ``logic_version``, or ``None`` when absent.

    ``None`` means *unknown or not applicable* -- it is never silently read as the regime
    the caller asked for.
    """
    if not logic_version or _MARKER not in logic_version:
        return None
    return logic_version.split(_MARKER, 1)[1].split(";", 1)[0] or None


@dataclass(frozen=True)
class MaterialProvenance:
    """What the caller asserts about the predictions it is willing to calibrate on.

    Attributes
    ----------
    model_version:
        Exact producing model version.  Matching a version *string* is weaker than
        matching an artifact digest -- the database has no per-run artifact field -- so
        this is a necessary, not a sufficient, generation check.  Stated here so the
        limitation is visible at the call site rather than assumed away.
    input_regime:
        ``"serving"`` / ``"full_info"`` (feature 091), or :data:`REGIME_ABSENT` for models
        that record no marker.  ``None`` means the caller declines to constrain the input
        condition, and mixed conditions may enter the fit.
    producer_train_through:
        Last race date the producing pipeline (booster **and** its learned calibrator)
        was trained on.  Only races strictly after it are treated as out-of-sample.
    material_as_of:
        Upper bound on ``computed_at``.  Needed for an as-of replay: it answers "which
        predictions existed at that moment", which race dates alone cannot.
    min_races:
        Minimum qualified races.  Fewer raises :class:`InsufficientQualifiedMaterial`.
    """

    model_version: str
    input_regime: str | None = None
    producer_train_through: dt.date | None = None
    material_as_of: dt.datetime | None = None
    min_races: int = 1

    def __post_init__(self) -> None:
        if not self.model_version or not self.model_version.strip():
            raise CalibrationProvenanceError("model_version is required; recency is not provenance")
        if self.input_regime is not None and not self.input_regime.strip():
            raise CalibrationProvenanceError(
                "input_regime must be a regime name or REGIME_ABSENT, never an empty string"
            )
        if self.min_races < 1:
            raise CalibrationProvenanceError("min_races must be at least 1")
        if self.producer_train_through is not None:
            # ``datetime`` is a subclass of ``date``, so a plain isinstance check would let a
            # timestamp through and compare it against a DATE column -- whose result depends on
            # the session timezone. The boundary is a calendar date; require exactly that.
            if isinstance(self.producer_train_through, dt.datetime) or not isinstance(
                self.producer_train_through, dt.date
            ):
                raise CalibrationProvenanceError(
                    "producer_train_through must be a date, not a datetime"
                )
        if self.material_as_of is not None:
            if not isinstance(self.material_as_of, dt.datetime):
                raise CalibrationProvenanceError("material_as_of must be a datetime")
            if self.material_as_of.tzinfo is None:
                # PredictionRun.computed_at is DateTime(timezone=True); a naive bound would be
                # interpreted in the session timezone and silently move the cut.
                raise CalibrationProvenanceError(
                    "material_as_of must be timezone-aware (computed_at is timezone-aware)"
                )

    # -- query predicates -----------------------------------------------------------
    def run_conditions(self) -> list:
        """Conditions on ``PredictionRun``, to be applied BEFORE the latest-run pick.

        Applying them afterwards would let an ineligible newer run win the ``DISTINCT ON``
        and silently drop a race that had perfectly good material.
        """
        conds = [PredictionRun.model_version == self.model_version]
        if self.input_regime == REGIME_ABSENT:
            conds.append(~PredictionRun.logic_version.contains(_MARKER, autoescape=True))
        elif self.input_regime is not None:
            # Trailing-boundary safe: the marker is the last segment or is followed by ';'.
            # ``autoescape`` is NOT optional: ``contains``/``endswith`` compile to LIKE, where
            # ``_`` and ``%`` are wildcards -- without it "full_info" also matches "fullXinfo".
            frag = f"{_MARKER}{self.input_regime}"
            conds.append(
                PredictionRun.logic_version.contains(f"{frag};", autoescape=True)
                | PredictionRun.logic_version.endswith(frag, autoescape=True)
            )
        if self.material_as_of is not None:
            conds.append(PredictionRun.computed_at <= self.material_as_of)
        return conds

    def race_conditions(self, race_date_column) -> list:
        """Conditions on the race date column.  Out-of-sample is STRICTLY after."""
        if self.producer_train_through is None:
            return []
        return [race_date_column > self.producer_train_through]

    def describe(self) -> dict:
        """Auditable record of the contract that was actually enforced."""
        return {
            "model_version": self.model_version,
            "input_regime": self.input_regime,
            "producer_train_through": (
                None if self.producer_train_through is None else str(self.producer_train_through)
            ),
            "material_as_of": (
                None if self.material_as_of is None else self.material_as_of.isoformat()
            ),
            "min_races": self.min_races,
            "generation_check": "model_version string equality (no per-run artifact digest exists)",
        }
