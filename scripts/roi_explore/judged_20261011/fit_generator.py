"""T006: 注入生成器の推定と凍結 → ``generator.json``。

R02 の pairs.parquet(判断時 O_J と確定 O_F の組・2026-08-02〜10-04)から、horizon 帯ごとに
``u = log(O_J/O_F) = a + b·log q_F + ε`` の最小二乗 a・b と残差 sd s、残差のレース共通成分の分散比 ρ
(レース内平均残差の分散 / 全残差分散。帯別と pooled)を推定する。参照実装は
docs/roi-model-improvement-20261010/evidence/decision_odds/m2_drift_reverse.py。

    cd training && uv run python ../scripts/roi_explore/judged_20261011/fit_generator.py [--pairs P] [--out generator.json]
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C  # noqa: E402

#: m3_spike.py の HB 表(m2 の推定値)。食い違いの確認用に generator.json に併記する。
REFERENCE_M2 = {
    "bands": [
        {"band": "(0,10]", "a": 0.341, "b": 0.142, "s": 0.241},
        {"band": "(10,30]", "a": 0.522, "b": 0.220, "s": 0.273},
        {"band": "(30,60]", "a": 0.639, "b": 0.274, "s": 0.301},
        {"band": "(60,180]", "a": 0.674, "b": 0.292, "s": 0.343},
        {"band": "(180,600]", "a": 0.803, "b": 0.336, "s": 0.338},
        {"band": "(600,3000]", "a": 1.029, "b": 0.364, "s": 0.741},
    ],
    "rho": 0.16,
}


def icc_rho(res: np.ndarray, groups: np.ndarray) -> tuple[float, float]:
    """(ρ_ICC, ρ_racemean): ρ_ICC = 1 − pooled within-race residual variance / total residual
    variance(レース共通成分の分散比・注入に使う)。ρ_racemean = var(レース内平均残差)/var(残差)は
    (1−ρ)/n̄ だけ上に偏る旧推定量(m2 の参照値 0.16 はこれ)で、比較のためだけに併記する。"""
    s = pd.Series(np.asarray(res, float))
    g = s.groupby(np.asarray(groups))
    n = g.size()
    dof = float((n - 1).sum())
    total = float(s.var(ddof=1))
    if dof <= 0 or not np.isfinite(total) or total <= 0:
        return float("nan"), float("nan")
    within = float(((s - g.transform("mean")) ** 2).sum() / dof)
    rho_icc = float(np.clip(1.0 - within / total, 0.0, 1.0))
    rho_racemean = float(g.transform("mean").var(ddof=1) / total)
    return rho_icc, rho_racemean


def fit_bands(pairs: pd.DataFrame) -> dict:
    """帯別 OLS と ρ。``pairs`` は O_J・O_F・q_F・hours・pair_id を持つ。"""
    d = pairs.loc[(pairs["O_J"] > 0) & (pairs["O_F"] > 0) & (pairs["q_F"] > 0)].copy()
    d["mins"] = d["hours"].to_numpy(dtype=float) * 60.0
    d["u"] = np.log(d["O_J"].to_numpy(float) / d["O_F"].to_numpy(float))
    d["lq"] = np.log(d["q_F"].to_numpy(float))
    d["band"] = C.h_band_index(d["mins"].to_numpy())
    if (d["band"] < 0).any():
        raise ValueError("pairs horizon outside (0, 3000] minutes")
    bands = []
    resid = np.full(len(d), np.nan)
    for i, label in enumerate(C.H_BAND_LABELS):
        m = (d["band"] == i).to_numpy()
        g = d[m]
        if len(g) < 3:
            raise ValueError(f"band {label}: too few rows ({len(g)})")
        X = np.column_stack([np.ones(len(g)), g["lq"].to_numpy()])
        beta, *_ = np.linalg.lstsq(X, g["u"].to_numpy(), rcond=None)
        res = g["u"].to_numpy() - X @ beta
        resid[m] = res
        s = float(np.std(res, ddof=1))
        rho_b, rho_b_rm = icc_rho(res, g["pair_id"].to_numpy())
        bands.append(
            {
                "band": label,
                "lo_min": C.H_BAND_EDGES[i],
                "hi_min": C.H_BAND_EDGES[i + 1],
                "a": float(beta[0]),
                "b": float(beta[1]),
                "s": s,
                "rho_band": rho_b,
                "rho_band_racemean_estimator": rho_b_rm,
                "n": int(len(g)),
                "n_pairs": int(g["pair_id"].nunique()),
            }
        )
    rho, rho_rm = icc_rho(resid, d["pair_id"].to_numpy())
    return {
        "bands": bands,
        "rho": rho,
        "rho_racemean_estimator": rho_rm,
        "n": int(len(d)),
        "n_pairs": int(d["pair_id"].nunique()),
        "n_races": int(d["race_id"].nunique()) if "race_id" in d else None,
        "horizon_pool_minutes": {
            "n_pairs": int(d["pair_id"].nunique()),
            "quantiles": {
                str(q): float(np.quantile(C.horizon_pool_minutes(d), q))
                for q in (0.1, 0.25, 0.5, 0.75, 0.9)
            },
        },
    }


def compare_reference(fit: dict) -> dict:
    diffs = []
    for mine, ref in zip(fit["bands"], REFERENCE_M2["bands"], strict=True):
        diffs.append(
            {
                "band": mine["band"],
                "da": round(mine["a"] - ref["a"], 4),
                "db": round(mine["b"] - ref["b"], 4),
                "ds": round(mine["s"] - ref["s"], 4),
            }
        )
    return {
        "reference": REFERENCE_M2,
        "diff_fit_minus_reference": diffs,
        "drho_icc_minus_reference": round(fit["rho"] - REFERENCE_M2["rho"], 4),
        "drho_racemean_minus_reference": round(fit["rho_racemean_estimator"] - REFERENCE_M2["rho"], 4),
    }


def build_generator(pairs: pd.DataFrame, *, pairs_path: pathlib.Path | None) -> dict:
    dates = pd.to_datetime(pairs["race_date"]).dt.date
    period = {"from": str(dates.min()), "to": str(dates.max())}
    if period["from"] < C.FIT_PERIOD_FROM or period["to"] > C.FIT_PERIOD_TO:
        raise ValueError(
            f"pairs period {period} outside the registered fit period "
            f"{C.FIT_PERIOD_FROM}..{C.FIT_PERIOD_TO}"
        )
    fit = fit_bands(pairs)
    out = {
        "name": "judged-odds-injection-generator",
        "version": "v0",
        "method": (
            "per horizon band (minutes, right-closed): OLS u=log(O_J/O_F)=a+b*log(q_F)+eps; "
            "s=std(resid, ddof=1); rho = 1 - pooled within-race resid variance / total resid variance "
            "(ICC, pooled over bands; the race-mean-variance estimator used by m2 is biased up by "
            "(1-rho)/n and is only reported for comparison); "
            "injection u=a+b*log q_F+s*(sqrt(rho)*eps_race+sqrt(1-rho)*eps_i), O_J=O_F*exp(u), "
            "renormalise sum(1/O) per race to the final value, round to 0.1, floor 1.0"
        ),
        "period": period,
        "fit_period_registered": {"from": C.FIT_PERIOD_FROM, "to": C.FIT_PERIOD_TO},
        "input": {
            "pairs_parquet": str(pairs_path.relative_to(C.ROOT)) if pairs_path else None,
            "sha256": C.sha256_file(pairs_path) if pairs_path else None,
            "n_rows": int(len(pairs)),
            "n_pairs": int(pairs["pair_id"].nunique()),
            "n_races": int(pairs["race_id"].nunique()),
            "n_days": int(dates.nunique()),
            "sources": {str(k): int(v) for k, v in pairs["source"].value_counts().items()}
            if "source" in pairs
            else None,
        },
        "bands": fit["bands"],
        "rho": fit["rho"],
        "rho_racemean_estimator": fit["rho_racemean_estimator"],
        "n": fit["n"],
        "horizon_pool_minutes": fit["horizon_pool_minutes"],
        "comparison_with_m2_reference": compare_reference(fit),
        "disclaimers": list(C.DISCLAIMERS),
    }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=str(C.PAIRS))
    ap.add_argument("--out", default=str(C.GENERATOR_JSON))
    args = ap.parse_args(argv)
    pairs_path = pathlib.Path(args.pairs)
    pairs = pd.read_parquet(pairs_path)
    gen = build_generator(pairs, pairs_path=pairs_path)
    C.write_json(pathlib.Path(args.out), gen)
    for b in gen["bands"]:
        print(
            f"{b['band']:>11}: a={b['a']:+.3f} b={b['b']:+.3f} s={b['s']:.3f} rho_band={b['rho_band']:.3f} "
            f"n={b['n']} pairs={b['n_pairs']}"
        )
    print(f"rho(pooled)={gen['rho']:.4f} n={gen['n']} pairs={gen['input']['n_pairs']} → {args.out}")
    for line in C.DISCLAIMERS:  # FR-011: on every output, stdout included
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
