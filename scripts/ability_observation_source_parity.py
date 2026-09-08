"""Read-only audit of canonical ordering versus existing speed-figure inputs."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import pickle

import numpy as np
import pandas as pd

from horseracing_features.speed_figure_features import _figure_runs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/111-ability-observation/source-frames.pkl"
OUT = ROOT / "specs/111-ability-observation/evidence/source-figure-parity.json"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def state(src):
    src = src.sort_values(["horse_id", "race_date"], kind="stable").reset_index(drop=True)
    if src.duplicated(["horse_id", "race_date"]).any():
        raise ValueError("Ambiguous source horse/day")
    g = src.groupby("horse_id", sort=False)
    best = src.spdfig_z == g.spdfig_z.cummax()
    src["best_date"] = src.race_date.where(best).groupby(src.horse_id).ffill()
    src["last_date"] = src.race_date
    return src


def ages(targets, src):
    out = pd.merge_asof(
        targets.sort_values("race_date", kind="stable"),
        src[["horse_id", "race_date", "best_date", "last_date"]].sort_values("race_date", kind="stable"),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    )
    for c in ("best", "last"):
        out[c + "_age"] = (out.race_date - out[c + "_date"]).dt.days
    return out.sort_values(["race_id", "horse_id"], kind="stable").reset_index(drop=True)


def main():
    if OUT.exists():
        raise FileExistsError("Parity evidence already exists")
    source_hash = digest(SOURCE)
    method_hash = digest(__file__)
    with SOURCE.open("rb") as f:
        frames = pickle.load(f)
    normalized = frames.races.copy()
    normalized["race_date"] = pd.to_datetime(normalized.race_date).dt.normalize()
    canonical = replace(frames, races=normalized.sort_values("race_id", kind="stable"),
                        race_results=frames.race_results.sort_values(["race_id", "horse_id"], kind="stable"))
    original = state(_figure_runs(frames))
    ordered = state(_figure_runs(canonical))
    keys = ["horse_id", "race_date"]
    universe = frames.race_results[["race_id", "horse_id"]].merge(
        normalized[["race_id", "race_date"]], on="race_id", validate="many_to_one")
    if universe.duplicated(keys).any():
        raise ValueError("Ambiguous source appearance population")
    valid_original = pd.MultiIndex.from_frame(universe[keys]).isin(pd.MultiIndex.from_frame(original[keys]))
    valid_canonical = pd.MultiIndex.from_frame(universe[keys]).isin(pd.MultiIndex.from_frame(ordered[keys]))
    valid_mismatch = int((valid_original != valid_canonical).sum())
    merged = original.merge(ordered, on=keys, how="outer", suffixes=("_original", "_canonical"),
                            validate="one_to_one", indicator=True)
    key_mismatch = int((merged["_merge"] != "both").sum())
    paired = merged[merged["_merge"] == "both"]
    z_diff = np.abs(paired.spdfig_z_original.to_numpy() - paired.spdfig_z_canonical.to_numpy())
    best_date_diff = int((paired.best_date_original != paired.best_date_canonical).sum())
    targets = frames.race_horses[["race_id", "horse_id"]].merge(
        normalized[["race_id", "race_date"]], on="race_id", validate="many_to_one")
    old_ages, new_ages = ages(targets, original), ages(targets, ordered)
    pd.testing.assert_frame_equal(old_ages[["race_id", "horse_id"]], new_ages[["race_id", "horse_id"]], check_exact=True)
    age_diffs = {}
    for c in ("best_age", "last_age"):
        a, b = old_ages[c].to_numpy(), new_ages[c].to_numpy()
        equal = (a == b) | (np.isnan(a) & np.isnan(b))
        age_diffs[c] = int((~equal).sum())
    z_max = float(z_diff.max()) if len(z_diff) else 0.0
    unchanged = source_hash == digest(SOURCE) and method_hash == digest(__file__)
    result = {
        "source_path": str(SOURCE), "source_sha256": source_hash,
        "method_path": str(Path(__file__).resolve()), "method_sha256": method_hash,
        "source_and_method_unchanged": unchanged,
        "comparison": "Existing _figure_runs(raw Frames) versus identical Frames with normalized race dates and canonical race/result ordering used by observation builder",
        "n_source_appearances": len(universe), "n_valid_original": len(original),
        "n_valid_canonical": len(ordered), "valid_mask_mismatch": valid_mismatch,
        "valid_key_mismatch": key_mismatch,
        "z_exact_mismatch": int((z_diff != 0).sum()), "z_max_abs_difference": z_max,
        "source_latest_best_date_mismatch": best_date_diff,
        "n_target_appearances": len(targets), "asof_age_mismatch": age_diffs,
        "rounding_tolerance": 1e-10,
        "acceptable": bool(unchanged and valid_mismatch == 0 and key_mismatch == 0
                           and z_max <= 1e-10 and all(n == 0 for n in age_diffs.values())),
        "can_adopt": False, "predictive_effect_measured": False,
        "notes": ["Every target horse lookup excludes the whole target day.",
                  "1e-10 is a numerical parity tolerance, not a prediction-quality threshold.",
                  "No original driver, feature builder, source frame, or training matrix is modified."],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("x") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["acceptable"]:
        raise RuntimeError("Canonical source parity requires review")


if __name__ == "__main__":
    main()
