"""141 FR-006: one-time held-out check of the market mixture on closing odds (2026-08-10..10-18).

Read-only DB. Production as-of features on the stored FINAL odds (only races whose winner odds x100
match the official 単勝 payout), p̄ = mean of the 15 mev-ens15-v1 2026 boosters, p_mix = the shared
definition with the frozen 2026 coefficients. Protocol: protocol.json (sha256 pinned, checked here).
Output: results/heldout_2026-08-10_2026-10-18.json (refuses to overwrite).

    cd training && uv run python ../scripts/roi_explore/market_mix_20261019/heldout.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from horseracing_eval import market_mix as MM
from horseracing_eval.bootstrap import block_bootstrap_counts
from horseracing_training import market_ev

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ENS = ROOT / "artifacts/market_ev/mev-ens15-v1"
DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
FROM, TO = "2026-08-10", "2026-10-18"
B, SEED = 4000, 20261019
BANDS = ((-np.inf, 0.8), (0.8, 1.0), (1.0, 1.2), (1.2, 1.5), (1.5, np.inf))
OUT = HERE / "results" / f"heldout_{FROM}_{TO}.json"


def verify_protocol() -> str:
    sha = hashlib.sha256((HERE / "protocol.json").read_bytes()).hexdigest()
    want = (HERE / "protocol.sha256").read_text().split()[0]
    if sha != want:
        raise SystemExit(f"protocol.json sha {sha} != frozen {want}")
    return sha


def load(conn):
    raw = market_ev.load_rows(conn, through=TO)
    feats = market_ev.build_features(raw)
    feats["race_date"] = pd.to_datetime(feats["race_date"])
    f = feats[(feats.race_date >= FROM) & (feats.race_date <= TO) & feats.race_ok.astype(bool)].copy()
    res = pd.read_sql(text("""
        select rr.race_id, rr.horse_id, rr.finish_order, rr.result_status
        from race_results rr join races r using(race_id)
        where r.race_date between :f and :t and r.track_type <> '障'"""), conn, params={"f": FROM, "t": TO})
    pay = pd.read_sql(text("""
        select p.race_id, p.horse_number, p.payout_yen from official_win_payouts p join races r using(race_id)
        where r.race_date between :f and :t"""), conn, params={"f": FROM, "t": TO})
    return f, res, pay


def population(f: pd.DataFrame, res: pd.DataFrame, pay: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    excl = {"not_in_results": 0, "no_single_winner": 0, "no_payout": 0, "odds_not_final": 0, "jump": 0}
    win = res[(res.result_status == "finished") & (res.finish_order == 1)]
    nwin = win.groupby("race_id").size()
    paym = {(r, int(h)): float(y) for r, h, y in pay[["race_id", "horse_number", "payout_yen"]].itertuples(index=False)}
    keep = []
    res_races = set(res.race_id)
    for rid, g in f.groupby("race_id", sort=True):
        if rid not in res_races:
            excl["not_in_results"] += 1
            continue
        if nwin.get(rid, 0) != 1:
            excl["no_single_winner"] += 1
            continue
        w = win[win.race_id == rid].horse_id.iloc[0]
        gw = g[g.horse_id == w]
        if len(gw) != 1:
            excl["no_single_winner"] += 1
            continue
        y = paym.get((rid, int(gw.horse_number.iloc[0])))
        if y is None:
            excl["no_payout"] += 1
            continue
        if abs(float(gw.odds.iloc[0]) * 100 - y) > 10:
            excl["odds_not_final"] += 1
            continue
        d = g.copy()
        d["won"] = (d.horse_id == w).astype(float)
        d["payout"] = np.where(d.horse_id == w, y / 100.0, 0.0)
        keep.append(d)
    pop = pd.concat(keep, ignore_index=True).sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    return pop, excl


def wnll(p: np.ndarray, d: pd.DataFrame) -> pd.Series:
    pn = p / pd.Series(p).groupby(d.race_id.to_numpy()).transform("sum").to_numpy()
    w = d.won.to_numpy(bool)
    return pd.Series(-np.log(np.clip(pn[w], 1e-12, 1)), index=d.race_id.to_numpy()[w])


def setstats(d, p, mask, counts, day_idx, n_days):
    won, pay = d.won.to_numpy(), d.payout.to_numpy()
    out = {"n": int(mask.sum()), "won": int(won[mask].sum()), "sum_p": float(p[mask].sum())}
    out["won_over_p"] = float(won[mask].sum() / p[mask].sum()) if mask.any() else None
    out["roi_official"] = float(pay[mask].sum() / mask.sum()) if mask.any() else None
    if mask.any():
        num_w = np.bincount(day_idx, weights=won * mask, minlength=n_days)
        den_p = np.bincount(day_idx, weights=p * mask, minlength=n_days)
        num_r = np.bincount(day_idx, weights=pay * mask, minlength=n_days)
        den_n = np.bincount(day_idx, weights=mask.astype(float), minlength=n_days)
        with np.errstate(invalid="ignore", divide="ignore"):
            r1 = (counts @ num_w) / (counts @ den_p)
            r2 = (counts @ num_r) / (counts @ den_n)
        out["won_over_p_ci"] = [float(np.nanpercentile(r1, 2.5)), float(np.nanpercentile(r1, 97.5))]
        out["roi_ci"] = [float(np.nanpercentile(r2, 2.5)), float(np.nanpercentile(r2, 97.5))]
    return out


def main() -> int:
    t0 = time.time()
    if OUT.exists():
        raise SystemExit(f"{OUT} exists; the held-out check runs once")
    psha = verify_protocol()
    coef = MM.coefficients_for(2026)
    eng = create_engine(DB)
    with eng.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        f, res, pay = load(conn)
    pop, excl = population(f, res, pay)
    model = market_ev.EnsembleMarketEvModel.load(ENS, "mev-ens15-v1")
    man = model.booster_manifest_for_year(2026)
    boosters, man_sha = model.load_members(man)
    X = model.design_matrix(pop)
    p = np.mean(np.vstack([b.predict(X, num_threads=1) for b in boosters]), axis=0)
    pm = np.empty_like(p)
    for _rid, idx in pop.groupby("race_id", sort=False).indices.items():
        pm[idx] = MM.mix_probabilities(p[idx], pop.odds.to_numpy(float)[idx], coef)
    odds = pop.odds.to_numpy(float)
    days = sorted(pop.race_date.dt.date.astype(str).unique())
    day_of_race = pop.drop_duplicates("race_id").set_index("race_id").race_date.dt.date.astype(str)
    counts = block_bootstrap_counts(len(days), B, SEED)
    day_idx = pop.race_date.dt.date.astype(str).map({d: i for i, d in enumerate(days)}).to_numpy()
    w_raw, w_mix = wnll(p, pop), wnll(pm, pop)
    diff = (w_mix - w_raw)
    dd = np.bincount(day_of_race.loc[diff.index].map({d: i for i, d in enumerate(days)}).to_numpy(),
                     weights=diff.to_numpy(), minlength=len(days))
    dn = np.bincount(day_of_race.loc[diff.index].map({d: i for i, d in enumerate(days)}).to_numpy(), minlength=len(days))
    reps = (counts @ dd) / (counts @ dn)
    point = float(diff.mean())
    lo, hi = float(np.percentile(reps, 2.5)), float(np.percentile(reps, 97.5))
    enabled = not (lo > 0)
    ev, evm = p * odds, pm * odds
    bands = {}
    for name, e, pp in (("raw", ev, p), ("mix", evm, pm)):
        bands[name] = {}
        for a, b in BANDS:
            m = (e > a) & (e <= b) if np.isfinite(a) else (e <= b)
            bands[name][f"({a},{b}]"] = setstats(pop, pp, m, counts, day_idx, len(days))
    sets = {"raw_ev_gt_1.0": setstats(pop, p, ev > 1.0, counts, day_idx, len(days)),
            "raw_ev_gt_1.2_S3": setstats(pop, p, ev > 1.2, counts, day_idx, len(days)),
            "mix_ev_gt_1.0": setstats(pop, pm, evm > 1.0, counts, day_idx, len(days)),
            "mix_ev_gt_1.2": setstats(pop, pm, evm > 1.2, counts, day_idx, len(days))}
    out = {
        "protocol_sha256": psha, "window": {"from": FROM, "to": TO}, "computed_at": dt.datetime.now(dt.UTC).isoformat(),
        "n_races": int(pop.race_id.nunique()), "n_rows": int(len(pop)), "n_days": len(days), "days": days,
        "exclusions_races": excl, "ensemble_manifest": man.name, "ensemble_manifest_sha256": man_sha,
        "coefficients": {"year": coef.year, "a": coef.a, "b": coef.b, "source_sha256": coef.source_sha256},
        "winner_nll": {"raw": float(w_raw.mean()), "mix": float(w_mix.mean()),
                       "paired_mix_minus_raw": {"point": point, "ci95": [lo, hi], "B": B, "seed": SEED}},
        "rule": {"display_enabled": enabled, "reason": "95% lower bound <= 0 (not significantly worse)" if enabled else "significantly worse"},
        "bands": bands, "sets": sets,
        "disclaimers": ["ROI の改善を示すものではない", "確定オッズでの確認。発走前オッズでの表示値は別に記録する"],
        "elapsed_s": round(time.time() - t0, 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"races {out['n_races']} rows {out['n_rows']} days {len(days)} excl {excl}")
    print(f"winner NLL raw {w_raw.mean():.5f} mix {w_mix.mean():.5f} diff {point:+.5f} [{lo:+.5f},{hi:+.5f}] -> display_enabled={enabled}")
    for k, v in sets.items():
        print(f"{k}: n={v['n']} won={v['won']} won/p={v['won_over_p']} ci={v.get('won_over_p_ci')} roi={v['roi_official']} ci={v.get('roi_ci')}")
    print("ROI の改善を示すものではない")
    return 0


if __name__ == "__main__":
    sys.exit(main())
