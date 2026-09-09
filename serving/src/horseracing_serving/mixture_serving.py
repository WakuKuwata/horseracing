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
from horseracing_training.calibration import DEFAULT_CLIP
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
    if metadata.get("feature_version") != "features-021" or metadata.get("feature_hash") != FULL_HASH:
        raise ServingError(
            f"'{model_version}' mixture metadata must pin features-021/{FULL_HASH[:12]}"
        )
    try:
        bundle = load_mixture_bundle(Path(art_dir) / "bundle.json", expected_sha256=expected)
        cols = list(feature_profile()["full_columns"])
    except ValueError as exc:
        raise ServingError(f"mixture bundle for '{model_version}' failed validation: {exc}") from exc
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
    targets = target_rows[["horse_id", "race_date"]].drop_duplicates("horse_id")
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
    predictions = assemble_predictions(ids, win, eps=DEFAULT_CLIP, stage_discount=stage_discount)
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
