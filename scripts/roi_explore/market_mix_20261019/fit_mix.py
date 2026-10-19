"""141: fit the yearly market-mixture coefficients and reproduce the 10/10 walk-forward numbers.

Population (identical to docs/roi-model-improvement-20261010/evidence/oof_error/measure2.py):
15-seed OOF of mev-ens15-v1 (p_s = (1 + pred/100) / odds, mean over seeds, clip [1e-6, 1-1e-6]),
race_ok ∧ ¬dead_heat ∧ race_date ≤ 2026-06-26. For races of year Y (2013..2026) the coefficients
(a, b) of p_mix = softmax_race(a·log q + b·log p̄) are fitted on years 2010..Y−1 (winner
log-likelihood, L-BFGS-B, bounds [0, 5]², start (1, 0)). Evaluation uses the SHARED definition
``horseracing_eval.market_mix.mix_log_probabilities`` (checked against the vectorized fit form).
Outputs (no DB access): coefficients.json, walkforward.json.

    cd training && uv run python ../scripts/roi_explore/market_mix_20261019/fit_mix.py
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.optimize import minimize  # noqa: E402

from horseracing_eval import market_mix as MM  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).resolve().parent
RES = ROOT / "artifacts/roi_explore/results"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
MAX_DATE = "2026-06-26"
FIT_FROM = 2010
YEARS = tuple(range(2013, 2027))
SEEDS = tuple(range(1, 16))
EV_BANDS = ((-np.inf, 0.8), (0.8, 1.0), (1.0, 1.2), (1.2, 1.5), (1.5, np.inf))
DISCLAIMER = "ROI の改善を示すものではない(確定オッズでの過去検証・記録のみ)"


def seed_path(s: int) -> pathlib.Path:
    tag = "armC_binary" + ("" if s == 1 else f"_seed{s}") + "_drop-sameday+weightlive_from2007_ens15"
    return RES / tag / "predictions.parquet"


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load() -> tuple[pd.DataFrame, dict]:
    base = pd.read_parquet(seed_path(1))
    P = np.empty((len(base), len(SEEDS)))
    shas = {}
    for i, s in enumerate(SEEDS):
        d = pd.read_parquet(seed_path(s))
        assert (d["race_id"].to_numpy() == base["race_id"].to_numpy()).all()
        assert (d["horse_id"].to_numpy() == base["horse_id"].to_numpy()).all()
        P[:, i] = (1 + d["pred"].to_numpy(float) / 100) / d["odds"].to_numpy(float)
        shas[f"seed_{s:02d}"] = sha256(seed_path(s))
    rows = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "won", "race_ok", "dead_heat"])
    df = base.merge(rows, on=["race_id", "horse_id"], how="left")
    keep = (df.race_ok & ~df.dead_heat & (df.race_date <= MAX_DATE)).to_numpy()
    df = df[keep].copy()
    P = P[keep]
    df = df.reset_index(drop=True)
    df["pbar"] = np.clip(P.mean(1), 1e-6, 1 - 1e-6)
    df["won_f"] = df["won"].astype(float)
    df = df.sort_values(["race_id", "horse_id"], kind="stable").reset_index(drop=True)
    shas["rows_2007"] = sha256(ROWS)
    return df, shas


def grouped_log_softmax(s: np.ndarray, codes: np.ndarray) -> np.ndarray:
    mx = pd.Series(s).groupby(codes).transform("max").to_numpy()
    e = np.exp(s - mx)
    z = pd.Series(e).groupby(codes).transform("sum").to_numpy()
    return s - mx - np.log(z)


def fit_year(df: pd.DataFrame, lq: np.ndarray, lp: np.ndarray, codes: np.ndarray, y: int):
    tr = ((df.year >= FIT_FROM) & (df.year < y)).to_numpy()
    c, q, p, won = codes[tr], lq[tr], lp[tr], df.won.to_numpy(bool)[tr]

    def nll(theta):
        lps = grouped_log_softmax(theta[0] * q + theta[1] * p, c)
        return -lps[won].mean()

    r = minimize(nll, x0=[1.0, 0.0], method="L-BFGS-B", bounds=[(0, 5), (0, 5)],
                 options={"ftol": 1e-12, "gtol": 1e-8, "maxiter": 1000})
    return float(r.x[0]), float(r.x[1]), int(tr.sum()), int(df.loc[tr, "race_id"].nunique()), bool(r.success)


def ratio(num, den):
    return float(num / den) if den > 0 else None


def summary(d: pd.DataFrame, pcol: str) -> dict:
    p = d[pcol].to_numpy(float)
    ev = p * d.odds.to_numpy(float)
    won = d.won_f.to_numpy(float)
    pn = p / d.groupby("race_id")[pcol].transform("sum").to_numpy(float)
    wn = -np.log(np.clip(pn[won.astype(bool)], 1e-9, 1))
    out = {"n_rows": int(len(d)), "n_races": int(d.race_id.nunique()),
           "winner_nll_norm": float(wn.mean()), "bands": {}, "thresholds": {}}
    for lo, hi in EV_BANDS:
        m = (ev > lo) & (ev <= hi) if np.isfinite(lo) else ev <= hi
        out["bands"][f"({lo},{hi}]"] = {
            "n": int(m.sum()), "won": int(won[m].sum()), "sum_p": float(p[m].sum()),
            "won_over_p": ratio(won[m].sum(), p[m].sum()),
            "roi": ratio((won[m] * d.odds.to_numpy(float)[m]).sum(), m.sum()),
        }
    raw_s3 = set(zip(d.race_id[d.pbar * d.odds > 1.2], d.horse_id[d.pbar * d.odds > 1.2], strict=True))
    for thr in (1.0, 1.2, 1.5):
        m = ev > thr
        sel = set(zip(d.race_id[m], d.horse_id[m], strict=True))
        out["thresholds"][str(thr)] = {
            "n": int(m.sum()), "won": int(won[m].sum()),
            "won_over_p": ratio(won[m].sum(), p[m].sum()),
            "roi": ratio((won[m] * d.odds.to_numpy(float)[m]).sum(), m.sum()),
            "mean_ev": float(ev[m].mean()) if m.any() else None,
            "overlap_raw_S3": ratio(len(raw_s3 & sel), len(raw_s3)),
        }
    s3 = (d.pbar * d.odds > 1.2).to_numpy()
    out["raw_S3_set"] = {"won_over_p": ratio(won[s3].sum(), p[s3].sum()), "mean_ev": float(ev[s3].mean())}
    return out


def main() -> int:
    t0 = time.time()
    df, shas = load()
    codes, _ = pd.factorize(df.race_id.to_numpy())
    odds = df.odds.to_numpy(float)
    inv = 1 / odds
    lq = np.log(inv) - np.log(pd.Series(inv).groupby(codes).transform("sum").to_numpy())
    lp = np.log(df.pbar.to_numpy(float))
    print(f"loaded {len(df):,} rows / {df.race_id.nunique():,} races ({time.time() - t0:.0f}s)", flush=True)
    coefs, p_mix = {}, np.full(len(df), np.nan)
    for y in YEARS:
        a, b, n_rows, n_races, ok = fit_year(df, lq, lp, codes, y)
        coefs[y] = {"year": y, "a": a, "b": b, "fit_from_year": FIT_FROM, "fit_through_year": y - 1,
                    "n_rows": n_rows, "n_races": n_races, "converged": ok}
        te = (df.year == y).to_numpy()
        p_mix[te] = np.exp(grouped_log_softmax(a * lq[te] + b * lp[te], codes[te]))
        print(f"{y}: a={a:.6f} b={b:.6f} (fit {FIT_FROM}..{y - 1}, {n_races:,} races)", flush=True)
    df["p_mix"] = p_mix
    # parity: the shared per-race definition gives the same vector as the vectorized fit form
    worst = 0.0
    for y in YEARS:
        cf = MM.MixCoefficients(year=y, a=coefs[y]["a"], b=coefs[y]["b"], fit_from_year=FIT_FROM,
                                fit_through_year=y - 1, n_races=0, n_rows=0, source_sha256="")
        part = df[df.year == y]
        for _rid, g in part.groupby("race_id", sort=False):
            pm = MM.mix_probabilities(g.pbar.to_numpy(), g.odds.to_numpy(), cf)
            worst = max(worst, float(np.max(np.abs(pm - g.p_mix.to_numpy()))))
    print(f"shared-definition parity: max |diff| = {worst:.3e}", flush=True)
    assert worst < 1e-12, worst
    d13 = df[df.year >= 2013]
    wf = {
        "population": f"mev-ens15-v1 15-seed OOF, race_ok & !dead_heat & race_date <= {MAX_DATE}, years 2013..2026",
        "fit": f"per year Y: fit on {FIT_FROM}..Y-1, L-BFGS-B bounds [0,5]^2 start (1,0)",
        "shared_definition_parity_max_abs_diff": worst,
        "raw": summary(d13, "pbar"),
        "mix": summary(d13, "p_mix"),
        "by_year": {},
        "settlement": "stored closing odds (pre-2025-10 JRA-VAN final odds); not official payouts",
        "disclaimer": DISCLAIMER,
    }
    for y, g in d13.groupby("year"):
        wf["by_year"][str(int(y))] = {"raw": summary(g, "pbar")["thresholds"], "mix": summary(g, "p_mix")["thresholds"]}
    cj = {"version": MM.MIX_VERSION, "source_model_version": MM.MIX_SOURCE_MODEL_VERSION,
          "inputs_sha256": shas, "coefficients": {str(k): v for k, v in coefs.items()},
          "disclaimer": DISCLAIMER}
    cj["source_sha256"] = hashlib.sha256(json.dumps(shas, sort_keys=True).encode()).hexdigest()
    (HERE / "coefficients.json").write_text(json.dumps(cj, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "walkforward.json").write_text(json.dumps(wf, ensure_ascii=False, indent=1), encoding="utf-8")
    r, m = wf["raw"], wf["mix"]
    print(f"winner NLL raw {r['winner_nll_norm']:.6f} mix {m['winner_nll_norm']:.6f}")
    for k in ("1.0", "1.2"):
        print(f"EV>{k}: raw n={r['thresholds'][k]['n']} w/p={r['thresholds'][k]['won_over_p']:.3f} roi={r['thresholds'][k]['roi']:.3f} | "
              f"mix n={m['thresholds'][k]['n']} w/p={m['thresholds'][k]['won_over_p']:.3f} roi={m['thresholds'][k]['roi']:.3f} overlap={m['thresholds'][k]['overlap_raw_S3']:.3f}")
    print("bands mix:", {k: (v["n"], round(v["won_over_p"], 3) if v["won_over_p"] else None, round(v["roi"], 3) if v["roi"] else None) for k, v in m["bands"].items()})
    print(DISCLAIMER, f"({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
