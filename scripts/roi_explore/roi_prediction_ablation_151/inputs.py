"""Frozen, database-free inputs for research 151 (no fitting or production inference).

The 130 stored six-member predictions are reconstructed independently of mutable
serving code. Exact full fields are required; no row-wise join may shrink a race.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
from pathlib import Path
import pickle

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "artifacts/111-ability-observation/snapshot.pkl"
WALK = ROOT / "artifacts/130-mixture-preweight-walkforward"
EVIDENCE = ROOT / "specs/130-mixture-preweight-walkforward/evidence/walkforward-races.json"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
MEMBERS = tuple(f"{branch}-{seed}" for branch in ("joint", "anchor") for seed in (42, 43, 44))
YEARS = tuple(range(2020, 2027))
REGIMES = ("preweight", "full")
MAX_DATE = "2026-06-26"
MEV_MODEL = ROOT / "artifacts/market_ev/mev-ens15-v1"
MEV_PARITY = ROOT / "specs/138-attention-conditions/evidence/parity_ens15.json"
KEYS = ["race_id", "horse_id"]


def seed_path(seed: int) -> Path:
    tag = "armC_binary" + ("" if seed == 1 else f"_seed{seed}") + "_drop-sameday+weightlive_from2007_ens15"
    return ROOT / "artifacts/roi_explore/results" / tag / "predictions.parquet"


def input_paths() -> list[Path]:
    """Enumerate inputs without reading labels, predictions, or performance results."""
    return [SNAPSHOT, WALK / "run-freeze.json", EVIDENCE, ROWS,
            *[seed_path(s) for s in range(1, 16)],
            *[seed_path(s).with_name("result.json") for s in range(1, 16)],
            MEV_MODEL / "ensemble.spec.json",
            *[MEV_MODEL / f"seed_{s:02d}/model.spec.json" for s in range(1, 16)],
            MEV_PARITY, ROOT / "scripts/roi_explore/direct_return_model.py",
            ROOT / "scripts/roi_explore/parity_ens15_20261001.py",
            *[WALK / sub / f"{m}-{y}.{ext}" for m in MEMBERS for y in YEARS
              for sub, ext in (("cache", "pkl"), ("receipts", "json"))]]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path):
    return json.loads(path.read_text())


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def correction_terms(frame: pd.DataFrame) -> pd.DataFrame:
    """130 terms from the frozen started-only history, strictly before each day."""
    cols = ["race_id", "horse_id", "race_date", "days_since_last", "sex"]
    x = frame[cols].copy()
    _require(not x.duplicated(KEYS).any(), "Duplicate snapshot identity")
    x["race_date"] = pd.to_datetime(x.race_date)
    history = x.loc[x.race_date >= "2007-01-01", ["horse_id", "race_date"]].drop_duplicates()
    history = history.sort_values(["horse_id", "race_date"], kind="stable")
    prior_day = history.groupby("horse_id", sort=False).race_date.shift(1)
    prior2_day = history.groupby("horse_id", sort=False).race_date.shift(2)
    history["prior_gap_log"] = np.log1p((prior_day - prior2_day).dt.days.to_numpy(float))
    x = x.loc[x.race_date.dt.year.isin(YEARS)].copy()
    x = x.merge(history, on=["horse_id", "race_date"], how="left", validate="many_to_one")
    gap = x.days_since_last.to_numpy(float)
    observed = gap[np.isfinite(gap)]
    _require(not np.isinf(gap).any() and (observed > 0).all() and (observed % 1 == 0).all(),
             "Invalid strict-past days_since_last")
    _require(set(x.sex.dropna()) <= {"牡", "牝", "セ"}, "Unknown original sex")
    theta = 2 * np.pi * ((x.race_date.dt.dayofyear.to_numpy() - 1)
                         / np.where(x.race_date.dt.is_leap_year, 366., 365.))
    female = np.where(x.sex.isna(), np.nan, (x.sex == "牝").to_numpy(float))
    x["gap_log"] = np.log1p(gap)
    x["female_sin"] = female * np.sin(theta)
    x["female_cos"] = female * np.cos(theta)
    return x[[*KEYS, "race_date", "gap_log", "prior_gap_log", "female_sin", "female_cos"]]


def correct_win(base, terms, coefficients, *, joint: bool, assembly_eps: float = 0.):
    """Exact 130 log tilt and win-only assembly, without importing serving code."""
    p = np.asarray(base, dtype=float)
    beta = np.asarray(coefficients, dtype=float)
    _require(np.isfinite(p).all() and (p > 0).all() and np.isclose(p.sum(), 1., atol=1e-8, rtol=0),
             "Invalid cached base probabilities")
    h = np.nan_to_num(np.asarray(terms, dtype=float), nan=0.)
    if joint:
        lp = np.log(p)
        h = np.column_stack((h, lp - lp.mean()))
    else:
        h = h[:, :1]
    _require(beta.shape == (h.shape[1],) and np.isfinite(beta).all(), "Invalid frozen coefficients")
    z = h @ beta
    raw = p * np.exp(z - z.max())
    q = raw / raw.sum()
    # 130's saved evidence is the authority for the original assembly convention.
    q = np.clip(q, assembly_eps, 1. - assembly_eps)
    q = q / float(q.sum())
    _require(np.isfinite(q).all() and (q > 0).all(), "Invalid reconstructed win probabilities")
    return q


def _load_caches(frozen):
    caches = {}
    audits = []
    for year in YEARS:
        n_valid = frozen["population"][str(year)]["n_valid_races"]
        for member in MEMBERS:
            path = WALK / "cache" / f"{member}-{year}.pkl"
            receipt = _read_json(WALK / "receipts" / f"{member}-{year}.json")
            for key, expected in (("member", member), ("year", year), ("smoke", False),
                                  ("source_hash", frozen["source_hash"]), ("n_valid", n_valid),
                                  ("cache_sha256", sha256(path))):
                _require(receipt.get(key) == expected, f"130 receipt mismatch: {member}/{year}/{key}")
            with path.open("rb") as f:
                cache = pickle.load(f)
            for key, expected in (("member", member), ("year", year), ("smoke", False), ("n_valid", n_valid)):
                _require(cache.get(key) == expected, f"130 cache mismatch: {member}/{year}/{key}")
            through = cache["oof_info"]["oof_pred_through"]
            _require(through < f"{year}-01-01", f"Future OOF calibrator: {member}/{year}")
            _require(cache["oof_info"].get("sufficient") is True, "Insufficient OOF cache")
            for reg in REGIMES:
                _require(len(cache["predictions"][reg]) == n_valid, "130 validation size differs")
            caches[(member, year)] = cache["predictions"]
            audits.append({"member": member, "year": year, "n_valid": n_valid,
                           "oof_pred_through": through, "receipt_verified": True})
    return caches, audits


def _validate_mev_lineage():
    """Check saved annual cutoffs plus the existing, hash-bound parity evidence.

    No original fit receipt binds each research parquet to a booster byte-for-byte;
    that limit remains explicit even when all saved metadata and parity agree.
    """
    ensemble = _read_json(MEV_MODEL / "ensemble.spec.json")
    rows_sha = sha256(ROWS)
    _require(ensemble["objective"] == "binary" and ensemble["seeds"] == list(range(1, 16)),
             "MEV ensemble identity differs")
    _require(ensemble["input_rows_sha256"] == rows_sha, "MEV input rows SHA differs")
    parity = _read_json(MEV_PARITY)
    _require(parity["acceptance"]["passed"] is True, "Existing MEV parity did not pass")
    _require(parity["production"]["ensemble_spec_sha256"] == sha256(MEV_MODEL / "ensemble.spec.json"),
             "MEV ensemble spec differs from parity evidence")
    _require(parity["research"]["rows"]["sha256"] == rows_sha, "MEV parity rows SHA differs")
    prediction_receipts = {r["seed"]: r for r in parity["research"]["ens_prediction_inputs"]}
    members = []
    for seed in range(1, 16):
        spec = _read_json(MEV_MODEL / f"seed_{seed:02d}/model.spec.json")
        result = _read_json(seed_path(seed).with_name("result.json"))
        _require(spec["objective"] == "binary" and spec["seed"] == seed, "MEV seed identity differs")
        _require(spec["input_rows_sha256"] == rows_sha and spec["feature_hash"] == ensemble["feature_hash"],
                 "MEV seed data/features lineage differs")
        _require(result["tag"] == seed_path(seed).parent.name
                 and result["params"]["objective"] == "binary" and result["params"]["seed"] == seed
                 and result["rounds"] == spec["rounds"] and result["synthetic_q"] == 0,
                 "MEV research result metadata differs")
        _require(set(result["features"]) == set(spec["features"]) | set(spec["cats"]),
                 "MEV research feature set differs from saved model")
        for year in YEARS:
            cutoff = spec["training_cutoff_by_year"][str(year)]
            _require(cutoff["train_from"] >= 2007 and cutoff["train_through"] == year - 1,
                     f"MEV seed {seed}: future training cutoff for {year}")
            _require(cutoff == ensemble["training_cutoff_by_year"][str(year)], "MEV ensemble cutoff differs")
        receipt = prediction_receipts[seed]
        _require(receipt["sha256"] == sha256(seed_path(seed)), "MEV predictions differ from parity evidence")
        members.append({"seed": seed, "training_cutoff_by_year": {
            str(y): spec["training_cutoff_by_year"][str(y)] for y in YEARS}})
    return {"members": members, "objective": "binary", "input_rows_sha_verified": True,
            "research_prediction_shas_match_existing_parity": True,
            "existing_parity_range": parity["range"], "existing_parity_common": parity["common"],
            "direct_research_parquet_to_booster_fit_receipt": False,
            "limitation": "Saved specifications declare annual past-only training and match research metadata. "
                          "Existing hash-bound parity empirically links these predictions to production in its stated range; "
                          "no original per-fit receipt proves every annual research parquet row came from the saved booster."}


def _load_mev():
    lineage = _validate_mev_lineage()
    base = pd.read_parquet(seed_path(1))
    _require(not base.duplicated(KEYS).any(), "Duplicate MEV identity")
    _require((base.year.to_numpy() == pd.to_datetime(base.race_date).dt.year.to_numpy()).all(), "MEV year/date mismatch")
    probs = []
    for seed in range(1, 16):
        d = base if seed == 1 else pd.read_parquet(seed_path(seed))
        _require(len(d) == len(base), f"MEV seed {seed}: size mismatch")
        for col in [*KEYS, "race_date", "year", "odds"]:
            _require(np.array_equal(d[col].to_numpy(), base[col].to_numpy()), f"MEV seed {seed}: {col} mismatch")
        p = (1 + d.pred.to_numpy(float) / 100) / d.odds.to_numpy(float)
        _require(np.isfinite(p).all(), f"MEV seed {seed}: nonfinite probability")
        probs.append(p)
    # Match 141's clip after the average (do not clip individual members).
    raw = np.column_stack(probs).mean(axis=1)
    base = base[[*KEYS, "race_date", "year", "odds"]].copy()
    base["p_mev"] = np.clip(raw, 1e-6, 1 - 1e-6)
    return base, {"seeds": 15, "rows": len(base), "postmean_clipped_rows": int((base.p_mev != raw).sum()),
                  "postmean_clip": [1e-6, 1-1e-6], "lineage": lineage}


def load_inputs() -> tuple[pd.DataFrame, dict]:
    """Return whole-race matched predictions and a structural, nonselection audit."""
    frozen = _read_json(WALK / "run-freeze.json")
    _require(sha256(SNAPSHOT) == frozen["snapshot_sha256"], "111 snapshot SHA differs from 130 freeze")
    _require(set(frozen["config"]["members"]) == set(MEMBERS), "130 member set differs")
    with SNAPSHOT.open("rb") as f:
        matrix, races = pickle.load(f)
    snapshot = matrix.frame[[*KEYS, "race_date", "horse_number", "days_since_last", "sex"]].copy()
    terms = correction_terms(snapshot)
    contexts = {r.context.race_id: r.context for r in races if r.context.race_date.year in YEARS}
    snapshot_result_info = {
        r.context.race_id: {
            "complete": r.n_result_rows is None or r.n_result_rows >= len(r.context.started_horses),
            "result_count_known": r.n_result_rows is not None,
            "winners": frozenset(label.horse_id for label in r.labels if label.win == 1
                                 and label.horse_id in {h.horse_id for h in r.context.started_horses}),
        } for r in races if r.context.race_date.year in YEARS
    }
    del matrix, races
    gc.collect()
    by_race = {rid: g.set_index("horse_id") for rid, g in terms.groupby("race_id", sort=False)}
    snapshot_ids = snapshot.groupby("race_id", sort=False).horse_id.agg(frozenset).to_dict()
    del terms
    caches, cache_audit = _load_caches(frozen)
    mev, mev_audit = _load_mev()
    rows = pd.read_parquet(ROWS, columns=[*KEYS, "race_date", "year", "odds", "horse_number", "won",
                                       "field_size", "race_ok", "dead_heat"])
    _require(not rows.duplicated(KEYS).any(), "Duplicate rows_2007 identity")
    rows["race_date"] = pd.to_datetime(rows.race_date).dt.strftime("%Y-%m-%d")
    window = rows.loc[rows.year.isin(YEARS) & (rows.race_date <= MAX_DATE)].copy()
    _require(not (window.groupby("race_id").race_ok.nunique() > 1).any(), "race_ok differs within race")
    _require(not (window.groupby("race_id").dead_heat.nunique() > 1).any(), "dead_heat differs within race")
    eligible = window.loc[window.race_ok & ~window.dead_heat].copy()
    mev_groups = {rid: g.set_index("horse_id") for rid, g in mev.groupby("race_id", sort=False)}
    evidence = {r["race_id"]: r for r in _read_json(EVIDENCE)["rows"]}
    excluded = {}
    excluded_ids = {}
    output = []
    parity = {reg: [] for reg in REGIMES}
    assembly_eps = 0.
    for rid, group in eligible.groupby("race_id", sort=False):
        year = int(group.year.iloc[0])
        ids = group.horse_id.tolist()
        identity = frozenset(ids)
        reason = None
        if rid not in contexts or snapshot_ids.get(rid) != identity:
            reason = "snapshot_missing_or_field_mismatch"
        elif frozenset(h.horse_id for h in contexts[rid].started_horses) != identity:
            reason = "snapshot_context_field_mismatch"
        elif not snapshot_result_info[rid]["complete"]:
            reason = "snapshot_incomplete_results"
        elif len(snapshot_result_info[rid]["winners"]) != 1:
            reason = "snapshot_no_unique_winner"
        elif rid not in mev_groups or frozenset(mev_groups[rid].index) != identity:
            reason = "mev_missing_or_field_mismatch"
        elif any(rid not in caches[(m, year)][reg] or
                 frozenset(caches[(m, year)][reg].get(rid, {})) != identity
                 for m in MEMBERS for reg in REGIMES):
            reason = "130_member_or_regime_missing_or_field_mismatch"
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            excluded_ids.setdefault(reason, []).append(rid)
            continue
        _require(rid in evidence, f"130 eligible evidence missing: {rid}")
        _require(group.won.sum() == 1, f"No unique winner: {rid}")
        _require(frozenset(group.loc[group.won, "horse_id"]) == snapshot_result_info[rid]["winners"],
                 f"Snapshot/rows winner differs: {rid}")
        _require(group.field_size.eq(len(ids)).all(), f"Stored field size differs: {rid}")
        _require(group.race_date.nunique() == 1 and group.year.nunique() == 1, f"Race date/year conflict: {rid}")
        _require(str(contexts[rid].race_date) == group.race_date.iloc[0], f"Snapshot date differs: {rid}")
        expected_numbers = {h.horse_id: h.horse_number for h in contexts[rid].started_horses}
        _require(group.horse_number.tolist() == [expected_numbers[h] for h in ids], f"Horse numbers differ: {rid}")
        mg = mev_groups[rid].loc[ids]
        _require(np.array_equal(mg.odds.to_numpy(float), group.odds.to_numpy(float)), f"MEV/rows odds differ: {rid}")
        _require(np.array_equal(mg.race_date.astype(str).to_numpy(), group.race_date.to_numpy()), f"MEV/rows dates differ: {rid}")
        _require(np.array_equal(mg.year.to_numpy(), group.year.to_numpy()), f"MEV/rows years differ: {rid}")
        _require(np.isfinite(group.odds).all() and (group.odds > 0).all(), f"Invalid odds: {rid}")
        corr = by_race[rid].loc[ids]
        h = corr[["gap_log", "prior_gap_log", "female_sin", "female_cos"]].to_numpy(float)
        g = group[[*KEYS, "horse_number", "race_date", "year", "odds", "won", "field_size"]].copy()
        g["p_mev"] = mg.p_mev.to_numpy()
        for reg in REGIMES:
            wins = []
            for member in MEMBERS:
                heads = caches[(member, year)][reg][rid]
                base = [heads[horse][0] for horse in ids]
                wins.append(correct_win(base, h, frozen["coefficients"][member][str(year)],
                                        joint=member.startswith("joint-"), assembly_eps=assembly_eps))
            mix = np.asarray(wins).mean(axis=0, dtype=np.float64)
            g[f"p_pred_{reg}"] = mix
            winner = group.loc[group.won, "horse_id"].iloc[0]
            _require(winner == evidence[rid]["winner"], f"130 winner differs: {rid}")
            nll = -np.log(mix[ids.index(winner)])
            parity[reg].append(abs(float(nll) - evidence[rid][f"{reg}_candidate_nll"]))
        inv = 1. / g.odds.to_numpy(float)
        g["q"] = inv / inv.sum()
        output.append(g)
    _require(bool(output), "No complete common races")
    for reg in REGIMES:
        _require(max(parity[reg]) <= 1e-10, f"130 {reg} evidence win NLL parity failed: {max(parity[reg])}")
    frame = pd.concat(output, ignore_index=True).sort_values(
        ["race_id", "race_date", "horse_number", "horse_id"], kind="stable").reset_index(drop=True)
    audit = {"window": {"from": "2020-01-01", "through": MAX_DATE},
             "window_races": int(window.race_id.nunique()), "eligible_rows_races": int(eligible.race_id.nunique()),
             "excluded_races": excluded, "excluded_race_ids": excluded_ids,
             "window_race_ok_false_races": int(window.loc[~window.race_ok, "race_id"].nunique()),
             "window_dead_heat_races": int(window.loc[window.dead_heat, "race_id"].nunique()), "common_races": int(frame.race_id.nunique()), "common_rows": len(frame),
             "exact_started_fields": True, "snapshot_sha_verified": True,
             "snapshot_legacy_unknown_result_count_races": sum(not info["result_count_known"]
                                                                for info in snapshot_result_info.values()), "cache_receipts": cache_audit,
             "mev": mev_audit, "assembly_eps": assembly_eps,
             "130_integrity_parity_only": {reg: {"n_races": len(parity[reg]), "max_abs_nll_difference": max(parity[reg])}
                                           for reg in REGIMES},
             "no_fit": True, "database_access": False,
             "note": "130 NLL parity is input integrity only and does not select an arm or a parameter."}
    return frame, audit
