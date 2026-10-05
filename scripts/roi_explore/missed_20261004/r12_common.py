"""R12_cross_pool shared loaders (read-only SELECT only; no product code edits).

Arms (prereg.json): (i) ens15 -> quinella max-EV point, (ii) quinella-marginal veto inside S3/S1,
(iii) trio-implied P3 vs win-implied P3 -> place.  This module only LOADS data; it never looks at
outcomes unless the caller asks for them explicitly (load_outcomes / load_dividends).
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "eval/src"))
RES = ROOT / "artifacts/roi_explore/results"
OUT = ROOT / "artifacts/roi_explore/missed_20261004/R12_cross_pool"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
R05 = ROOT / "artifacts/roi_explore/missed_20261004/R05_settlement"
DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
ENS_TAGS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
MEV_TAG = "armC_binary_drop-sameday+weightlive_serving_v2_2007"
WIN_CUTOFF = "2026-06-26"
MARKET_L2 = 0.75   # probability/market_odds.py MARKET_STAGE_LAMBDA2 (fit on real price grids, no outcomes)
MARKET_L3 = 0.70   # MARKET_STAGE_LAMBDA3


def sha(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def engine():
    return create_engine(DB)


def load_rows(year_from: int = 2024, with_outcomes: bool = False) -> tuple[pd.DataFrame, dict]:
    cols = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "q", "popularity",
            "odds_rank", "field_size", "race_ok", "days_since_last"]
    if with_outcomes:
        cols += ["won", "finish_order", "result_status", "dead_heat", "n_winners"]
    d = pd.read_parquet(ROWS, columns=cols)
    d = d[(d.year >= year_from) & d.race_ok].copy()
    d["race_date"] = pd.to_datetime(d.race_date).dt.strftime("%Y-%m-%d")
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
    m = d[evs].to_numpy(float)
    d["ens_ev"] = np.where(np.isnan(m).any(axis=1), np.nan, np.mean(1.0 + m / 100.0, axis=1))
    d["single_ev"] = 1.0 + d["pred_single"].to_numpy(float) / 100.0
    d["p_ens_raw"] = d["ens_ev"] / d["odds"].to_numpy(float)
    d = d.drop(columns=evs + ["pred_single"])
    # a race is model-complete only if every started horse has an ens15 prediction
    d["race_pred_ok"] = d.groupby("race_id")["ens_ev"].transform(lambda s: bool(s.notna().all()))
    d = d.sort_values(["race_date", "race_id", "horse_number"], kind="mergesort").reset_index(drop=True)
    return d, prov


def load_grids(bet_type: str) -> pd.DataFrame:
    """Grids with timing class: 'pre' if observed_at < post_time, else 'final' (no post_time -> final;
    official_at for those sits ~8-10 min after the typical post time of the race number)."""
    q = text("""select q.race_id, q.quotes, q.official_at, q.observed_at, r.post_time, r.race_date
                from exotic_quotes q join races r using(race_id) where q.bet_type = :b""")
    with engine().connect() as c:
        g = pd.read_sql(q, c, params={"b": bet_type})
    g["timing"] = np.where(g.post_time.notna() & (g.observed_at < g.post_time), "pre", "final")
    g["race_date"] = pd.to_datetime(g.race_date).dt.strftime("%Y-%m-%d")
    return g


def parse_grid(quotes: dict, started: set[int], k: int) -> dict[tuple, float]:
    out = {}
    for key, v in quotes.items():
        combo = tuple(sorted(int(x) for x in key.split("-")))
        if len(combo) != k or not started.issuperset(combo):
            continue
        o = v[0] if isinstance(v, list) else v
        if o is None:
            continue
        o = float(o)
        if o > 0:
            out[combo] = o
    return out


def load_dividends(bet_types=("quinella", "place")) -> pd.DataFrame:
    q = text("""select eo.race_id, eo.bet_type, eo.selection, eo.odds from exotic_odds eo
                where eo.bet_type = any(:b)""")
    with engine().connect() as c:
        d = pd.read_sql(q, c, params={"b": list(bet_types)})
    d["odds"] = d.odds.astype(float)
    return d


def harville_top2(p: np.ndarray, lam: float) -> np.ndarray:
    """Quinella matrix P{i,j} (symmetric, zero diagonal) from win probs p (sum 1) with a stage-2
    power discount: P(i->j) = p_i * p_j^lam / sum_{k!=i} p_k^lam."""
    pl = p ** lam
    s = pl.sum()
    denom = s - pl                      # sum over k != i
    ex = p[:, None] * pl[None, :] / denom[:, None]
    np.fill_diagonal(ex, 0.0)
    return ex + ex.T


def harville_top_marginals(p: np.ndarray, l2: float, l3: float) -> tuple[np.ndarray, np.ndarray]:
    """P(i in top2), P(i in top3) under Harville with stage discounts (l2 on 2nd, l3 on 3rd)."""
    n = len(p)
    p2 = p ** l2
    p3 = p ** l3
    s2 = p2.sum()
    s3 = p3.sum()
    # P(a 1st, b 2nd)
    ex = p[:, None] * p2[None, :] / (s2 - p2)[:, None]
    np.fill_diagonal(ex, 0.0)
    top2 = p + ex.sum(axis=0)           # first or second
    # P(i 3rd) = sum_{a,b distinct, != i} ex[a,b] * p3_i / (s3 - p3_a - p3_b)
    den = s3 - p3[:, None] - p3[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        w = np.where(den > 0, ex / den, 0.0)
    np.fill_diagonal(w, 0.0)
    third = np.zeros(n)
    tot_w = w.sum()
    for i in range(n):
        wi = tot_w - w[i, :].sum() - w[:, i].sum()   # pairs (a,b) with a!=i, b!=i
        third[i] = p3[i] * wi
    return top2, top2 + third
