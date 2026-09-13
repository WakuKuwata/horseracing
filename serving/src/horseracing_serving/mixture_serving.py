"""131: serve the 129 six-member mixture through the ordinary model_versions path.

A mixture model_version row points ``weights_uri``/``calibrator_uri`` at ``<dir>/bundle.json``
and carries ``metadata.json`` with ``artifact_kind: mixture_model_version``. ``load_serving_model``
dispatches here on that kind; everything downstream (pipeline, persistence, API) sees a model
object with the same attributes the booster ``ServingModel`` exposes.

Prediction = the 129 shadow path (serving regime -> six corrected members -> equal-weight mean)
for WIN, then the production display convention for top2/top3 (Harville from the mean win with
the runtime stage discount), so the persisted heads follow the same rule as every other model.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus
from horseracing_db.models import Race, RaceHorse
from horseracing_eval.predictor import Prediction
from horseracing_features import registry as feature_registry
from horseracing_training.dataset import CATEGORICAL_FEATURES
from horseracing_training.predictor import assemble_predictions
from sqlalchemy import select

from .mixture_model import (
    FULL_HASH,
    MixtureBundle,
    feature_profile,
    load_mixture_bundle,
    predict_mixture,
    prepare_race_inputs,
)
from .model_loader import ServingError

ARTIFACT_KIND = "mixture_model_version"
HISTORY_START = dt.date(2007, 1, 1)
#: Coefficients are fit for one calendar year; production tolerates the following year (marked
#: in logic_version) and refuses beyond it, so a forgotten refresh fails loudly, not silently.
COEFFICIENT_GRACE_YEARS = 1


@dataclass(frozen=True)
class MixtureServingModel:
    model_version: str
    bundle: MixtureBundle
    feature_cols: list[str]
    categorical_cols: list[str]
    feature_version: str
    feature_hash: str
    metadata: dict
    race_class_representation: str = "raw"
    objective: str = "mixture"
    market_offset: None = None
    booster: None = None
    encoders: dict = field(default_factory=dict)
    categorical_vocab: dict = field(default_factory=dict)
    coefficient_grace_years: int = COEFFICIENT_GRACE_YEARS

    @property
    def bundle_sha12(self) -> str:
        return self.bundle.sha256[:12]

    @property
    def coefficient_year(self) -> int:
        years = {int(m["correction"]["valid_year"]) for m in self.bundle.manifest["members"]}
        if len(years) != 1:
            raise ServingError("mixture members disagree on coefficient year")
        return years.pop()


def load_mixture_serving_model(model_version: str, art_dir: Path, metadata: dict):
    if metadata.get("artifact_kind") != ARTIFACT_KIND:
        raise ServingError(f"'{model_version}' is not a mixture model_version")
    expected = metadata.get("bundle_sha256")
    if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
        raise ServingError(f"'{model_version}' metadata lacks a valid bundle_sha256")
    if (metadata.get("feature_version") != "features-021"
            or metadata.get("feature_hash") != FULL_HASH):
        raise ServingError(
            f"'{model_version}' mixture metadata must pin features-021/{FULL_HASH[:12]}"
        )
    # The members were trained on RAW race_class spellings. ``feature_profile`` pins the registry
    # version/hash, but a representation binding (098: values change, column names do not) is not
    # visible in either, so pin it here too — the booster path resolves this in the loader as well.
    representation = getattr(feature_registry, "RACE_CLASS_REPRESENTATION", None)
    if representation not in (None, "raw"):
        raise ServingError(
            f"'{model_version}' mixture members are raw-representation artifacts; registry "
            f"binds {representation!r}"
        )
    try:
        bundle = load_mixture_bundle(Path(art_dir) / "bundle.json", expected_sha256=expected)
        cols = list(feature_profile()["full_columns"])
    except ValueError as exc:
        raise ServingError(
            f"mixture bundle for '{model_version}' failed validation: {exc}"
        ) from exc
    return MixtureServingModel(
        model_version=model_version, bundle=bundle, feature_cols=cols,
        categorical_cols=[c for c in CATEGORICAL_FEATURES if c in cols],
        feature_version="features-021", feature_hash=FULL_HASH, metadata=metadata,
    )


def is_mixture(model) -> bool:
    return isinstance(model, MixtureServingModel)


# --- as-of inputs the booster path does not need -------------------------------------------

def ensure_race_date(feature_rows: pd.DataFrame, race_ids, race_date: dt.date) -> pd.DataFrame:
    """The serving feature matrix carries only model inputs + ids; the mixture needs the day."""
    if "race_date" not in feature_rows.columns:
        out = feature_rows.copy()
        out["race_date"] = race_date
        return out
    mask = feature_rows.race_id.isin(list(race_ids))
    if not (pd.to_datetime(feature_rows.loc[mask, "race_date"]) == pd.Timestamp(race_date)).all():
        raise ServingError("feature rows disagree with the target race day")
    return feature_rows


def load_started_history(session, horse_ids) -> pd.DataFrame:
    """Every registered start (any entry status) of the target horses since 2007; no results."""
    stmt = (
        select(RaceHorse.race_id, RaceHorse.horse_id, Race.race_date, RaceHorse.entry_status)
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(RaceHorse.horse_id.in_(list(horse_ids)), Race.race_date >= HISTORY_START)
    )
    rows = session.execute(stmt).all()
    return pd.DataFrame(rows, columns=["race_id", "horse_id", "race_date", "entry_status"])


def history_for(target_rows: pd.DataFrame, raw_history: pd.DataFrame) -> pd.DataFrame:
    """Started rows strictly before each target horse's race day; no result columns."""
    required = {"race_id", "horse_id", "race_date"}
    if not required.issubset(raw_history.columns) or not {"horse_id", "race_date"}.issubset(
        target_rows.columns
    ):
        raise ValueError("History/target columns missing")
    history = raw_history[[c for c in raw_history.columns if "result" not in c]].copy()
    if "entry_status" in history.columns:
        if not history.entry_status.isin(EntryStatus.ALL).all():
            raise ValueError("Unknown history entry status")
        history = history[history.entry_status == EntryStatus.STARTED]
    history["race_date"] = pd.to_datetime(history.race_date)
    targets = target_rows[["horse_id", "race_date"]].drop_duplicates()
    if targets.horse_id.duplicated().any():
        # ``drop_duplicates("horse_id")`` would silently keep the FIRST date and truncate the
        # later targets' history; one call = one race day per horse (a horse runs once a day).
        raise ValueError("history_for: a horse has several target dates in one call")
    targets = targets.assign(race_date=pd.to_datetime(targets.race_date)).rename(
        columns={"race_date": "target_date"}
    )
    merged = history.merge(targets, on="horse_id", how="inner")
    merged = merged[
        (merged.race_date < merged.target_date)
        & (merged.race_date >= pd.Timestamp(HISTORY_START))
    ]
    out = merged[["race_id", "horse_id", "race_date"]].sort_values(
        ["horse_id", "race_date", "race_id"], kind="stable"
    )
    return out.reset_index(drop=True)


def assert_history_matches_features(rows: pd.DataFrame, history: pd.DataFrame) -> None:
    """The prior-gap history and the feature matrix must describe the SAME past.

    ``career_starts`` (feature 004) is the number of the horse's started rows strictly before the
    race day, from the same ``race_horses`` population the prior-gap term reads. If the two ever
    disagree (a different pool start, a filter on one side only, an ID re-key between the two
    loads), the gap terms and the model inputs would refer to different "last starts" — a
    plausible-looking number, silently wrong. Fail closed instead; the audit of the 2026 runs
    (34,304 horse rows) found the two equal everywhere, so this never fires on a consistent DB.
    """
    if "career_starts" not in rows.columns:
        raise ServingError("feature rows lack career_starts; cannot cross-check the history")
    counts = history.groupby("horse_id").size() if len(history) else pd.Series(dtype="int64")
    expected = pd.to_numeric(rows["career_starts"], errors="coerce")
    actual = rows["horse_id"].map(counts).fillna(0).astype("float64")
    bad = rows.loc[expected.notna() & (expected.to_numpy() != actual.to_numpy()), "horse_id"]
    if len(bad):
        raise ServingError(
            "started history disagrees with career_starts for "
            f"{sorted(bad.tolist())[:5]} (history rows != feature count)"
        )


def mixture_context(session, feature_rows: pd.DataFrame, race_ids, race_date: dt.date):
    """One day's shared inputs: dated feature rows + the started history of every target horse."""
    rows = ensure_race_date(feature_rows, race_ids, race_date)
    horses = rows.loc[rows.race_id.isin(list(race_ids)), "horse_id"].unique()
    return rows, load_started_history(session, horses)


# --- prediction ------------------------------------------------------------------------------

def predict_mixture_race(
    model: MixtureServingModel, race_id: str, feature_rows: pd.DataFrame,
    raw_history: pd.DataFrame, *, stage_discount=None,
):
    """Same 4-tuple as ``predictor.predict_race``; WIN is the corrected six-member mean."""
    from .predictor import _jsonable

    rows, regime_audit = prepare_race_inputs(feature_rows, race_id, "serving")
    ids = rows.horse_id.tolist()
    history = history_for(rows, raw_history)
    assert_history_matches_features(rows, history)
    result = predict_mixture(
        model.bundle, race_id, feature_rows, history, "serving",
        coefficient_grace_years=model.coefficient_grace_years,
    )
    if list(result.predictions) != ids:
        raise ServingError("mixture started population differs from the prepared rows")
    win = np.asarray([result.predictions[h].win for h in ids], dtype=float)
    if not np.isfinite(win).all() or (win <= 0).any():
        raise ServingError("mixture produced a non-finite or non-positive win vector")
    # Production display convention (049): top2/top3 from the served win vector, win untouched.
    # ``eps=0.0`` — the mixture contract assembles at eps 0 (129 ``INFERENCE['assembly_eps']``):
    # each member's corrected q is already positive and race-normalised, and the six-member mean
    # inherits that. Re-clipping at DEFAULT_CLIP (1e-6) is NOT a no-op here: a corrected q can sit
    # below 1e-6 (the residual tilt multiplies a base p clipped at 1e-6), and clipping it up
    # renormalises the WHOLE race, so the persisted win would no longer be the mixture's own vector
    # (2026 backfill audit: 27 races / 416 horse rows differed, max 3.3e-7). Harville needs win<1
    # only, which the ``_heads`` domain check upstream already guarantees.
    assembled = assemble_predictions(ids, win, eps=0.0, stage_discount=stage_discount)
    if max(abs(assembled[h].win - win[i]) for i, h in enumerate(ids)) > 1e-12:
        raise ServingError("display assembly changed the mixture win vector")
    predictions = {
        h: Prediction(win=float(win[i]), top2=assembled[h].top2, top3=assembled[h].top3)
        for i, h in enumerate(ids)
    }
    snapshots: dict[str, dict] = {}
    for i, hid in enumerate(ids):
        feat = {c: _jsonable(rows.iloc[i][c]) for c in model.feature_cols}
        feat["_raw_win"] = float(win[i])
        feat["_calibrated_win"] = float(win[i])
        snapshots[hid] = feat
    explanations = {hid: None for hid in ids}  # 040: mixture has no single booster to decompose
    audit = {
        **result.audit, "regime_audit": regime_audit, "n_rows": len(ids),
        "representation": model.race_class_representation, "feature_version": model.feature_version,
        "n_unknown": None, "unknown_values": [], "n_history_rows": int(len(history)),
    }
    return predictions, snapshots, explanations, audit
