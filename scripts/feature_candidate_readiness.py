"""Read-only novelty/coverage audit; no model training or predictive-effect claim."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import pickle

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from horseracing_db.enums import EntryStatus
from horseracing_db.session import create_db_engine
from horseracing_features.history import build_history_features
from horseracing_features.loader import load_frames
from horseracing_features.speed_figure_features import _figure_runs

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "specs/110-feature-pruning/evidence/ability-weight-readiness.json"
INPUT = ROOT / "artifacts/110-feature-pruning/readiness-inputs.pkl"


def before(targets, source):
    return pd.merge_asof(targets.sort_values("race_date", kind="stable"),
        source.sort_values("race_date", kind="stable"), on="race_date", by="horse_id",
        direction="backward", allow_exact_matches=False)


def describe(values):
    valid = values.dropna()
    return {"n": len(values), "n_valid": len(valid), "missing_rate": float(values.isna().mean()),
        "quantiles": ({str(k): float(v) for k, v in valid.quantile([0, .1, .5, .9, 1]).items()}
                      if len(valid) else {})}


def main():
    if OUT.exists() or INPUT.exists():
        raise FileExistsError("Audit evidence already exists")
    engine = create_db_engine(os.environ.get("DATABASE_URL",
        "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"),
        isolation_level="REPEATABLE READ")
    with Session(engine) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        frames = load_frames(session, end_date=dt.date(2026, 8, 23))
    appearances = frames.race_horses.merge(frames.races[["race_id", "race_date"]], on="race_id")
    # Check the entire candidate population before discarding invalid measurements.
    if appearances.duplicated(["horse_id", "race_date"]).any():
        raise ValueError("Ambiguous same-horse/same-day appearances; audit cannot choose one")
    started = appearances[appearances.entry_status == EntryStatus.STARTED].copy()
    targets = started[["race_id", "horse_id", "race_date"]].copy()
    history = build_history_features(frames)[["race_id", "horse_id", "days_since_last"]]
    speed = _figure_runs(frames).sort_values(["horse_id", "race_date"], kind="stable")
    weights = started[["horse_id", "race_date", "weight"]].copy()
    weights["weight"] = pd.to_numeric(weights.weight, errors="coerce")
    weights = weights[weights.weight.between(200, 800)].sort_values(["horse_id", "race_date"], kind="stable")
    INPUT.parent.mkdir(parents=True, exist_ok=True)
    with INPUT.open("xb") as f:
        pickle.dump({"targets": targets, "history": history, "speed": speed, "weights": weights}, f, protocol=5)

    g = speed.groupby("horse_id", sort=False)
    speed["last_valid_date"] = speed.race_date
    # Existing, clipped spdfig_z. A tie updates to the latest attainment date.
    is_best = speed.spdfig_z == g.spdfig_z.cummax()
    speed["best_date"] = speed.race_date.where(is_best).groupby(speed.horse_id).ffill()
    speed["speed_sd"] = g.spdfig_z.expanding(min_periods=2).std(ddof=0).reset_index(level=0, drop=True)
    out = before(targets, speed[["horse_id", "race_date", "last_valid_date", "best_date", "speed_sd"]])
    out["last_valid_age"] = (out.race_date - out.last_valid_date).dt.days
    out["best_age"] = (out.race_date - out.best_date).dt.days
    out = out.merge(history, on=["race_id", "horse_id"], validate="one_to_one")
    valid = out[["last_valid_age", "best_age", "days_since_last"]].notna().all(axis=1)
    assert (out.loc[valid, "best_age"] >= out.loc[valid, "last_valid_age"]).all()
    assert (out.loc[valid, "last_valid_age"] >= out.loc[valid, "days_since_last"]).all()
    assert (out.loc[valid, "days_since_last"] > 0).all()

    gw = weights.groupby("horse_id", sort=False)
    n_before = gw.cumcount()
    mean_before = (gw.weight.cumsum() - weights.weight) / n_before.replace(0, np.nan)
    weights["weight_deviation"] = (weights.weight - mean_before).where(n_before >= 3)
    # Lookup into an earlier date then makes weight here the previous observed weight.
    out = before(out, weights[["horse_id", "race_date", "weight_deviation"]])
    out = out[out.race_date >= pd.Timestamp("2019-01-01")]
    cols = ["last_valid_age", "best_age", "speed_sd", "weight_deviation"]
    groups = {"all": out, "canonical": out[~out.horse_id.str.startswith("nk:")],
        "nk": out[out.horse_id.str.startswith("nk:")],
        "2019_2024": out[out.race_date.dt.year <= 2024],
        "2025_plus": out[out.race_date.dt.year >= 2025]}
    summaries = {name: {c: describe(sub[c]) for c in cols} for name, sub in groups.items() if len(sub)}
    comparisons = {}
    for a, b in [("last_valid_age", "days_since_last"), ("best_age", "days_since_last")]:
        eligible = out[[a, b]].notna().all(axis=1)
        equal = np.isclose(out.loc[eligible, a], out.loc[eligible, b], atol=1e-12, rtol=0)
        comparisons[f"{a}_vs_{b}"] = {"n_both_valid": int(eligible.sum()),
            "n_different": int((~equal).sum()), "equal_rate": float(equal.mean()) if len(equal) else None}
    h = hashlib.sha256()
    with INPUT.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    result = {"checked_on": "2026-09-07", "window": ["2019-01-01", "2026-08-23"],
        "can_adopt": False, "predictive_effect_measured": False, "source_pool_start": str(frames.races.race_date.min()),
        "input_path": str(INPUT), "input_sha256": h.hexdigest(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "definitions": {"last_valid_age": "Days since latest valid past clipped speed figure",
            "best_age": "Days since latest attainment of highest past clipped speed figure; ties use latest date",
            "speed_sd": "Expanding population standard deviation (ddof=0), at least 2 prior valid speed figures",
            "weight_deviation": "Previous valid body weight minus mean of at least 3 valid observations BEFORE that previous measurement; started and 200..800kg"},
        "summaries": summaries, "comparisons": comparisons,
        "notes": ["All lookups exclude the target day. No current-race result is a candidate input.",
            "Different values do not prove independent information or accuracy improvement.",
            "Speed SD also reflects changing race conditions; it is not an estimator standard error.",
            "This read-only audit uses a separate live DB snapshot from the feature-pruning experiment.",
            "Canonical/nk and time strata describe coverage, not causal biology or predictive effects."]}
    with OUT.open("x") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    print(json.dumps({"summaries": summaries["all"], "comparisons": comparisons}, indent=2))


if __name__ == "__main__":
    main()
