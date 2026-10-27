"""Isolated repair audit for R09 X09; leaves the October 4 study unchanged.

The original X09 block carries a dirt array across a row-order change. Rebuild
both versions, require old-artifact parity, and replace only X09 for the original
13-cell sieve. No model fitting or new rule/threshold search is performed.
"""
from __future__ import annotations

import os
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[name] = "1"

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
import pyarrow as pa
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
OLD = ROOT / "scripts/roi_explore/missed_20261004"
sys.path.insert(0, str(OLD))
import r09_cells as source
import r09_new_info as original

OUT = ROOT / "artifacts/roi_comprehensive_20261005/r09_x09"
CELL = "X09_first_dirt_sire_dirt_top"
pa.set_cpu_count(1)
pa.set_io_thread_count(1)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save(path, value):
    with Path(path).open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2,
                  default=lambda x: x.item() if hasattr(x, "item") else str(x))
        handle.write("\n")


def reconstruct(d, *, repaired):
    """Faithfully reproduce the original X09 block except its one bad array."""
    order = d.sort_values(["horse_id", "race_date", "race_id"]).index
    sorted_d = d.loc[order]
    sorted_dirt = sorted_d.track_type.eq("ダ").astype(float)
    prior_dirt = (sorted_dirt.groupby(sorted_d.horse_id).cumsum() - sorted_dirt).reindex(d.index).to_numpy()
    prior_starts = sorted_d.groupby("horse_id").cumcount().reindex(d.index).to_numpy()
    dirt = d.track_type.eq("ダ").to_numpy(dtype=float) if repaired else sorted_dirt.to_numpy()
    sc, _ = pd.factorize(d.sire_n.fillna("__NA__"))
    hc, _ = pd.factorize(d.horse_id)
    day = pd.to_datetime(d.race_date).to_numpy().astype("datetime64[D]").astype(np.int64)
    win = d.won.to_numpy(dtype=float)
    q = d.q.fillna(0).to_numpy(dtype=float)
    sire = source.AsOf(sc, day, {"w": win*dirt, "q": q*dirt}).query(sc, day)
    horse = source.AsOf(hc, day, {"w": win*dirt, "q": q*dirt}).query(hc, day)
    sw, sq = sire["w"]-horse["w"], sire["q"]-horse["q"]
    ae = np.where(d.sire_n.notna().to_numpy() & (sq >= 5.), (sw+1.)/(sq+1.), np.nan)
    first = (dirt == 1.) & (prior_starts >= 1) & (prior_dirt == 0) & d.birth_year.ge(2005).to_numpy()
    eligible = first & np.isfinite(ae)
    disc = eligible & d.year.between(2010, 2015).to_numpy()
    threshold = float(np.quantile(ae[disc], 2/3))
    return {"cell": eligible & (ae >= threshold), "ae": ae, "sire_w": sw, "sire_q": sq,
            "threshold": threshold, "first": first, "dirt": dirt}


def independent_history(d):
    """Separate daily group/cumsum implementation: no AsOf helper or sorting keys."""
    x = pd.DataFrame({"horse": d.horse_id, "sire": d.sire_n.fillna("__NA__"),
                      "day": pd.to_datetime(d.race_date),
                      "w": d.won.astype(float)*d.track_type.eq("ダ"),
                      "q": d.q.fillna(0)*d.track_type.eq("ダ")})
    before = {}
    for key in ("sire", "horse"):
        daily = x.groupby([key, "day"], sort=True)[["w", "q"]].sum()
        past = daily.groupby(level=0).cumsum() - daily
        query = pd.MultiIndex.from_frame(x[[key, "day"]])
        before[key] = past.reindex(query).to_numpy()
    return before["sire"] - before["horse"]


def main():
    started = time.monotonic()
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "freeze.json").exists():
        raise RuntimeError("Repair audit already frozen; refusing overwrite")
    old_cells_path = original.OUT / "cells.parquet"
    old_sieve_path = original.OUT / "sieve.json"
    inputs = [original.ROWS, old_cells_path, old_sieve_path, original.PREREG,
              original.OUT / "cells_summary.json"]
    sources = [Path(__file__), Path(source.__file__), Path(original.__file__)]
    input_hashes = {str(p): sha(p) for p in inputs}
    source_hashes = {str(p): sha(p) for p in sources}
    save(OUT / "freeze.json", {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Deterministic X09 row-alignment bug repair; not a newly discovered betting rule",
        "history_start": "2007-01-01", "evaluation_start": 2010,
        "evaluation_through": original.SETTLE_LAST,
        "change": "At the original X09 block, derive dirt from restored d.track_type instead of stale is_dirt",
        "threshold": "Unchanged recipe: discovery 2010-2015 2/3 quantile recomputed from repaired feature",
        "comparison": "Original 13-cell D/Q sieve, 4000 year-stratified day replicates; replace only X09",
        "training_runs": 0, "worker_threads": 1,
        "after_outcome_repair": True, "untouched_holdout": False, "can_adopt": False,
        "input_sha256": input_hashes, "source_sha256": source_hashes,
    })
    cols = ["race_id", "horse_id", "race_date", "year", "track_type", "won", "q", "odds", "race_ok", "dead_heat"]
    d = pd.read_parquet(original.ROWS, columns=cols)
    d["horse_id"] = d.horse_id.astype(str); d["race_id"] = d.race_id.astype(str)
    assert pd.to_datetime(d.race_date).min() >= pd.Timestamp("2007-01-01")
    assert not d[["horse_id", "race_date"]].duplicated().any()
    engine = create_engine(original.DB_URL)
    with engine.connect() as con:
        con.execute(text("SET TRANSACTION READ ONLY"))
        horses = pd.read_sql(text("SELECT horse_id, birth_year, sire_name FROM horses"), con)
    engine.dispose()
    horses["horse_id"] = horses.horse_id.astype(str)
    horses = horses.loc[horses.horse_id.isin(d.horse_id)].copy()
    horses.to_parquet(OUT / "horse_metadata.parquet", index=False)
    d = d.merge(horses, on="horse_id", how="left", validate="many_to_one")
    d["sire_n"] = d.sire_name.map(source._norm)
    old = reconstruct(d, repaired=False)
    fixed = reconstruct(d, repaired=True)
    archived = pd.read_parquet(old_cells_path)
    assert archived[["race_id", "horse_id"]].equals(d[["race_id", "horse_id"]])
    old_mask = archived[CELL].to_numpy(bool)
    assert np.array_equal(old["cell"], old_mask), "Cannot reproduce historical X09 membership with current metadata"
    assert np.allclose(old["ae"], archived.sire_dirt_ae.to_numpy(float), rtol=1e-10, atol=1e-9, equal_nan=True)
    independent = independent_history(d)
    np.testing.assert_allclose(fixed["sire_w"], independent[:, 0], rtol=1e-9, atol=1e-7)
    np.testing.assert_allclose(fixed["sire_q"], independent[:, 1], rtol=1e-9, atol=1e-7)
    # Change every label/market probability on and after one diagnostic date.
    # Strictly-before histories on that date (and all earlier dates) must stay identical.
    pivot = str(d.loc[d.year.ge(2019), "race_date"].min())
    mutated = d.copy()
    future = mutated.race_date.ge(pivot)
    mutated.loc[future, "won"] = ~mutated.loc[future, "won"].astype(bool)
    mutated.loc[future, "q"] = mutated.loc[future, "q"]*.37+.01
    perturb = reconstruct(mutated, repaired=True)
    unaffected = d.race_date.le(pivot).to_numpy()
    assert np.allclose(fixed["ae"][unaffected], perturb["ae"][unaffected], equal_nan=True)
    assert np.array_equal(fixed["cell"][unaffected], perturb["cell"][unaffected])
    assert fixed["threshold"] == perturb["threshold"]
    del mutated, perturb, independent
    fixed_cells = archived.copy()
    fixed_cells[CELL] = fixed["cell"]
    fixed_cells["sire_dirt_ae"] = fixed["ae"]
    fixed_cells.to_parquet(OUT / "repaired_cells.parquet", index=False)
    assert archived.drop(columns=[CELL, "sire_dirt_ae"]).equals(fixed_cells.drop(columns=[CELL, "sire_dirt_ae"]))
    ens_parts = []
    pred_hashes = {}
    keys = pd.MultiIndex.from_frame(d[["race_id", "horse_id"]])
    for tag in original.ENS_RUNS.values():
        path = original.RES / tag / "predictions.parquet"
        pred_hashes[str(path)] = sha(path)
        pred = pd.read_parquet(path, columns=["race_id", "horse_id", "pred"]).set_index(["race_id", "horse_id"])
        ens_parts.append(1+pred.pred.reindex(keys).to_numpy()/100)
    ens = np.mean(ens_parts, axis=0)
    del ens_parts
    pop = original.population_mask(d, ens)
    assert not d.loc[pop, "dead_heat"].any()
    ph = ens/d.odds.to_numpy(float)
    for column in source.CELL_IDS:
        d[column] = fixed_cells[column].to_numpy()
    periods = original.build_periods(d, pop, d.won.to_numpy(dtype=float), ph, list(source.CELL_IDS))
    new_sieve = original.sieve(periods, list(source.CELL_IDS), original.BOOT_B, original.BOOT_SEED)
    old_sieve = json.loads(old_sieve_path.read_text())
    for cid in source.CELL_IDS:
        if cid == CELL:
            continue
        for window in original.PERIODS:
            for metric in ("L_adj", "se", "ci95"):
                assert np.allclose(new_sieve[cid][window][metric], old_sieve["cells"][cid][window][metric]), (cid, window, metric)
    comparisons = []
    for window, (lo, hi) in original.PERIODS.items():
        region = pop & d.year.between(lo, hi).to_numpy()
        for version, mask, result in (("original", old_mask, old_sieve["cells"][CELL]),
                                      ("repaired", fixed["cell"], new_sieve[CELL])):
            m = region & mask
            comparisons.append({"period": window, "version": version, "n": int(m.sum()),
                                "hits": int(d.loc[m, "won"].sum()),
                                "stored_odds_roi": float((d.loc[m, "won"]*d.loc[m, "odds"]).sum()/m.sum()),
                                "L_adj": result[window]["L_adj"], "ci95": result[window]["ci95"],
                                "se": result[window]["se"],
                                "selected_on_turf": int((m & d.track_type.ne("ダ").to_numpy()).sum())})
    changed = (old_mask != fixed["cell"])
    summary = {
        "bug_confirmed": True, "rows": len(d),
        "dirt_vector_mismatches": int(np.count_nonzero(old["dirt"] != fixed["dirt"])),
        "original_selected": int(old_mask.sum()), "repaired_selected": int(fixed["cell"].sum()),
        "membership_changes": int(changed.sum()), "shared_membership": int((old_mask & fixed["cell"]).sum()),
        "old_discovery_quantile": old["threshold"], "repaired_discovery_quantile": fixed["threshold"],
        "original_artifact_reproduced": True, "independent_daily_cumulative_matches_all_rows": True,
        "same_day_and_future_label_invariance": {"pivot": pivot, "unchanged_rows": int(unaffected.sum()), "passed": True},
        "unchanged_other_cell_columns": 12,
        "original_decision": old_sieve["cells"][CELL]["decision"], "repaired_decision": new_sieve[CELL]["decision"],
        "repaired_passed_cells": [c for c in source.CELL_IDS if new_sieve[c]["decision"]["passed"]],
        "comparisons": comparisons, "horse_metadata_sha256": sha(OUT / "horse_metadata.parquet"),
        "prediction_sha256": pred_hashes,
        "elapsed_seconds": time.monotonic()-started,
        "can_adopt": False, "untouched_holdout": False,
        "limits": ["This repairs one malformed feature; non-rejection does not establish zero effect.",
                   "Model and horse metadata provenance otherwise inherits the original R09 study.",
                   "Stored-odds ROI is diagnostic and is not settlement at certified decision-time prices."]}
    assert all(sha(p)==h for p,h in input_hashes.items())
    assert all(sha(p)==h for p,h in source_hashes.items())
    assert all(sha(p)==h for p,h in pred_hashes.items())
    summary["original_inputs_and_sources_unchanged"] = True
    save(OUT / "summary.json", summary)
    save(OUT / "repaired_sieve.json", new_sieve)
    pd.DataFrame(comparisons).to_csv(OUT / "comparison.csv", index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
