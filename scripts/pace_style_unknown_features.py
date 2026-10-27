"""132 research-only correction of unknown historical running styles.

The shipped feature registry and serving inputs are deliberately untouched. Unknown
appearances stay in the last-five STARTED window; only their binary observations
become NaN. In particular, 後方 remains a known legacy nonmatch in this experiment.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from horseracing_db.enums import EntryStatus
from horseracing_features.loader import Frames
from horseracing_features.pace_features import (
    _CLOSER_STYLES, _FRONT_STYLES, _pace_runs, _rolling_asof, build_pace_features,
)
from horseracing_features.pace_scenario_features import build_pace_scenario_features
from horseracing_training.dataset import TrainingMatrix

KEYS = ["race_id", "horse_id"]
CUTOFF = dt.date(2026, 9, 6)
SEMANTICS_ID = "132-style-unknown-v1-last5-started-known-only-mean"
KNOWN_STYLES = frozenset(_FRONT_STYLES | _CLOSER_STYLES | {"中団", "後方"})
STYLE_COLUMNS = ["front_runner_rate", "closer_rate"]
SCENARIO_COLUMNS = [
    "field_front_rate_ex_self", "field_closer_rate_ex_self", "pace_imbalance_ex_self",
    "front_pressure", "closer_setup", "field_style_coverage",
]
OVERLAY_COLUMNS = STYLE_COLUMNS + SCENARIO_COLUMNS


def validate_frames(frames: Frames, *, cutoff: dt.date = CUTOFF) -> str:
    if cutoff > CUTOFF:
        raise ValueError("Requested cutoff exceeds 2026-09-06")
    dates = pd.to_datetime(frames.races.race_date, errors="raise")
    if dates.empty or dates.isna().any() or dates.max() > pd.Timestamp(cutoff):
        raise ValueError("Missing or forbidden race date")
    if not dates.eq(dates.dt.normalize()).all():
        raise ValueError("race_date must be a whole day")
    if frames.races.race_id.duplicated().any():
        raise ValueError("Duplicate race IDs")
    allowed = set(frames.races.race_id)
    for source in (frames.race_horses, frames.race_results):
        if source[KEYS].isna().any().any() or source.duplicated(KEYS).any():
            raise ValueError("Missing or duplicate appearance keys")
        if not set(source.race_id) <= allowed:
            raise ValueError("Appearance outside the date-bounded race set")
    return str(dates.max().date())


def known_mask(styles: pd.Series) -> pd.Series:
    # Blank strings are missing, but nonblank spelling is not silently normalized.
    blank = styles.map(lambda x: isinstance(x, str) and not x.strip())
    unknown = styles.isna() | blank
    unexpected = styles.loc[~unknown & ~styles.isin(KNOWN_STYLES)]
    if len(unexpected):
        raise ValueError(f"Unregistered nonempty running_style tokens: {sorted(map(repr, unexpected.unique()))}")
    return ~unknown


def token_audit(frames: Frames) -> dict:
    through = validate_frames(frames)
    styles = frames.race_horses.running_style
    known = known_mask(styles)
    tokens = {str(k): int(v) for k, v in styles.fillna("<NULL>").value_counts().items()}
    return {"data_through": through, "raw_rows": len(styles), "known_rows": int(known.sum()),
            "unknown_rows": int((~known).sum()), "tokens": tokens,
            "known_styles": sorted(KNOWN_STYLES), "後方_policy": "known legacy nonmatch; remains zero",
            "source_caveat": "ID namespace and year are not row-level feed provenance"}


def build_overlay(frames: Frames) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return corrected eight columns, original eight columns, and row diagnostics."""
    validate_frames(frames)
    legacy = build_pace_features(frames)
    runs = _pace_runs(frames)
    known = known_mask(runs.running_style)
    runs["is_front"] = runs.is_front.where(known, np.nan)
    runs["is_closer"] = runs.is_closer.where(known, np.nan)
    # 0 is correct for the observation COUNT, not for an unknown binary style.
    runs["known_style_count"] = known.astype(float)
    runs["started_window_count"] = 1.0
    started = runs.loc[runs.is_started.eq(1)].copy()
    targets = runs[KEYS + ["race_date"]]
    style = _rolling_asof(started, targets, {
        "front_runner_rate": ("is_front", "mean"),
        "closer_rate": ("is_closer", "mean"),
        "known_style_fraction5": ("known_style_count", "mean"),
    })
    # Reuse the same eligible window to expose actual counts without changing it.
    ordered = started.sort_values(["horse_id", "race_date"], kind="stable").copy()
    g = ordered.groupby("horse_id", sort=False)
    for name, source in (("known_style_count5", "known_style_count"),
                         ("started_count5", "started_window_count")):
        ordered[name] = g[source].rolling(5, min_periods=1).sum().reset_index(level=0, drop=True)
    counts = pd.merge_asof(
        targets.sort_values("race_date", kind="stable"),
        ordered[["horse_id", "race_date", "known_style_count5", "started_count5"]]
        .sort_values("race_date", kind="stable"),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )
    fixed = legacy.set_index(KEYS).copy()
    style_indexed = style.set_index(KEYS)
    if not fixed.index.equals(style_indexed.reindex(fixed.index).index):
        raise ValueError("Style target index mismatch")
    for col in STYLE_COLUMNS:
        fixed[col] = style_indexed[col].reindex(fixed.index).astype("float64")
    fixed = fixed.reset_index()
    unchanged = [c for c in legacy.columns if c not in STYLE_COLUMNS]
    pd.testing.assert_frame_equal(legacy[unchanged], fixed[unchanged], check_exact=True)
    old_scenario = build_pace_scenario_features(frames, pace=legacy)
    new_scenario = build_pace_scenario_features(frames, pace=fixed)
    pd.testing.assert_frame_equal(old_scenario[KEYS + ["style_mismatch"]],
                                  new_scenario[KEYS + ["style_mismatch"]], check_exact=True)
    old = legacy[KEYS + STYLE_COLUMNS].merge(old_scenario[KEYS + SCENARIO_COLUMNS],
                                             on=KEYS, validate="one_to_one", sort=False)
    new = fixed[KEYS + STYLE_COLUMNS].merge(new_scenario[KEYS + SCENARIO_COLUMNS],
                                            on=KEYS, validate="one_to_one", sort=False)
    diag = counts[KEYS + ["race_date", "known_style_count5", "started_count5"]].copy()
    diag[["known_style_count5", "started_count5"]] = diag[["known_style_count5", "started_count5"]].fillna(0.)
    diag = diag.merge(frames.race_horses[KEYS + ["entry_status"]], on=KEYS,
                      validate="one_to_one", sort=False)
    return new, old, diag


def cell_changes(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    return ~(old.eq(new) | (old.isna() & new.isna()))


def apply_overlay(matrix: TrainingMatrix, overlay: pd.DataFrame) -> TrainingMatrix:
    if list(overlay.columns) != KEYS + OVERLAY_COLUMNS:
        raise ValueError("Unexpected overlay schema")
    if overlay.duplicated(KEYS).any() or matrix.frame.duplicated(KEYS).any():
        raise ValueError("Duplicate matrix or overlay keys")
    if not set(OVERLAY_COLUMNS) <= set(matrix.feature_cols):
        raise ValueError("Matrix lacks overlay columns")
    if any(overlay[c].dtype != np.dtype("float64") for c in OVERLAY_COLUMNS):
        raise ValueError("Overlay dtype must be float64")
    if np.isinf(overlay[OVERLAY_COLUMNS].to_numpy()).any():
        raise ValueError("Infinite overlay value")
    original = matrix.frame
    index = pd.MultiIndex.from_frame(original[KEYS])
    patch = overlay.set_index(KEYS)
    if not index.isin(patch.index).all():
        raise ValueError("Overlay missing a matrix appearance")
    frame = original.copy(deep=True)
    for col in OVERLAY_COLUMNS:
        frame[col] = patch[col].reindex(index).to_numpy(dtype="float64")
    rest = [c for c in original if c not in OVERLAY_COLUMNS]
    pd.testing.assert_frame_equal(original[rest], frame[rest], check_exact=True)
    if not frame.dtypes.equals(original.dtypes):
        raise ValueError("Overlay changed matrix dtypes")
    return TrainingMatrix(frame, matrix.feature_cols.copy(), matrix.categorical_cols.copy(),
                          dict(matrix.build_audit) | {"research_overlay_semantics": SEMANTICS_ID})


def coverage_report(old: pd.DataFrame, new: pd.DataFrame, diagnostics: pd.DataFrame) -> dict:
    keys = pd.MultiIndex.from_frame(old[KEYS])
    n = new.set_index(KEYS).reindex(keys)[OVERLAY_COLUMNS].reset_index(drop=True)
    o = old[OVERLAY_COLUMNS].reset_index(drop=True)
    diag = diagnostics.set_index(KEYS).reindex(keys).reset_index()
    diff = cell_changes(o, n)
    material = o.isna().ne(n.isna()) | (o - n).abs().gt(1e-12)
    diag["year"] = pd.to_datetime(diag.race_date).dt.year.astype(str)
    diag["month"] = pd.to_datetime(diag.race_date).dt.strftime("%Y-%m")
    diag["id_namespace"] = np.where(diag.horse_id.str.startswith("nk:"), "nk", "canonical")
    diag["history_group"] = np.where(diag.started_count5.eq(0), "no_prior_start",
        np.where(diag.known_style_count5.eq(0), "prior_all_unknown",
                 np.where(diag.known_style_count5.lt(diag.started_count5), "prior_partial_unknown", "prior_all_known")))
    diag["changed"] = diff.any(axis=1).to_numpy()

    def summarize(mask):
        positions = np.flatnonzero(mask.to_numpy())
        d, a, b, ch = diag.iloc[positions], o.iloc[positions], n.iloc[positions], diff.iloc[positions]
        substantial = material.iloc[positions]
        return {"n_rows": len(d), "n_races": int(d.race_id.nunique()),
                "changed_rows": int(ch.any(axis=1).sum()),
                "changed_races": int(d.loc[ch.any(axis=1).to_numpy(), "race_id"].nunique()),
                "material_changed_rows": int(substantial.any(axis=1).sum()),
                "material_changed_races": int(d.loc[substantial.any(axis=1).to_numpy(), "race_id"].nunique()),
                "rounding_only_rows": int((ch.any(axis=1) & ~substantial.any(axis=1)).sum()),
                "prior_all_unknown_rows": int(d.history_group.eq("prior_all_unknown").sum()),
                "prior_partial_unknown_rows": int(d.history_group.eq("prior_partial_unknown").sum()),
                "known_count5": {str(int(k)): int(v) for k, v in d.known_style_count5.value_counts().items()},
                "columns": {c: {"changed": int(ch[c].sum()), "old_missing": int(a[c].isna().sum()),
                    "material_changed": int(substantial[c].sum()),
                    "rounding_only": int((ch[c] & ~substantial[c]).sum()),
                    "new_missing": int(b[c].isna().sum()),
                    "zero_to_unknown": int((a[c].eq(0) & b[c].isna()).sum()),
                    "finite_to_finite": int((ch[c] & a[c].notna() & b[c].notna()).sum())}
                    for c in OVERLAY_COLUMNS},
                "coverage_mean_old": float(a.field_style_coverage.mean()) if len(a) else None,
                "coverage_mean_new": float(b.field_style_coverage.mean()) if len(b) else None}

    started = diag.entry_status.eq(EntryStatus.STARTED)
    result = {"all_started": summarize(started)}
    for group in ("year", "month", "id_namespace", "history_group"):
        result[group] = {str(k): summarize(started & diag[group].eq(k)) for k in sorted(diag[group].unique())}
    return result
