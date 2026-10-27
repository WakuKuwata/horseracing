"""Rebuild all histories from a bounded 2007+ raw-column snapshot, then enrich DSL inputs.

Never reads the 1986 exploration dataset. Existing 108 model probabilities are attached
for traceability, but are not authorized for strict-history pattern scoring: their
historical runtime pool override was not retained in the attestation.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["HORSERACING_FEATURE_POOL_START"] = "2007-01-01"

import numpy as np
import pandas as pd
from horseracing_training import market_ev

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/roi_strict2007_20260930/input"
BASE = ROOT / "artifacts/market_ev/rows_2007.parquet"
BUNDLE = ROOT / "artifacts/oof/8bdde26857f62c5571ef45b02954836dacae7e4a2ea9174c12eb0c9209fb691f/bundle.json"
RAW_COLUMNS = [
    "race_id", "horse_id", "horse_number", "frame", "sex", "age", "weight", "weight_diff",
    "jockey_weight", "odds", "popularity", "running_style", "jockey_id", "trainer_id",
    "race_date", "venue_code", "race_number", "distance", "track_type", "going", "weather",
    "race_class", "grade", "prize_money", "finish_order", "result_status", "margin_sec",
    "last_3f", "sire_line", "damsire_line",
]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                              default=lambda x: x.item() if hasattr(x, "item") else str(x)) + "\n")


def pattern_fields(value, found: set[str]) -> None:
    if isinstance(value, list):
        for child in value:
            pattern_fields(child, found)
    elif isinstance(value, dict):
        for key in ("field", "by"):
            if isinstance(value.get(key), str):
                found.add(value[key])
        for child in value.values():
            pattern_fields(child, found)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "rows.parquet").exists():
        raise SystemExit("Refusing to overwrite the strict2007 input.")
    inputs = [BASE, BUNDLE, Path(market_ev.__file__), Path(__file__),
              OUT / "model108_provenance.json", ROOT / "scripts/roi_explore/export_rows_2007.py"]
    inputs.extend(sorted((ROOT / "artifacts/roi_explore/patterns").glob("*.json")))
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in inputs}
    write_json(OUT / "build_freeze.json", {
        "created_at": datetime.now(timezone.utc).isoformat(), "input_sha256": hashes,
        "history_start": "2007-01-01", "data_through": "2026-09-22",
        "build": "Read only raw columns of rows_2007; recompute every history before adding same-day and OOF columns.",
        "model_patterns_allowed": False,
    })
    raw = pd.read_parquet(BASE, columns=RAW_COLUMNS)
    dates = pd.to_datetime(raw.race_date)
    assert dates.min() >= pd.Timestamp("2007-01-01")
    assert dates.max() <= pd.Timestamp("2026-09-22")
    assert not raw[["horse_id", "race_date"]].duplicated().any()
    print(f"Rebuilding {len(raw):,} rows, history floor 2007-01-01", flush=True)
    df = market_ev.build_features(raw)
    df["n_winners"] = df.groupby("race_id")["won"].transform("sum").astype(int)
    df["dead_heat"] = df.n_winners != 1
    # Full parity makes pre-2007 contamination in an inherited aggregate visible.
    prior = pd.read_parquet(BASE).sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    mismatch = {}
    for column in prior:
        a, b = prior[column], df[column]
        if column == "race_date":
            equal = pd.to_datetime(a).eq(pd.to_datetime(b))
        elif pd.api.types.is_numeric_dtype(a):
            equal = np.isclose(a.to_numpy(dtype=float, na_value=np.nan),
                               b.to_numpy(dtype=float, na_value=np.nan), rtol=1e-10, atol=1e-10, equal_nan=True)
        else:
            equal = a.fillna("<NULL>").eq(b.fillna("<NULL>"))
        if not np.all(equal):
            mismatch[column] = int((~np.asarray(equal)).sum())
    assert not mismatch, mismatch
    parity_columns = list(prior.columns)
    del prior, raw
    # Cold starts are defined at the permitted history boundary, not at true lifetime debut.
    first = df.sort_values(["horse_id", "race_date", "race_id"]).drop_duplicates("horse_id")
    assert (first.career_starts == 0).all() and (first.career_wins == 0).all()
    assert first.prev_finish.isna().all() and first.days_since_last.isna().all()
    cold = {"horse_first_rows": len(first), "horse_first_starts_nonzero": 0,
            "horse_first_prior_finish_nonmissing": 0, "human_firstday": {},
            "horse_examples": first.head(8)[[
                "race_id", "horse_id", "race_date", "career_starts", "career_wins", "prev_finish",
            ]].replace({np.nan: None}).to_dict("records")}
    for key, prefix in (("jockey_id", "jockey"), ("trainer_id", "trainer"), ("combo_id", "combo")):
        present = df[df[key].notna()]
        initial = present[present.race_date.eq(present.groupby(key).race_date.transform("min"))]
        assert (initial[f"{prefix}_starts_all"] == 0).all()
        assert (initial[f"{prefix}_wins_all"] == 0).all()
        assert initial[f"{prefix}_win_rate_all"].isna().all()
        cold["human_firstday"][prefix] = {"rows": len(initial), "starts_nonzero": 0,
            "wins_nonzero": 0, "win_rate_nonmissing": 0,
            "examples": initial.head(4)[[key, "race_date", f"{prefix}_starts_all",
                                         f"{prefix}_win_rate_all"]].replace({np.nan: None}).to_dict("records")}
    del first
    # Same-day features depend only on earlier race numbers at the same venue.
    df = df.sort_values(["race_date", "venue_code", "race_number", "race_id", "horse_number"]).reset_index(drop=True)
    jockey = df.groupby(["race_date", "venue_code", "jockey_id"], sort=False)["won"]
    df["jockey_wins_today_before"] = (jockey.cumsum() - df.won).astype(float)
    df["jockey_rides_today_before"] = jockey.cumcount().astype(float)
    races = df.groupby(["race_date", "venue_code", "race_number", "race_id"], sort=True).size().reset_index(name="n")
    winners = df[df.won & (df.n_winners == 1)].drop_duplicates("race_id").set_index("race_id")
    races["fav_won"] = races.race_id.map(winners.is_fav).astype(float)
    races["winner_pop"] = races.race_id.map(winners.popularity).astype(float)
    races["winner_front"] = races.race_id.map(winners.running_style.isin(["逃げ", "先行"])).astype(float)
    grp = races.groupby(["race_date", "venue_code"], sort=False)
    races["day_prev_n"] = grp.cumcount().astype(float)
    same_day = ["day_prev_n"]
    for column, output in (("fav_won", "day_prev_fav_win_rate"),
                           ("winner_pop", "day_prev_winner_pop_mean"),
                           ("winner_front", "day_prev_winner_style_front_share")):
        numerator = grp[column].cumsum() - races[column].fillna(0.)
        denominator = grp[column].transform(lambda x: x.notna().cumsum()) - races[column].notna().astype(float)
        races[output] = np.where(denominator > 0, numerator / denominator.replace(0, np.nan), np.nan)
        same_day.append(output)
    assert races.loc[races.day_prev_n == 0, same_day[1:]].isna().all().all()
    df = df.merge(races[["race_id", *same_day]], on="race_id", how="left", validate="many_to_one")
    # Attach sealed predictions exactly; callers must honor the explicit provenance gate.
    bundle = json.loads(BUNDLE.read_text())
    predictions = pd.DataFrame([
        (race_id, horse_id, v["win"], v["top2"], v["top3"])
        for race_id, horses in bundle["predictions"].items() for horse_id, v in horses.items()
    ], columns=["race_id", "horse_id", "p", "p_top2", "p_top3"])
    del bundle
    df = df.merge(predictions, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    df["in_bundle"] = df.race_id.isin(set(predictions.race_id))
    del predictions
    ranked = df.sort_values(["race_id", "p", "horse_number"], ascending=[True, False, True])
    df["p_rank"] = (ranked.groupby("race_id").cumcount() + 1).reindex(df.index).where(df.p.notna())
    df["ev"] = df.p * df.odds
    df["p_over_q"] = df.p / df.q
    p1 = df[df.p_rank == 1].set_index("race_id")
    p2 = df[df.p_rank == 2].set_index("race_id")
    df["p_gap12"] = df.race_id.map(p1.p) - df.race_id.map(p2.p)
    df["model_fav_is_market_fav"] = df.race_id.map(p1.is_fav).astype(float)
    df["place_hit"] = np.where(df.field_size >= 8, df.finish_order <= 3,
                               np.where(df.field_size >= 5, df.finish_order <= 2, np.nan)).astype(float)
    df = df.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    df["race_date"] = df.race_date.dt.strftime("%Y-%m-%d")
    required = {"race_id", "horse_id", "horse_number", "race_date", "race_ok", "dead_heat", "won", "year", "odds", "is_fav"}
    for path in sorted((ROOT / "artifacts/roi_explore/patterns").glob("*.json")):
        pattern_fields(json.loads(path.read_text()), required)
    assert not required.difference(df.columns), sorted(required.difference(df.columns))
    assert hashes == {name: sha(ROOT / name) for name in hashes}
    df.to_parquet(OUT / "rows.parquet", index=False)
    provenance = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strict2007_histories_rebuilt": True,
        "training_start": "2007-01-01", "history_start": "2007-01-01",
        "date_min": df.race_date.min(), "date_max": df.race_date.max(),
        "rows": len(df), "races": int(df.race_id.nunique()), "columns": list(df.columns),
        "input_sha256": hashes, "rows_sha256": sha(OUT / "rows.parquet"),
        "original_base_rebuild_parity_columns": parity_columns, "rebuild_mismatches": mismatch,
        "required_dsl_columns": sorted(required), "missing_dsl_columns": [],
        "model_training_start_verified": True,
        "model_history_start_verified": False,
        "model_patterns_allowed": False,
        "model108_provenance": "model108_provenance.json",
        "model_rows_attached": int(df.p.notna().sum()),
        "model_use_note": "108 fold train hashes match 2007+ sets; historical FEATURE_POOL_START override not recorded, so p columns are attached for traceability but strict-history policy scoring must hold those patterns.",
        "cold_start_audit": cold,
        "limitations": [
            "2007 boundary left-truncates career history; is_debut means no start in permitted history.",
            "Snapshot input values may contain retrospective corrections; this is not an as-of snapshot reconstruction.",
            "Saved odds include non-final values in recent periods; original settlement caveat remains.",
            "Same-day ordering assumes increasing race_number at a venue precedes the current race; publication times absent.",
        ],
    }
    write_json(OUT / "provenance.json", provenance)
    print(json.dumps({k: provenance[k] for k in ("rows", "races", "date_min", "date_max", "rows_sha256", "model_patterns_allowed", "missing_dsl_columns")}, indent=2), flush=True)


if __name__ == "__main__":
    main()
