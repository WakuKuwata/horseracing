"""Frozen local, strictly past clock/speed and pace additions for research 143.

The trusted local 111 pickle is deserialized, immediately date bounded, and its
mutable Horse frame discarded before any feature builder is called. No DB API
is used. Existing feature implementations are reused without modification.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import pickle
import sys

import numpy as np
import pandas as pd

SOURCE = Path(__file__).resolve()
ROOT = SOURCE.parents[3]
for _package in ("features", "db"):
    sys.path.insert(0, str(ROOT / _package / "src"))
_baseline_spec = importlib.util.spec_from_file_location(
    "_roi142_feature143_baseline", ROOT / "scripts/roi_explore/roi_improvement_142/models.py")
baseline = importlib.util.module_from_spec(_baseline_spec)
sys.modules[_baseline_spec.name] = baseline
_baseline_spec.loader.exec_module(baseline)
from horseracing_features.loader import Frames  # noqa: E402
from horseracing_features import corner_trajectory_features as corner  # noqa: E402
from horseracing_features import pace_features as pace  # noqa: E402
from horseracing_features import pace_scenario_features as scenario  # noqa: E402
from horseracing_features import speed_figure_features as speed  # noqa: E402

KEYS = ["race_id", "horse_id"]
LOWER, UPPER = baseline.LOWER, baseline.UPPER
SPEED_COLUMNS = tuple(pace.PACE_TIME_COLUMNS + speed.SPEED_FIGURE_COLUMNS)
PACE_COLUMNS = tuple(pace.POSITION_STYLE_COLUMNS + scenario.PACE_SCENARIO_COLUMNS + corner.CORNER_TRAJECTORY_COLUMNS)
ADDED_COLUMNS = SPEED_COLUMNS + PACE_COLUMNS
HISTORY_EXCLUDED_RACES = {
    "202504040406": "duplicate horse_number=7 under numeric/nk aliases; no results; whole history race excluded",
    "202504040407": "duplicate horse_number=15 under numeric/nk aliases; missing horse_number=16; whole history race excluded",
}
DEFAULT_SOURCE = ROOT / "artifacts/111-ability-observation/source-frames.pkl"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def source_files() -> list[Path]:
    return [SOURCE, Path(baseline.__file__), Path(baseline.legacy.__file__),
            ROOT / "features/src/horseracing_features/loader.py",
            ROOT / "db/src/horseracing_db/enums.py",
            *[Path(m.__file__) for m in (pace, speed, scenario, corner)]]


def validate_keys(frame: pd.DataFrame, label: str) -> None:
    if frame[KEYS].isna().any().any() or (frame[KEYS].astype(str) == "").any().any():
        raise ValueError(f"{label}: missing exact starter key")
    if frame.duplicated(KEYS).any():
        raise ValueError(f"{label}: duplicate exact starter key")
    if not frame.race_id.astype(str).str.fullmatch(r"\d{12}").all():
        raise ValueError(f"{label}: invalid race ID")


def key_index(frame: pd.DataFrame) -> pd.MultiIndex:
    return pd.MultiIndex.from_frame(frame[KEYS])


def bounded_frames(raw: Frames) -> Frames:
    """Date/exclusion filters precede all validity checks and feature calculations."""
    dates = pd.to_datetime(raw.races.race_date, errors="raise").dt.normalize()
    races = raw.races.loc[dates.between(LOWER, UPPER)].copy()
    races["race_date"] = dates.loc[races.index]
    races = races.loc[~races.race_id.isin(HISTORY_EXCLUDED_RACES)].copy()
    if races.empty or races.race_id.duplicated().any() or races.race_id.isna().any():
        raise ValueError("empty or duplicate bounded race population")
    if not races.race_id.astype(str).str.fullmatch(r"\d{12}").all():
        raise ValueError("invalid bounded race ID")
    ids = frozenset(races.race_id)
    rh = raw.race_horses.loc[raw.race_horses.race_id.isin(ids)].copy()
    rr = raw.race_results.loc[raw.race_results.race_id.isin(ids)].copy()
    validate_keys(rh, "history entries")
    validate_keys(rr, "history results")
    if rh.horse_number.isna().any() or rh.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("unregistered duplicate/missing history horse_number; exclude whole race explicitly")
    if not key_index(rr).isin(key_index(rh)).all():
        raise ValueError("history results have unmatched exact entry keys")
    appearances = rh.merge(races[["race_id", "race_date"]], on="race_id", validate="many_to_one")
    if appearances.duplicated(["horse_id", "race_date"]).any():
        raise ValueError("ambiguous multiple appearances for horse on same day")
    # Parquet represents lists as ndarray; the existing builders require list/tuple.
    rr["corner_orders"] = rr.corner_orders.map(lambda x: x.tolist() if isinstance(x, np.ndarray) else x)
    return Frames(races=races.reset_index(drop=True), race_horses=rh.reset_index(drop=True),
                  race_results=rr.reset_index(drop=True))


def validate_reference(frames: Frames, reference: pd.DataFrame) -> None:
    validate_keys(reference, "reference")
    if not key_index(reference).isin(key_index(frames.race_horses)).all():
        raise ValueError("reference key missing in frozen history; no alias matching allowed")
    meta = reference[[*KEYS, "race_date", "horse_number"]].merge(
        frames.race_horses[[*KEYS, "horse_number"]].merge(
            frames.races[["race_id", "race_date"]], on="race_id", validate="many_to_one"),
        on=KEYS, suffixes=("_target", "_source"), validate="one_to_one")
    if not (pd.to_datetime(meta.race_date_target).dt.normalize() == meta.race_date_source).all():
        raise ValueError("reference/source race date differs")
    if not (meta.horse_number_target == meta.horse_number_source).all():
        raise ValueError("reference/source horse_number differs")
    source = frames.race_horses.loc[frames.race_horses.race_id.isin(reference.race_id)
                                     & frames.race_horses.entry_status.eq("started")]
    if len(source) != len(reference) or not key_index(source).isin(key_index(reference)).all():
        raise ValueError("reference/source complete started field differs")


def join_additions(reference: pd.DataFrame, additions: pd.DataFrame) -> pd.DataFrame:
    """Exact one-to-one key join; a missing value is retained, a missing row fails."""
    validate_keys(reference, "reference")
    validate_keys(additions, "additions")
    if len(reference) != len(additions) or not key_index(reference).isin(key_index(additions)).all():
        raise ValueError("addition key coverage mismatch")
    if set(ADDED_COLUMNS) & set(reference.columns):
        raise ValueError("addition columns already present")
    if set(additions.columns) != set(KEYS + list(ADDED_COLUMNS)):
        raise ValueError("addition feature contract differs")
    out = reference.merge(additions, on=KEYS, how="left", sort=False, validate="one_to_one")
    pd.testing.assert_frame_equal(out[KEYS].reset_index(drop=True), reference[KEYS].reset_index(drop=True))
    return out


def build_additions(frames: Frames, reference: pd.DataFrame) -> pd.DataFrame:
    frames = bounded_frames(frames)
    validate_reference(frames, reference)
    targets = frozenset(reference.race_id)
    p = pace.build_pace_features(frames, target_race_ids=targets)
    parts = [p[KEYS + list(pace.PACE_TIME_COLUMNS + pace.POSITION_STYLE_COLUMNS)],
             speed.build_speed_figure_features(frames, target_race_ids=targets),
             scenario.build_pace_scenario_features(frames, pace=p),
             corner.build_corner_trajectory_features(frames, target_race_ids=targets)]
    out = reference[KEYS].copy()
    for part in parts:
        validate_keys(part, "builder output")
        if not key_index(reference).isin(key_index(part)).all():
            raise ValueError("builder lost target starter")
        out = out.merge(part, on=KEYS, how="left", sort=False, validate="one_to_one")
    out = out[KEYS + list(ADDED_COLUMNS)]
    if np.isinf(out[list(ADDED_COLUMNS)].to_numpy(float)).any():
        raise ValueError("infinite addition feature")
    join_additions(reference, out)
    return out


def export_from_local(source_path: Path, rows_path: Path, output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    if not output_dir.is_relative_to(ROOT / "artifacts/143-existing-feature-roi"):
        raise ValueError("outputs must be within artifacts/143-existing-feature-roi")
    paths = {str(p.resolve()): sha(p) for p in [source_path, rows_path, *source_files()]}
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("feature export directory must be empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    # Only the already trusted, repository-owned, frozen local source is accepted.
    if source_path.resolve() != DEFAULT_SOURCE.resolve():
        raise ValueError("only the frozen 111 local source pickle is accepted")
    with source_path.open("rb") as stream:
        raw = pickle.load(stream)
    frames = bounded_frames(raw)
    del raw
    reference = baseline.read_rows(rows_path)
    additions = build_additions(frames, reference)
    files = {}
    for name, frame in (("races", frames.races), ("race_horses", frames.race_horses),
                        ("race_results", frames.race_results), ("additions", additions)):
        path = output_dir / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        files[name] = dict(path=str(path.relative_to(ROOT)), sha256=sha(path), rows=len(frame))
    if any(sha(Path(p)) != h for p, h in paths.items()):
        raise ValueError("source/input changed during feature export")
    coverage = {}
    for year, group in reference.groupby("year"):
        mask = reference.year.eq(year).to_numpy()
        coverage[str(year)] = {c: dict(known=int(additions.loc[mask, c].notna().sum()),
                                      total=len(group), fraction=float(additions.loc[mask, c].notna().mean()))
                               for c in ADDED_COLUMNS}
    manifest = dict(version=1, research_only=True, can_adopt=False, untouched_holdout=False,
                    source_kind="frozen_111_local_pickle_no_database_reads", lower=LOWER, upper=UPPER,
                    source_min_date=frames.races.race_date.min().date().isoformat(),
                    source_max_date=frames.races.race_date.max().date().isoformat(),
                    mutable_horses_discarded=True, same_day_excluded=True,
                    target_rows=len(reference), target_races=int(reference.race_id.nunique()),
                    exact_target_key_coverage=1.0, source_and_dependency_sha256=paths,
                    history_excluded_races=HISTORY_EXCLUDED_RACES,
                    target_excluded_races=baseline.EXCLUDED_RACES,
                    speed_columns=list(SPEED_COLUMNS), pace_columns=list(PACE_COLUMNS),
                    files=files, coverage_by_year=coverage)
    with (output_dir / "manifest.json").open("x") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export"])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export_from_local(args.source, args.rows, args.output)
    print(json.dumps({k: result[k] for k in ("target_rows", "target_races", "source_min_date", "source_max_date")}))


if __name__ == "__main__":
    main()
