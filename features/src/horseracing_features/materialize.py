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
import decimal
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from .condition_change_features import build_condition_change_features
from .corner_trajectory_features import build_corner_trajectory_features
from .debut_pedigree_features import build_debut_pedigree_features
from .extra_features import build_extra_features
from .history import build_history_features
from .human_form import build_human_form_features
from .loader import Frames
from .lowcost_features import build_lowcost_features
from .owner_breeder_features import build_owner_breeder_features
from .pace_features import build_pace_features
from .pace_scenario_features import build_pace_scenario_features
from .past_market_features import build_past_market_features  # Feature 058 (B1)
from .pedigree_features import build_pedigree_features
from .pm_core_strength import build_pm_core_strength_features  # Feature 069 (F02)
from .race_level_features import build_race_level_features
from .registry import FEATURE_VERSION, materialized_columns
from .relative_ability_features import build_relative_ability_features
from .schema import DEFAULT_LOW_HISTORY_MAX
from .speed_figure_features import build_speed_figure_features  # Feature 061

_KEYS = ["race_id", "horse_id"]
#: Feature 026: horses pedigree columns folded into the staleness fingerprint, so a pedigree
#: backfill (sire_name filled/corrected while the race tables stay unchanged) trips fail-closed.
_HORSE_FP_COLS = ["horse_id", "sire_name", "dam_name", "damsire_name",
                  "sire_id", "dam_id", "damsire_id",
                  # Feature 056: owner/breeder/lines backfill detection (fail-closed)
                  "owner_name", "breeder_name", "sire_line", "damsire_line"]
MANIFEST_VERSION = 1
#: Feature 055: value-canonical fingerprint. fp-v1 hashed raw dtypes (hash_pandas_object is
#: dtype-sensitive: int64(1) != float64(1.0)), which forced verification to re-load the FULL pool
#: exactly like materialize time. fp-v2 canonicalizes values first (numeric -> float64, other ->
#: str), so equal VALUES hash equal regardless of the load window — verification can then reuse
#: the end_date-windowed frames (+ a small delta load) instead of a second full-pool load.
FINGERPRINT_ALGO = "fp-v2"


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
    #: Feature 055: fingerprint algorithm tag. Old manifests (field absent -> None) predate the
    #: value-canonical hash and MUST be regenerated — fail-closed, never silently accepted.
    fingerprint_algo: str | None = None

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False, sort_keys=True, indent=2)


def _canon_cell(v) -> str:
    """Canonical string for one cell: missing -> "", numeric -> repr(float), else str.

    Used for object/datetime columns whose cells may be None/NaN/Decimal/int/list — the numeric
    branch makes an object-held Decimal('56.0')/int 56 hash identically to a float64 56.0 column
    (repr(float) == str(float) in py3). Lists (race_results.corner_orders) fall through to str.
    """
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float, decimal.Decimal)):
        return repr(float(v))
    return str(v)


def _hash_frame(df: pd.DataFrame, cols: list[str]) -> str:
    """Deterministic VALUE-canonical hash of a frame's projected columns (fp-v2, Feature 055).

    Row-order independent via sorted keys. Every column is canonicalized to STRINGS before
    hashing, so the hash depends only on values, never on pool-dependent dtypes: the 025/026
    int->float drift (a column all-int inside one load window but NaN-bearing in another loads as
    int64 vs float64), object-held Decimals from read_sql, and float64 columns degraded to object
    by concatenating an EMPTY delta frame (read_sql of zero rows yields all-object dtypes) all
    hash identically for equal values. Missing (None/NaN) canonicalizes to "".
    """
    sub = df[cols].sort_values([c for c in _KEYS if c in cols] or cols, kind="stable").copy()
    for c in sub.columns:
        s = sub[c]
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            f = s.astype("float64")  # vectorized fast path; astype(str) of float == repr(float)
            sub[c] = f.astype(str).mask(f.isna(), "")
        else:
            sub[c] = s.map(_canon_cell)
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
    scenario = build_pace_scenario_features(frames, pace=pace)  # Feature 031 (field-composition)
    debutped = build_debut_pedigree_features(  # Feature 032 (debut/low-history × pedigree)
        frames, history=history, pedigree=pedigree
    )
    condchg = build_condition_change_features(frames, pace=pace)  # Feature 033 (condition×ability)
    cornertraj = build_corner_trajectory_features(frames)  # Feature 041 (corner trajectory)
    ownerbrd = build_owner_breeder_features(frames)  # Feature 056 (owner/breeder as-of rates)
    racelevel = build_race_level_features(frames)    # Feature 056 (prize class, as-of half)
    out = (
        history.merge(extra, on=_KEYS, how="left")
        .merge(human, on=_KEYS, how="left")
        .merge(pace, on=_KEYS, how="left")
        .merge(pedigree, on=_KEYS, how="left")
        .merge(lowcost, on=_KEYS, how="left")
        .merge(scenario, on=_KEYS, how="left")
        .merge(debutped, on=_KEYS, how="left")
        .merge(condchg, on=_KEYS, how="left")
        .merge(cornertraj, on=_KEYS, how="left")
        .merge(ownerbrd, on=_KEYS, how="left")
        .merge(racelevel, on=_KEYS, how="left")
    )
    # Feature 059: within-race relative ability — depends on the ASSEMBLED as-of ability columns
    # (win_rate / rel_time_avg / ...), so it runs AFTER the merges over the full `out` frame.
    relability = build_relative_ability_features(frames, ability_frame=out)
    out = out.merge(relability, on=_KEYS, how="left")
    # Feature 058 (B1): past market-assessment (popularity) — independent as-of block over
    # race_horses.popularity (does not read assembled ability columns). Accuracy-first model only.
    pastmkt = build_past_market_features(frames)
    out = out.merge(pastmkt, on=_KEYS, how="left")
    # Feature 061: speed figure — independent as-of block (races/race_results only, no new
    # source columns => source_fingerprint unchanged). Additive left-merge (INV-F2).
    spdfig = build_speed_figure_features(frames)
    out = out.merge(spdfig, on=_KEYS, how="left")
    # Feature 069 (F02): past market SUPPORT (s=log(q×N)) — independent as-of block over
    # race_horses.odds. Additive left-merge (INV-F2). Reads a NEW source column (odds) that 058
    # did not, so source_fingerprint MUST include odds (a fresh materialize is required).
    pmcs = build_pm_core_strength_features(frames)
    out = out.merge(pmcs, on=_KEYS, how="left")
    # Feature 070 (F03/F04/F05 past-market bundle) was rejected at the staged gate — NOT wired in
    # (bump reverted). pm_rank_robust/pm_expectation_residual/pm_conditioned.py kept as the
    # documented negative result (their unit tests call the build functions directly).
    # Feature 063 (closing figure) was rejected at the full 19-fold gate (redundant with 061 over
    # the full period) — NOT wired in. closing_figure_features.py + tests kept as negative result.
    # Feature 062 (Elo rating) was rejected at the pre-registered gate (redundant under pl_topk), so
    # it is NOT wired into the default as-of source. rating_features.py + its unit tests remain as
    # the documented negative result; re-enable this block only if a future variant passes.
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
        fingerprint_algo=FINGERPRINT_ALGO,
    )
    _manifest_path(parquet_path).write_text(manifest.to_json(), encoding="utf-8")
    return manifest


def read_manifest(parquet_path: str | Path) -> Manifest:
    """Load the manifest sidecar only (cheap JSON read — no parquet load). Feature 055: lets the
    builder learn ``data_through`` up front to plan the fingerprint-verification load window."""
    parquet_path = Path(parquet_path)
    mpath = _manifest_path(parquet_path)
    if not parquet_path.exists() or not mpath.exists():
        raise MaterializationError(f"materialized parquet/manifest missing: {parquet_path}")
    raw = json.loads(mpath.read_text(encoding="utf-8"))
    return Manifest(**raw)


def read_materialized(parquet_path: str | Path) -> tuple[pd.DataFrame, Manifest]:
    parquet_path = Path(parquet_path)
    manifest = read_manifest(parquet_path)
    df = pd.read_parquet(parquet_path)
    return df, manifest


def assert_manifest_compatible(manifest: Manifest) -> None:
    """Frame-free compatibility checks (feature_version / fingerprint algo) — fail-closed.

    Feature 055: split out of assert_fresh so a backfill run that already fingerprint-verified once
    can still cheaply re-assert compatibility per day without any DB load."""
    if manifest.feature_version != FEATURE_VERSION:
        raise MaterializationError(
            f"feature_version mismatch: parquet={manifest.feature_version} now={FEATURE_VERSION}"
        )
    if manifest.fingerprint_algo != FINGERPRINT_ALGO:
        raise MaterializationError(
            f"fingerprint algo mismatch: manifest={manifest.fingerprint_algo!r} "
            f"now={FINGERPRINT_ALGO!r} (old-format manifest) — re-run `features materialize`"
        )


def assert_fresh(manifest: Manifest, frames: Frames) -> None:
    """Fail-closed if the parquet is stale vs the current source (fingerprint/version), so callers
    never silently serve outdated features (codex P0). ``frames`` must cover races through
    ``manifest.data_through`` (fp-v2 is value-canonical, so any load window that covers the
    materialized range verifies identically — Feature 055)."""
    assert_manifest_compatible(manifest)
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
