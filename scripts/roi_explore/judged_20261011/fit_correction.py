"""T008: ①-c 判断時の 2 段補正(軽量版)の 4 係数を凍結 → ``coefficients.json``。

    logit p' = b0 + b1·logit p̂_J + b2·log h + b3·log q_J      (h は分、q_J は判断時オッズの市場 vote-share)

c1: R02 の実組(scored_pairs.parquet・443 レース・5,967 頭)。p̂_J = EVe_J / O_J(現行 15 seed 平均の判断時 EV)、
    h = hours×60、q_J は O_J から r02_drift.market_block で再計算(pair_id 単位)、ラベル won。
c2: 2025 年の 15 seed OOF 行(armC_binary*…ens15/predictions.parquet・year==2025)に生成器を当てた模擬判断時行。
    h を R02 の組の horizon 分布から race ごとに抽出 → O_J を生成 → 市場 12 列を再計算 → p̂_J は現行 2025 booster
    (seed_NN/model_2025.txt・15 seed 平均)を摂動後の特徴で再推論。
どちらも正則化なしのロジスティック回帰(numpy IRLS)。n・対数尤度・係数の SE を記録する。評価時に再 fit しない(FR-008)。

    cd training && uv run python ../scripts/roi_explore/judged_20261011/fit_correction.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C  # noqa: E402

from horseracing_training import market_ev  # noqa: E402

COEF_NAMES = ("b0_intercept", "b1_logit_p", "b2_log_h", "b3_log_q")
C2_PERTURB_SEED = 20261011 + 100
OOF_YEAR = 2025


def result_tag(seed: int) -> str:
    return (
        "armC_binary_drop-sameday+weightlive_from2007_ens15"
        if seed == 1
        else f"armC_binary_seed{seed}_drop-sameday+weightlive_from2007_ens15"
    )


def design(p_hat: np.ndarray, h_min: np.ndarray, q: np.ndarray) -> np.ndarray:
    return np.column_stack(
        [
            np.ones(len(p_hat)),
            C.logit(p_hat),
            np.log(np.asarray(h_min, float)),
            np.log(np.clip(np.asarray(q, float), 1e-9, None)),
        ]
    )


def fit_logistic(X: np.ndarray, y: np.ndarray, *, max_iter: int = 100, tol: float = 1e-10) -> dict:
    """正則化なしロジスティック回帰(Newton/IRLS)。係数・SE(逆ヘッセ行列)・対数尤度・反復数。"""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    beta = np.zeros(X.shape[1])
    converged = False
    ll_prev = -np.inf
    for it in range(1, max_iter + 1):
        z = X @ beta
        p = C.sigmoid(z)
        ll = float(
            np.sum(
                y * np.log(np.clip(p, 1e-300, None))
                + (1 - y) * np.log(np.clip(1 - p, 1e-300, None))
            )
        )
        w = p * (1 - p)
        H = X.T @ (X * w[:, None])
        g = X.T @ (y - p)
        step = np.linalg.solve(H, g)
        beta = beta + step
        if abs(ll - ll_prev) < tol and np.max(np.abs(step)) < 1e-8:
            converged = True
            break
        ll_prev = ll
    p = C.sigmoid(X @ beta)
    w = p * (1 - p)
    H = X.T @ (X * w[:, None])
    cov = np.linalg.inv(H)
    se = np.sqrt(np.diag(cov))
    ll = float(
        np.sum(
            y * np.log(np.clip(p, 1e-300, None)) + (1 - y) * np.log(np.clip(1 - p, 1e-300, None))
        )
    )
    ll_null = float(len(y) * (y.mean() * np.log(y.mean()) + (1 - y.mean()) * np.log(1 - y.mean())))
    return {
        "coef": {k: float(v) for k, v in zip(COEF_NAMES, beta, strict=True)},
        "se": {k: float(v) for k, v in zip(COEF_NAMES, se, strict=True)},
        "n": int(len(y)),
        "n_won": int(y.sum()),
        "loglik": ll,
        "loglik_null": ll_null,
        "loglik_identity": float(-np.sum(C.nll_binary(C.sigmoid(X[:, 1]), y))),
        "n_iter": it,
        "converged": bool(converged),
    }


def apply_correction(coef: dict, p_hat: np.ndarray, h_min: np.ndarray, q: np.ndarray) -> np.ndarray:
    b = np.array([coef[k] for k in COEF_NAMES], float)
    return C.sigmoid(design(p_hat, h_min, q) @ b)


# ------------------------------------------------------------------ c1


def c1_frame(scored: pd.DataFrame, r02) -> pd.DataFrame:
    d = scored.sort_values(["pair_id", "horse_number"]).reset_index(drop=True)
    if d["dead_heat"].any():
        d = d[~d["dead_heat"].astype(bool)].reset_index(drop=True)
    d = d[(d["O_J"] > 0) & (d["EVe_J"].notna())].reset_index(drop=True)
    d["odds"] = d["O_J"].astype(float)
    m = r02.market_block(d[["pair_id", "horse_id", "horse_number", "odds"]].copy(), "pair_id")
    d["q_J"] = m["q"].to_numpy()
    d["p_hat_J"] = d["EVe_J"].to_numpy(float) / d["O_J"].to_numpy(float)
    d["h_min"] = np.maximum(d["hours"].to_numpy(float) * 60.0, 1.0)
    return d


def fit_c1(scored_path: pathlib.Path, r02) -> dict:
    S = pd.read_parquet(scored_path)
    dates = pd.to_datetime(S["race_date"]).dt.date
    if str(dates.min()) < C.FIT_PERIOD_FROM or str(dates.max()) > C.FIT_PERIOD_TO:
        raise ValueError("scored_pairs outside the registered fit period")
    d = c1_frame(S, r02)
    fit = fit_logistic(
        design(d["p_hat_J"].to_numpy(), d["h_min"].to_numpy(), d["q_J"].to_numpy()),
        d["won"].to_numpy(float),
    )
    fit.update(
        {
            "source": "R02 scored_pairs (real judged/final pairs)",
            "input": str(scored_path.relative_to(C.ROOT)),
            "sha256": C.sha256_file(scored_path),
            "n_races": int(d["pair_id"].nunique()),
            "period": {"from": str(dates.min()), "to": str(dates.max())},
            "p_hat_definition": "EVe_J / O_J (15-seed ensemble EV at judgement time)",
            "q_definition": "recomputed from O_J per pair with r02_drift.market_block",
            "h_minutes_quantiles": {
                str(q): float(np.quantile(d["h_min"], q)) for q in (0.1, 0.5, 0.9)
            },
        }
    )
    return fit


# ------------------------------------------------------------------ c2


def load_oof_2025(results_dir: pathlib.Path) -> pd.DataFrame:
    """15 seed の 2025 OOF: p_s = (pred/100 + 1)/odds の平均 p̄(parity 確認用)。"""
    acc = None
    for s in C.SEEDS:
        p = results_dir / result_tag(s) / "predictions.parquet"
        d = pd.read_parquet(p)
        d = d[d["year"] == OOF_YEAR][["race_id", "horse_id", "odds", "pred"]].copy()
        d[f"p_{s}"] = (d["pred"].to_numpy(float) / 100.0 + 1.0) / d["odds"].to_numpy(float)
        d = d.drop(columns=["pred", "odds"])
        acc = (
            d
            if acc is None
            else acc.merge(d, on=["race_id", "horse_id"], how="inner", validate="1:1")
        )
    acc["p_bar"] = acc[[f"p_{s}" for s in C.SEEDS]].mean(axis=1)
    return acc[["race_id", "horse_id", "p_bar"]]


def load_2025_boosters(ens_dir: pathlib.Path):
    import lightgbm as lgb

    paths = [ens_dir / f"seed_{s:02d}" / f"model_{OOF_YEAR}.txt" for s in C.SEEDS]
    return [lgb.Booster(model_file=str(p)) for p in paths], [C.sha256_file(p) for p in paths]


def c2_frame(
    rows: pd.DataFrame,
    oof: pd.DataFrame,
    gen: dict,
    h_pool: np.ndarray,
    r02,
    spec: dict,
    boosters: list,
    *,
    seed: int = C2_PERTURB_SEED,
) -> tuple[pd.DataFrame, dict]:
    d = rows.loc[
        rows["race_ok"].astype(bool) & ~rows["dead_heat"].astype(bool) & (rows["year"] == OOF_YEAR)
    ]
    d = d.merge(oof, on=["race_id", "horse_id"], how="inner", validate="1:1")
    d = d.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    seg, n_r = C.race_segments(d["race_id"].to_numpy())
    rng = np.random.default_rng(seed)
    mins = rng.choice(h_pool, size=n_r)
    # parity: unperturbed re-inference must reproduce the stored OOF p̄
    X0 = market_ev._design_matrix(spec, d)
    p0 = np.mean(np.vstack([b.predict(X0, num_threads=1) for b in boosters]), axis=0)
    parity = {"max_abs_diff": float(np.max(np.abs(p0 - d["p_bar"].to_numpy()))), "n": int(len(d))}
    oj = C.perturb_odds(d["odds"].to_numpy(float), seg, n_r, mins, gen, rng)
    pj = d.copy()
    pj["O_F"] = pj["odds"].to_numpy(float)
    pj["odds"] = oj
    pj = C.apply_market_block(pj, r02)
    XJ = market_ev._design_matrix(spec, pj)
    pj["p_hat_J"] = np.mean(np.vstack([b.predict(XJ, num_threads=1) for b in boosters]), axis=0)
    pj["q_J"] = pj["q"].to_numpy(float)
    pj["h_min"] = mins[seg]
    info = {
        "n_races": int(n_r),
        "perturb_seed": seed,
        "parity_unperturbed_vs_oof": parity,
        "h_minutes_quantiles": {str(q): float(np.quantile(mins, q)) for q in (0.1, 0.5, 0.9)},
        "mean_log_ratio_oj_of": float(np.mean(np.log(oj / pj["O_F"].to_numpy()))),
    }
    return pj, info


def fit_c2(
    rows_path: pathlib.Path,
    results_dir: pathlib.Path,
    ens_dir: pathlib.Path,
    gen_path: pathlib.Path,
    pairs_path: pathlib.Path,
    r02,
) -> dict:
    gen = C.read_json(gen_path)
    pairs = pd.read_parquet(pairs_path)
    rows = pd.read_parquet(rows_path)
    oof = load_oof_2025(results_dir)
    spec = C.read_json(ens_dir / market_ev.ENSEMBLE_SPEC_NAME)
    boosters, shas = load_2025_boosters(ens_dir)
    pj, info = c2_frame(rows, oof, gen, C.horizon_pool_minutes(pairs), r02, spec, boosters)
    fit = fit_logistic(
        design(pj["p_hat_J"].to_numpy(), pj["h_min"].to_numpy(), pj["q_J"].to_numpy()),
        pj["won"].to_numpy(float),
    )
    fit.update(
        {
            "source": f"simulated judged rows: {OOF_YEAR} OOF rows + generator; p_hat from the 15 "
            f"production model_{OOF_YEAR}.txt boosters on perturbed features",
            "input": {
                "rows": str(rows_path.relative_to(C.ROOT)),
                "rows_sha256": C.sha256_file(rows_path),
                "generator_sha256": C.sha256_file(gen_path),
                "horizon_pool_sha256": C.sha256_file(pairs_path),
                "oof_results_dir": str(results_dir.relative_to(C.ROOT)),
                "boosters_sha256": dict(zip([f"seed_{s:02d}" for s in C.SEEDS], shas, strict=True)),
            },
            **info,
        }
    )
    return fit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(C.COEFFICIENTS_JSON))
    ap.add_argument("--scored", default=str(C.SCORED_PAIRS))
    ap.add_argument("--rows", default=str(C.ROWS_2007))
    ap.add_argument("--results-dir", default=str(C.ENS_RESULTS))
    ap.add_argument("--ens-dir", default=str(C.ENS_DIR))
    ap.add_argument("--generator", default=str(C.GENERATOR_JSON))
    ap.add_argument("--pairs", default=str(C.PAIRS))
    args = ap.parse_args(argv)
    t0 = time.time()
    r02 = C.load_r02()
    c1 = fit_c1(pathlib.Path(args.scored), r02)
    print(
        "c1",
        {k: round(v, 4) for k, v in c1["coef"].items()},
        "n",
        c1["n"],
        "ll",
        round(c1["loglik"], 2),
        f"({time.time() - t0:.0f}s)",
        flush=True,
    )
    c2 = fit_c2(
        pathlib.Path(args.rows),
        pathlib.Path(args.results_dir),
        pathlib.Path(args.ens_dir),
        pathlib.Path(args.generator),
        pathlib.Path(args.pairs),
        r02,
    )
    print(
        "c2",
        {k: round(v, 4) for k, v in c2["coef"].items()},
        "n",
        c2["n"],
        "ll",
        round(c2["loglik"], 2),
        "parity",
        c2["parity_unperturbed_vs_oof"],
        f"({time.time() - t0:.0f}s)",
        flush=True,
    )
    out = {
        "name": "judged-ev-logit-correction",
        "version": "v0",
        "model": "logit p' = b0 + b1*logit(p_hat_J) + b2*log(h_minutes) + b3*log(q_J)",
        "coef_names": list(COEF_NAMES),
        "fit_period_registered": {"from": C.FIT_PERIOD_FROM, "to": C.FIT_PERIOD_TO},
        "refit_at_evaluation": False,
        "c1": c1,
        "c2": c2,
        "disclaimers": list(C.DISCLAIMERS),
        "elapsed_s": round(time.time() - t0, 1),
    }
    C.write_json(pathlib.Path(args.out), out)
    print("→", args.out)
    for line in C.DISCLAIMERS:  # FR-011: on every output, stdout included
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
