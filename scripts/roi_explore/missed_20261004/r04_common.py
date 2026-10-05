"""R04_delta_r2 shared loaders (read-only). Population and component definitions follow prereg.json."""
from __future__ import annotations

import hashlib
import json
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[3]
RES = ROOT / "artifacts/roi_explore/results"
OUT = ROOT / "artifacts/roi_explore/missed_20261004/R04_delta_r2"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
ENS_TAGS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
MEV_TAG = "armC_binary_drop-sameday+weightlive_serving_v2_2007"
BUNDLE108 = ROOT / "artifacts/oof/8bdde26857f62c5571ef45b02954836dacae7e4a2ea9174c12eb0c9209fb691f/bundle.json"
W130 = ROOT / "artifacts/130-mixture-preweight-walkforward"
EVID130 = ROOT / "specs/130-mixture-preweight-walkforward/evidence/walkforward-races.json"
CUTOFF = "2026-06-26"
MIX_MEMBERS = [("joint", "pruning", 42), ("joint", "pruning", 43), ("joint", "pruning", 44),
               ("anchor", "anchor", 42), ("anchor", "anchor", 43), ("anchor", "anchor", 44)]


def sha(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load_population() -> tuple[pd.DataFrame, dict]:
    """Rows of the pre-registered population with q, p_mev, p_ens; sorted by (race_date, race_id, horse_number)."""
    cols = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "q", "won", "race_ok",
            "dead_heat", "days_since_last", "sex"]
    d = pd.read_parquet(ROWS, columns=cols)
    d = d[d.race_ok & ~d.dead_heat & (d.year >= 2010) & (d.race_date <= CUTOFF)].copy()
    prov = {"rows_sha256": sha(ROWS)}
    evs = []
    for s, tag in ENS_TAGS.items():
        f = RES / tag / "predictions.parquet"
        v = pd.read_parquet(f, columns=["race_id", "horse_id", "pred"]).rename(columns={"pred": f"pred{s}"})
        prov[f"ens_seed{s}_sha256"] = sha(f)
        d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
        evs.append(f"pred{s}")
    f = RES / MEV_TAG / "predictions.parquet"
    v = pd.read_parquet(f, columns=["race_id", "horse_id", "pred"]).rename(columns={"pred": "pred_single"})
    prov["mev_sha256"] = sha(f)
    d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    if d[evs + ["pred_single"]].isna().any().any():
        raise SystemExit("FAIL: missing predictions")
    odds = d.odds.to_numpy(float)
    d["p_ens"] = np.mean([1.0 + d[c].to_numpy(float) / 100.0 for c in evs], axis=0) / odds
    d["ens_ev"] = np.mean([1.0 + d[c].to_numpy(float) / 100.0 for c in evs], axis=0)
    d["p_mev"] = (1.0 + d["pred_single"].to_numpy(float) / 100.0) / odds
    d = d.drop(columns=evs + ["pred_single"])
    d = d.sort_values(["race_date", "race_id", "horse_number"], kind="mergesort").reset_index(drop=True)
    return d, prov


def attach_prod108(d: pd.DataFrame) -> pd.DataFrame:
    b = json.loads(BUNDLE108.read_text())
    P = b["predictions"]
    d["p_prod"] = [P[r][h]["win"] for r, h in zip(d.race_id, d.horse_id)]
    return d


def attach_anchor42_preweight(d: pd.DataFrame) -> pd.DataFrame:
    import pickle
    out = np.full(len(d), np.nan)
    for y in range(2020, 2027):
        c = pickle.load(open(W130 / "cache" / f"anchor-42-{y}.pkl", "rb"))["predictions"]["preweight"]
        idx = np.flatnonzero(d.year.to_numpy() == y)
        out[idx] = [c[r][h][0] for r, h in zip(d.race_id.to_numpy()[idx], d.horse_id.to_numpy()[idx])]
    d["p_anchor42"] = out
    return d
