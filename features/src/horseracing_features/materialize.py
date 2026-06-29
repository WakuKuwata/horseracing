"""Feature 025: parquet materialization of the heavy as-of / past-derived features.

The as-of feature blocks (history / 020 extra / 020 human_form / 023 pace) are expensive to
recompute in-memory per predictor over 20 years. This module computes them ONCE over the full pool
and writes a parquet + manifest; the builder reads them back (opt-in). Static/current-race are NOT
materialized (cheap, computed by build_static_features).

CRITICAL (codex):
- Single implementation: `build_asof_features` is the ONLY as-of source — it calls the same block
  functions the in-memory builder and the serving fallback use. No duplicated as-of logic.
- Parity: the materialized values must be bit-identical to the in-memory computation; the builder
  asserts this (FEATURE_VERSION unchanged → adopted models unchanged).
- Staleness fail-closed: the manifest stores a SOURCE FINGERPRINT (hash of the feature-input source
  columns). A range/row-count check misses in-range backfills, so reads verify the fingerprint and
  fail-closed on mismatch (never silently serve stale features).
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
from pathlib import Path

import pandas as pd

from .extra_features import build_extra_features
from .history import build_history_features
from .human_form import build_human_form_features
from .loader import Frames
from .lowcost_features import build_lowcost_features
from .pace_features import build_pace_features
from .pedigree_features import build_pedigree_features
from .registry import FEATURE_VERSION, materialized_columns
from .schema import DEFAULT_LOW_HISTORY_MAX

_KEYS = ["race_id", "horse_id"]
#: Feature 026: horses pedigree columns folded into the staleness fingerprint, so a pedigree
#: backfill (sire_name filled/corrected while the race tables stay unchanged) trips fail-closed.
_HORSE_FP_COLS = ["horse_id", "sire_name", "dam_name", "damsire_name",
                  "sire_id", "dam_id", "damsire_id"]
MANIFEST_VERSION = 1


class MaterializationError(RuntimeError):
    """Raised when the materialized parquet is missing/stale/uncovered (fail-closed)."""


@dataclasses.dataclass(frozen=True)
class Manifest:
    manifest_version: int
    feature_version: str
    n_rows: int
    data_from: str | None
    data_through: str | None
    content_hash: str
    source_fingerprint: str
    materialized_columns: list[str]
    generated_at: str | None = None

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False, sort_keys=True, indent=2)


def _hash_frame(df: pd.DataFrame, cols: list[str]) -> str:
    """Deterministic hash of a frame's projected columns (row-order independent via sorted keys).

    object columns may hold unhashable cells (e.g. race_results.corner_orders is a list), so they
    are stringified before hashing — deterministic and sufficient for change detection.
    """
    sub = df[cols].sort_values([c for c in _KEYS if c in cols] or cols, kind="stable").copy()
    for c in sub.columns:
        if sub[c].dtype == object:
            sub[c] = sub[c].map(lambda v: "" if v is None else str(v))
    return hashlib.sha256(
        pd.util.hash_pandas_object(sub, index=False).values.tobytes()
    ).hexdigest()


def _restrict(frames: Frames, through: datetime.date | None) -> Frames:
    """Frames restricted to races on/before ``through`` (None = no restriction)."""
    if through is None:
        return frames
    races = frames.races.copy()
    keep = races[pd.to_datetime(races["race_date"]) <= pd.Timestamp(through)]["race_id"]
    keep_set = set(keep)
    rh = frames.race_horses[frames.race_horses["race_id"].isin(keep_set)]
    # Feature 026: restrict horses to those running in the kept races, so a FUTURE horse (only in
    # races beyond `through`) does not flip the fingerprint (consistent with the date restriction).
    horses = frames.horses
    if len(horses):
        horses = horses[horses["horse_id"].isin(set(rh["horse_id"]))]
    return Frames(
        races=races[races["race_id"].isin(keep_set)],
        race_horses=rh,
        race_results=frames.race_results[frames.race_results["race_id"].isin(keep_set)],
        horses=horses,
    )


def source_fingerprint(frames: Frames, *, through: datetime.date | None = None) -> str:
    """Hash of the SOURCE columns that feed the as-of features (races/race_horses/race_results),
    restricted to races on/before ``through``.

    Detects in-range backfills/edits that a date-range + row-count check would miss (codex P0). The
    ``through`` restriction means new FUTURE races (beyond the materialized range) do NOT trigger a
    false staleness — they are handled by the builder's fallback (serving), while any change WITHIN
    the materialized range flips the fingerprint and forces fail-closed.
    """
    fr = _restrict(frames, through)
    horse_fp_cols = [c for c in _HORSE_FP_COLS if c in fr.horses.columns]
    parts = [
        _hash_frame(fr.races, list(fr.races.columns)),
        _hash_frame(fr.race_horses, list(fr.race_horses.columns)),
        _hash_frame(fr.race_results, list(fr.race_results.columns)),
        # Feature 026: pedigree backfill detection (the race tables may be unchanged).
        _hash_frame(fr.horses, horse_fp_cols) if horse_fp_cols and len(fr.horses) else "",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def build_asof_features(
    frames: Frames, *, low_history_max: int = DEFAULT_LOW_HISTORY_MAX
) -> pd.DataFrame:
    """THE single as-of source: per-(race_id, horse_id) materialized columns.

    Calls the same block functions as the in-memory builder / serving fallback (no duplicate logic).
    """
    history = build_history_features(frames, low_history_max=low_history_max)
    extra = build_extra_features(frames)
    human = build_human_form_features(frames)
    pace = build_pace_features(frames)
    pedigree = build_pedigree_features(frames)  # Feature 026 (single as-of source)
    lowcost = build_lowcost_features(frames)    # Feature 030 (single as-of source)
    out = (
        history.merge(extra, on=_KEYS, how="left")
        .merge(human, on=_KEYS, how="left")
        .merge(pace, on=_KEYS, how="left")
        .merge(pedigree, on=_KEYS, how="left")
        .merge(lowcost, on=_KEYS, how="left")
    )
    cols = [*_KEYS, *materialized_columns()]
    return out[cols].sort_values(_KEYS, kind="stable").reset_index(drop=True)


def _manifest_path(parquet_path: Path) -> Path:
    return parquet_path.with_suffix(".manifest.json")


def write_materialized(
    parquet_path: str | Path, frames: Frames, *, low_history_max: int = DEFAULT_LOW_HISTORY_MAX,
    generated_at: str | None = None,
) -> Manifest:
    """Compute as-of features once and write parquet + manifest (deterministic content_hash)."""
    parquet_path = Path(parquet_path)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    asof = build_asof_features(frames, low_history_max=low_history_max)
    asof.to_parquet(parquet_path, index=False)

    if len(frames.races):
        dates = pd.to_datetime(frames.races["race_date"]).dropna()
    else:
        dates = pd.Series([], dtype="datetime64[ns]")
    through = dates.max().date() if len(dates) else None
    manifest = Manifest(
        manifest_version=MANIFEST_VERSION,
        feature_version=FEATURE_VERSION,
        n_rows=int(len(asof)),
        data_from=(dates.min().date().isoformat() if len(dates) else None),
        data_through=(through.isoformat() if through else None),
        content_hash=_hash_frame(asof, list(asof.columns)),
        source_fingerprint=source_fingerprint(frames, through=through),
        materialized_columns=materialized_columns(),
        generated_at=generated_at,
    )
    _manifest_path(parquet_path).write_text(manifest.to_json(), encoding="utf-8")
    return manifest


def read_materialized(parquet_path: str | Path) -> tuple[pd.DataFrame, Manifest]:
    parquet_path = Path(parquet_path)
    mpath = _manifest_path(parquet_path)
    if not parquet_path.exists() or not mpath.exists():
        raise MaterializationError(f"materialized parquet/manifest missing: {parquet_path}")
    df = pd.read_parquet(parquet_path)
    raw = json.loads(mpath.read_text(encoding="utf-8"))
    manifest = Manifest(**raw)
    return df, manifest


def assert_fresh(manifest: Manifest, frames: Frames) -> None:
    """Fail-closed if the parquet is stale vs the current source (fingerprint/version), so callers
    never silently serve outdated features (codex P0)."""
    if manifest.feature_version != FEATURE_VERSION:
        raise MaterializationError(
            f"feature_version mismatch: parquet={manifest.feature_version} now={FEATURE_VERSION}"
        )
    through = datetime.date.fromisoformat(manifest.data_through) if manifest.data_through else None
    current = source_fingerprint(frames, through=through)
    if manifest.source_fingerprint != current:
        raise MaterializationError(
            "source fingerprint mismatch (data changed/backfilled since materialize) — regenerate"
        )


def has_future_rows(frames: Frames, manifest: Manifest, *, start_date, end_date) -> bool:
    """True if any in-scope race is beyond the materialized range (→ builder fallback compute)."""
    if manifest.data_through is None:
        return True
    through = pd.Timestamp(datetime.date.fromisoformat(manifest.data_through))
    d = pd.to_datetime(frames.races["race_date"])
    scope = frames.races[(d >= pd.Timestamp(start_date)) & (d > through)]
    if end_date is not None:
        scope = scope[pd.to_datetime(scope["race_date"]) <= pd.Timestamp(end_date)]
    return len(scope) > 0
