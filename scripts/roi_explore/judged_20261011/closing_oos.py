"""T012 / FR-010 (i) / SC-003: 判断時専用版 ①-a の確定オッズ 2026 OOS 非劣化確認。

rows_2007.parquet の 2026 年(race_ok ∧ ¬dead_heat ∧ race_date ≤ 2026-06-26=確定オッズが汚れていない窓)で、
現行 ens15(2026 booster・15 seed 平均)と judged(log_min=0=未摂動・15 seed 平均)の馬単位 NLL と
レース内正規化 winner NLL を比べる。差の 95% 区間は開催日ブロック bootstrap(B=4000・seed=20261011)。
規則 (i): judged NLL ≤ ens15 + 0.0002 かつ winner NLL の paired 差 ≤ 0(点推定)。出力は results/closing_oos_2026.json
(append-only)。ROI の改善を示すものではない。判断時専用版は表示・確定側に接続しない。

    cd training && uv run python ../scripts/roi_explore/judged_20261011/closing_oos.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C  # noqa: E402
import score as SC  # noqa: E402

from horseracing_training import market_ev  # noqa: E402

CLOSING_WINDOW_TO = "2026-06-26"
TOL_NLL = 0.0002


def load_window(rows_path: pathlib.Path, window_to: str = CLOSING_WINDOW_TO) -> pd.DataFrame:
    rows = pd.read_parquet(rows_path)
    d = rows.loc[
        rows["race_ok"].astype(bool)
        & ~rows["dead_heat"].astype(bool)
        & (rows["year"] == 2026)
        & (pd.to_datetime(rows["race_date"]) <= window_to)
    ]
    return d.sort_values(["race_id", "horse_number"]).reset_index(drop=True)


def predict_mean(spec: dict, boosters: list, d: pd.DataFrame) -> np.ndarray:
    X = market_ev._design_matrix(spec, d)
    return np.mean(np.vstack([b.predict(X, num_threads=1) for b in boosters]), axis=0)


def compare(d: pd.DataFrame, p_ref: np.ndarray, p_arm: np.ndarray, *, b=SC.BOOT_B, seed=SC.BOOT_SEED) -> dict:
    from horseracing_eval.bootstrap import block_bootstrap_counts

    df = d[["race_id", "race_date", "won", "odds"]].copy()
    df["odds_used"] = df["odds"].astype(float)
    df["day"] = pd.to_datetime(df["race_date"]).dt.date.astype(str)
    days = sorted(df["day"].unique())
    day_idx = df["day"].map({x: i for i, x in enumerate(days)}).to_numpy()
    counts = block_bootstrap_counts(len(days), b, seed)
    series = SC.paired_series(df, p_arm, p_ref)
    paired = SC.paired_bootstrap(day_idx, len(days), series, counts)
    y = df["won"].to_numpy(bool)
    out = {
        "n_rows": int(len(df)),
        "n_races": int(df["race_id"].nunique()),
        "n_days": len(days),
        "window": {"from": days[0], "to": days[-1]},
        "ens15": {
            "nll": float(C.nll_binary(p_ref, y).mean()),
            "winner_nll": float(np.nanmean(C.winner_nll_per_race(p_ref, df["race_id"].to_numpy(), y))),
            "ev_gt12_n": int(((p_ref * df["odds_used"].to_numpy()) > 1.2).sum()),
        },
        "judged_h0": {
            "nll": float(C.nll_binary(p_arm, y).mean()),
            "winner_nll": float(np.nanmean(C.winner_nll_per_race(p_arm, df["race_id"].to_numpy(), y))),
            "ev_gt12_n": int(((p_arm * df["odds_used"].to_numpy()) > 1.2).sum()),
        },
        "paired_judged_minus_ens15": paired,
        "bootstrap": {"method": "race-day block bootstrap", "B": b, "seed": seed, "n_blocks": len(days)},
    }
    d_nll = paired["nll_J"]["point"]
    d_w = paired["winner_nll_J"]["point"]
    out["rule_i"] = {
        "nll_within_tolerance": bool(d_nll is not None and d_nll <= TOL_NLL),
        "winner_nll_not_worse": bool(d_w is not None and d_w <= 0.0),
        "tolerance_nll": TOL_NLL,
        "pass": bool(d_nll is not None and d_nll <= TOL_NLL and d_w is not None and d_w <= 0.0),
        "note": "FR-010 (i): closing-odds 2026 OOS (<= 2026-06-26), judged scored with log_min=0",
    }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default=str(C.ROWS_2007))
    ap.add_argument("--ens-dir", default=str(C.ENS_DIR))
    ap.add_argument("--judged-dir", default=str(C.JUDGED_DIR))
    ap.add_argument("--out", default=str(C.RESULTS_DIR / "closing_oos_2026.json"))
    ap.add_argument(
        "--window-to",
        default=CLOSING_WINDOW_TO,
        help="diagnostic only: extend the window past 2026-06-26 (stored odds after that date "
        "were pre-race values until 139's repair, so it is no longer a closing-odds test)",
    )
    ap.add_argument(
        "--seeds",
        default="",
        help="diagnostic only: comma list of judged seeds (default all 15; fewer = diagnostic)",
    )
    args = ap.parse_args(argv)
    t0 = time.time()
    d = load_window(pathlib.Path(args.rows), args.window_to)
    ens_model, ens_boosters, ens_info = SC.load_ens15(pathlib.Path(args.ens_dir))
    seeds = tuple(int(x) for x in args.seeds.split(",") if x) or C.SEEDS
    spec_j, boosters_j, info_j = SC.load_judged(
        pathlib.Path(args.judged_dir), ens_model.spec["features"], seeds
    )
    if spec_j is None and args.seeds:
        spec_j, boosters_j, info_j = SC.load_judged_partial(
            pathlib.Path(args.judged_dir), ens_model.spec["features"], seeds
        )
    if spec_j is None:
        raise SystemExit(f"judged artifact incomplete: {info_j}")
    diagnostic = bool(args.seeds) or args.window_to != CLOSING_WINDOW_TO
    d = d.copy()
    d["log_min"] = 0.0
    p_ref = predict_mean(ens_model.spec, ens_boosters, d)
    p_arm = predict_mean(spec_j, boosters_j, d)
    res = compare(d, p_ref, p_arm)
    res.update(
        {
            "diagnostic_only": diagnostic,
            "diagnostic_note": (
                "window and/or seeds differ from the registered rule (i) check; not usable for the verdict"
                if diagnostic
                else None
            ),
            "computed_at": dt.datetime.now(dt.UTC).isoformat(),
            "inputs": {
                "rows": str(pathlib.Path(args.rows)),
                "rows_sha256": C.sha256_file(pathlib.Path(args.rows)),
                "ens15": ens_info,
                "judged": info_j,
            },
            "disclaimers": list(C.DISCLAIMERS),
            "elapsed_s": round(time.time() - t0, 1),
        }
    )
    SC.write_result(pathlib.Path(args.out), res)
    pr = res["paired_judged_minus_ens15"]
    print(
        f"ens15 nll={res['ens15']['nll']:.5f} wnll={res['ens15']['winner_nll']:.4f} ev>1.2 n={res['ens15']['ev_gt12_n']} | "
        f"judged(h=0) nll={res['judged_h0']['nll']:.5f} wnll={res['judged_h0']['winner_nll']:.4f} ev>1.2 n={res['judged_h0']['ev_gt12_n']}"
    )
    print(
        f"dNLL {pr['nll_J']['point']:+.5f} [{pr['nll_J']['ci_low']:+.5f},{pr['nll_J']['ci_high']:+.5f}]  "
        f"dWNLL {pr['winner_nll_J']['point']:+.5f} [{pr['winner_nll_J']['ci_low']:+.5f},{pr['winner_nll_J']['ci_high']:+.5f}]  "
        f"rule(i) pass={res['rule_i']['pass']} → {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
