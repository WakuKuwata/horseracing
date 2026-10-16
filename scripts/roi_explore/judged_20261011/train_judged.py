"""T007: 判断時専用モデル ①-a ``mev-ens15-judged-v0`` の 2026 年 booster を 1 seed 学習する(研究 artifact・本番非結線)。

学習行 = rows_2007.parquet(race_ok ∧ ¬dead_heat ∧ year≤2025)を 2 倍にしたもの:
  未摂動(log_min=0=発走 1 分前相当)+ 注入(h を R02 の組の horizon 分布から race ごとに抽出・generator.json の
  帯別 a/b/s/ρ で u を生成・O_J=O_F·exp(u)・レース内 Σ1/O を確定と同じに再正規化・0.1 刻み下限 1.0・
  市場 12 列を r02_drift.market_block で再計算・log_min=log h)。
特徴 = 現行 spec の 66 features + 'log_min'(67)+ 11 cats = 78 列。params は現行 + 1 スレッド・決定論。
出力 = seed_NN/model_2026.txt・seed_NN/model.spec.json(judged: true)。

    cd training && uv run python ../scripts/roi_explore/judged_20261011/train_judged.py --seed 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C  # noqa: E402

from horseracing_training import market_ev  # noqa: E402

PERTURB_SEED_BASE = 20261011
TRAIN_THROUGH = 2025
TRAINED_FOR = 2026


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def judged_spec_from(base_spec: dict) -> dict:
    """現行 spec(66 features・11 cats・cat_maps)に 'log_min' を足した判断時専用 spec の骨格。"""
    feats = list(base_spec["features"]) + ["log_min"]
    cats = list(base_spec["cats"])
    return {
        "objective": "binary",
        "features": feats,
        "cats": cats,
        "cat_maps": base_spec["cat_maps"],
        "feature_hash": hashlib.sha256(
            json.dumps(feats + cats, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
    }


def make_training_frame(
    rows: pd.DataFrame,
    gen: dict,
    h_pool: np.ndarray,
    r02,
    *,
    seed: int,
    limit_rows: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """未摂動 + 注入の 2 倍フレーム。rows は (race_id, horse_number) 順・race_ok ∧ ¬dead_heat ∧ year≤2025。"""
    tr = rows.loc[
        rows["race_ok"].astype(bool)
        & ~rows["dead_heat"].astype(bool)
        & (rows["year"] <= TRAIN_THROUGH)
    ]
    tr = tr.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    if limit_rows is not None:
        cut = tr.iloc[:limit_rows]
        tr = tr[tr["race_id"].isin(cut["race_id"].unique())].reset_index(drop=True)
    seg, n_r = C.race_segments(tr["race_id"].to_numpy())
    rng = np.random.default_rng(PERTURB_SEED_BASE + seed)
    mins = rng.choice(h_pool, size=n_r)
    oj = C.perturb_odds(tr["odds"].to_numpy(float), seg, n_r, mins, gen, rng)
    pt = tr.copy()
    pt["odds"] = oj
    pt = C.apply_market_block(pt, r02)
    pt["log_min"] = np.log(mins)[seg]
    tr0 = tr.copy()
    tr0["log_min"] = 0.0
    trn = pd.concat([tr0, pt], ignore_index=True)
    info = {
        "n_races": int(n_r),
        "n_rows_unperturbed": int(len(tr0)),
        "n_rows_injected": int(len(pt)),
        "perturb_seed": PERTURB_SEED_BASE + seed,
        "h_minutes_sampled_quantiles": {
            str(q): float(np.quantile(mins, q)) for q in (0.1, 0.5, 0.9)
        },
        "mean_log_ratio_oj_of": float(np.mean(np.log(oj / tr["odds"].to_numpy(float)))),
    }
    return trn, info


def train(args) -> dict:
    t0 = time.time()
    base_spec = C.read_json(pathlib.Path(args.base_spec))
    spec = judged_spec_from(base_spec)
    gen = C.read_json(pathlib.Path(args.generator))
    pairs = pd.read_parquet(args.pairs)
    h_pool = C.horizon_pool_minutes(pairs)
    r02 = C.load_r02()
    rows = pd.read_parquet(args.rows)
    log(f"rows {len(rows):,} loaded ({time.time() - t0:.0f}s)")
    trn, info = make_training_frame(
        rows, gen, h_pool, r02, seed=args.seed, limit_rows=args.limit_rows
    )
    log(
        f"training frame {len(trn):,} rows ({info['n_races']:,} races x2) ({time.time() - t0:.0f}s)"
    )
    X = market_ev._design_matrix(spec, trn)
    y = trn["won"].to_numpy(dtype=np.float64)
    n_feat = len(spec["features"])
    cat_idx = list(range(n_feat, n_feat + len(spec["cats"])))
    params = dict(C.LGB_PARAMS, seed=int(args.seed))
    t1 = time.time()
    bst = lgb.train(
        params,
        lgb.Dataset(X, y, categorical_feature=cat_idx, free_raw_data=True),
        num_boost_round=args.rounds,
    )
    fit_s = time.time() - t1
    out_dir = pathlib.Path(args.out) / f"seed_{args.seed:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / f"model_{TRAINED_FOR}.txt"
    bst.save_model(str(model_path))
    spec_out = dict(spec)
    spec_out.update(
        {
            "version": C.JUDGED_VERSION,
            "judged": True,
            "trained_for_year": TRAINED_FOR,
            "train_through": TRAIN_THROUGH,
            "train_from": 2007,
            "training_cutoff_by_year": {
                str(TRAINED_FOR): {"train_from": 2007, "train_through": TRAIN_THROUGH}
            },
            "seed": int(args.seed),
            "rounds": int(args.rounds),
            "num_threads": 1,
            "deterministic": True,
            "params": params,
            "drop_groups": sorted(base_spec.get("drop_groups") or ["sameday", "weightlive"]),
            "log_min_unperturbed": 0.0,
            "generator_sha256": C.sha256_file(pathlib.Path(args.generator)),
            "generator_rho": gen["rho"],
            "horizon_pool_sha256": C.sha256_file(pathlib.Path(args.pairs)),
            "input_rows_sha256": C.sha256_file(pathlib.Path(args.rows)),
            "base_spec_sha256": C.sha256_file(pathlib.Path(args.base_spec)),
            "base_feature_hash": base_spec.get("feature_hash"),
            "training_frame": info,
            "limit_rows": args.limit_rows,
            "model_sha256": C.sha256_file(model_path),
            "fit_seconds": round(fit_s, 1),
            "total_seconds": round(time.time() - t0, 1),
            "not_for_production": "research artifact; EnsembleMarketEvModel.load must not accept this spec",
            "disclaimers": list(C.DISCLAIMERS),
        }
    )
    C.write_json(out_dir / "model.spec.json", spec_out)
    log(f"saved {model_path} (fit {fit_s:.0f}s, total {time.time() - t0:.0f}s)")
    return spec_out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", default=str(C.JUDGED_DIR))
    ap.add_argument("--rows", default=str(C.ROWS_2007))
    ap.add_argument("--pairs", default=str(C.PAIRS))
    ap.add_argument("--generator", default=str(C.GENERATOR_JSON))
    ap.add_argument("--base-spec", default=str(C.ENS_DIR / "seed_01" / "model.spec.json"))
    ap.add_argument("--rounds", type=int, default=C.ROUNDS)
    ap.add_argument(
        "--limit-rows",
        type=int,
        default=None,
        help="tests: use only the first N rows (whole races)",
    )
    args = ap.parse_args(argv)
    if args.seed not in C.SEEDS:
        raise SystemExit(f"--seed must be in 1..15, got {args.seed}")
    train(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
