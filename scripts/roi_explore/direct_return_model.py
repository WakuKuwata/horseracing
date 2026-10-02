"""Arm C: 純収益を直接学習するモデル(LightGBM 回帰・年次 walk-forward)。

目的変数 = 100 円賭けたときの純収益(won×odds×100 − 100)。特徴 = design.md §1 の数値・カテゴリ列(オッズ・
市場 q を含む=「この価格でこの馬を買うと平均していくら返るか」を直接回帰する)。結果列は目的変数のみ。
学習は年 y の予測に y−1 年までのみ(strictly-before・年単位 refit)。政策は事前固定: 予測純収益 > τ の馬を均等額で買う。
τ は学習側の予測分布から決めない(固定値 {0, 5, 10, 20} 円)。診断として予測分位別の実現 ROI(単調性)を出す。

    cd training && uv run python ../scripts/roi_explore/direct_return_model.py --start-year 2000
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import evaluate as ev  # noqa: E402

ART = ev.ART
NUM = [
    "odds", "q", "popularity", "odds_rank", "q_share_of_fav", "fav_q", "fav_odds", "second_odds", "odds_gap12",
    "q_entropy_norm", "n_fav_under_2", "n_odds_under_10", "field_size", "distance", "race_number", "month", "dow",
    "prize_money", "is_graded", "is_last_race", "is_first_race", "age", "frame", "horse_number", "weight",
    "weight_diff", "jockey_weight", "is_debut", "career_starts", "career_wins", "career_win_rate",
    "career_top3_rate", "prev_finish", "prev2_finish", "prev3_finish", "avg_last3_finish", "best_finish_last5",
    "wins_last5", "top3_last5", "prev_popularity", "prev_odds", "prev_q", "prev_finish_pct", "prev_beat_market",
    "prev_field_size", "prev_distance", "dist_change", "class_change", "days_since_last", "tataki_2",
    "prev_weight", "weight_change_vs_prev", "prev_last3f_rank", "prev_margin_sec", "last_won", "jockey_change",
    "jockey_win_rate_365", "jockey_starts_365", "jockey_win_rate_all", "jockey_starts_all",
    "trainer_win_rate_365", "trainer_starts_365", "trainer_win_rate_all", "combo_starts_all",
    "combo_win_rate_all", "jockey_excess_365", "trainer_excess_365", "jockey_excess_all", "trainer_excess_all",
    "jockey_wins_today_before", "jockey_rides_today_before", "day_prev_n",
    "day_prev_fav_win_rate", "day_prev_winner_pop_mean", "day_prev_winner_style_front_share",
]
CAT = ["venue_code", "track_type", "going", "weather", "race_class_canon", "dist_band", "sex", "prev_running_style",
       "prev_class_canon", "prev_track_type", "sire_line"]
TAUS = [0.0, 5.0, 10.0, 20.0]
GROUPS = {
    "venue": ["venue_code"],
    "sameday": ["jockey_wins_today_before", "jockey_rides_today_before", "day_prev_n", "day_prev_fav_win_rate",
                "day_prev_winner_pop_mean", "day_prev_winner_style_front_share"],
    "human": ["jockey_win_rate_365", "jockey_starts_365", "jockey_win_rate_all", "jockey_starts_all",
              "trainer_win_rate_365", "trainer_starts_365", "trainer_win_rate_all", "combo_starts_all",
              "combo_win_rate_all", "jockey_excess_365", "trainer_excess_365", "jockey_excess_all",
              "trainer_excess_all"],
    "marketstruct": ["q_share_of_fav", "fav_q", "fav_odds", "second_odds", "odds_gap12", "q_entropy_norm",
                     "n_fav_under_2", "n_odds_under_10", "popularity", "odds_rank"],
    "history": ["prev_finish", "prev2_finish", "prev3_finish", "avg_last3_finish", "best_finish_last5", "wins_last5",
                "top3_last5", "prev_popularity", "prev_odds", "prev_q", "prev_finish_pct", "prev_beat_market",
                "prev_field_size", "prev_distance", "dist_change", "class_change", "days_since_last", "tataki_2",
                "prev_weight", "weight_change_vs_prev", "prev_last3f_rank", "prev_margin_sec", "last_won",
                "jockey_change", "career_starts", "career_wins", "career_win_rate", "career_top3_rate", "is_debut",
                "prev_running_style", "prev_class_canon", "prev_track_type"],
    "sire": ["sire_line"],
    "weight": ["weight", "weight_diff"],
    "odds": ["odds", "q"],
    # serving-time unavailable before weigh-in (~50 min before post): 当日馬体重とその派生
    "weightlive": ["weight", "weight_diff", "weight_change_vs_prev"],
}
GROUPS["all_but_odds"] = sorted({f for g, fs in GROUPS.items() if g != "odds" for f in fs}
                                | {"distance", "race_number", "month", "dow", "prize_money", "is_graded",
                                   "is_last_race", "is_first_race", "age", "frame", "horse_number", "jockey_weight",
                                   "field_size", "track_type", "going", "weather", "race_class_canon", "dist_band",
                                   "sex"})


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def file_sha256(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_spec(*, objective, feats, cats, cat_maps, last_year, params, rounds, train_from, drop_groups,
               training_cutoff_by_year, input_rows_sha256) -> dict:
    """model.spec.json の中身(138 で来歴キーを追加。既存キー objective/features/cats/cat_maps/trained_for_year/
    train_through は不変=137 の loader は未知キーを無視する)。feature_hash = sha256(json.dumps(features + cats))。"""
    import hashlib
    return {"objective": objective, "features": feats, "cats": cats, "cat_maps": cat_maps, "trained_for_year": last_year,
            "train_through": last_year - 1,
            "seed": int(params["seed"]), "rounds": int(rounds), "num_threads": int(params["num_threads"]),
            "deterministic": bool(params.get("deterministic", False)), "train_from": int(train_from),
            "drop_groups": sorted(g for g in drop_groups.split(",") if g),
            "feature_hash": hashlib.sha256(json.dumps(list(feats) + list(cats), ensure_ascii=False).encode("utf-8")).hexdigest(),
            "input_rows_sha256": input_rows_sha256,
            "training_cutoff_by_year": {str(k): v for k, v in sorted(training_cutoff_by_year.items())}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2000)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--rounds", type=int, default=300)
    ap.add_argument("--objective", default="regression", choices=["regression", "huber", "binary"],
                    help="binary = same features, LightGBM logloss on won; pred = 100*(p_hat*odds-1) (NLL-vs-return control)")
    ap.add_argument("--with-model-p", action="store_true", help="add OOF p/ev as features (2008+ only)")
    ap.add_argument("--boot", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--tag", default="")
    ap.add_argument("--drop-groups", default="", help="comma-separated GROUPS keys to drop (ablation)")
    ap.add_argument("--synthetic-q", type=int, default=0, help=">0: replace outcomes by winner~q draws with this seed (null world)")
    ap.add_argument("--train-from", type=int, default=1986, help="earliest training year (history-length sensitivity)")
    ap.add_argument("--save-last-model", default="", help="path prefix: save the last year's booster + feature/category spec")
    ap.add_argument("--rows", default="", help="alternative rows parquet (e.g. the 2007+ product export)")
    ap.add_argument("--deterministic", action="store_true",
                    help="LightGBM deterministic=True + force_row_wise=True (use with --threads 1 for bit-reproducible fits; 138)")
    ap.add_argument("--train-window-years", type=int, default=0,
                    help=">0: rolling window — train year y on [y-N, y-1] only (default 0 = expanding from --train-from)")
    args = ap.parse_args(argv)
    tag = f"armC_{args.objective}" + ("_withp" if args.with_model_p else "") + (f"_seed{args.seed}" if args.seed != 1 else "") \
        + (f"_drop-{args.drop_groups.replace(',', '+')}" if args.drop_groups else "") \
        + (f"_nullq{args.synthetic_q}" if args.synthetic_q else "") \
        + (f"_from{args.train_from}" if args.train_from != 1986 else "") \
        + (f"_win{args.train_window_years}" if args.train_window_years else "") + args.tag
    out_dir = ART / "results" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows_path = args.rows or (ART / "rows.parquet")
    df = pd.read_parquet(rows_path)
    df = df[df["race_ok"] & ~df["dead_heat"]].reset_index(drop=True)
    if args.synthetic_q:
        # null world: one winner per race drawn from market q (E[ROI] ≈ 1/overround); outcome columns replaced
        df = df.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
        rng = np.random.default_rng(args.synthetic_q)
        race_idx = pd.factorize(df["race_id"])[0]
        starts = np.r_[0, np.flatnonzero(np.diff(race_idx)) + 1, len(race_idx)]
        cq = np.cumsum(df["q"].to_numpy()); race_cum0 = np.r_[0.0, cq[starts[1:-1] - 1]]
        race_tot = cq[starts[1:] - 1] - race_cum0
        u = rng.random(len(starts) - 1) * race_tot + race_cum0
        w_rows = np.clip(np.searchsorted(cq, u, side="right"), starts[:-1], starts[1:] - 1)
        won = np.zeros(len(df), dtype=bool); won[w_rows] = True
        df["won"] = won
        log(f"synthetic-q world seed={args.synthetic_q}: all-horse ROI = {(won * df['odds'].to_numpy()).sum() / len(df):.4f}")
    df["ret"] = df["won"].astype(float) * df["odds"] * 100.0 - 100.0
    feats = list(NUM)
    drop = set()
    for g in [x for x in args.drop_groups.split(",") if x]:
        drop |= set(GROUPS[g])
    feats = [f for f in feats if f not in drop]
    cats = [c for c in CAT if c not in drop]
    if args.with_model_p:
        feats += ["p", "ev", "p_over_q", "p_rank", "p_gap12", "p_top3"]
    X = df[feats].astype(float).copy()
    for c in cats:
        codes, _ = pd.factorize(df[c].astype(object).where(df[c].notna(), None))
        X[c] = codes.astype(float); X.loc[codes < 0, c] = np.nan
    cat_maps = {}
    for c in cats:
        codes, uniques = pd.factorize(df[c].astype(object).where(df[c].notna(), None))
        cat_maps[c] = {str(u): int(i) for i, u in enumerate(uniques)}
    X = X.to_numpy(dtype=np.float32)
    y = df["ret"].to_numpy(dtype=np.float64)
    if args.objective == "binary":
        y = df["won"].to_numpy(dtype=np.float64)
    odds_all = df["odds"].to_numpy(dtype=np.float64)
    years = df["year"].to_numpy()
    cat_idx = [len(feats) + i for i in range(len(cats))]
    pred = np.full(len(df), np.nan)
    params = {"objective": args.objective, "learning_rate": 0.05, "num_leaves": 63, "min_data_in_leaf": 1000,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 10.0,
              "verbose": -1, "num_threads": args.threads, "seed": args.seed, "max_bin": 255}
    if args.objective == "huber":
        params["alpha"] = 200.0
    if args.deterministic:
        params["deterministic"] = True
        params["force_row_wise"] = True
    importances = {}
    cutoffs = {}
    if args.save_last_model:
        pathlib.Path(args.save_last_model).parent.mkdir(parents=True, exist_ok=True)  # 138: 親 dir を作る
    for yv in range(args.start_year, args.end_year + 1):
        tr = (years < yv) & (years >= args.train_from); te = years == yv
        if args.train_window_years:
            tr &= years >= yv - args.train_window_years
        if args.with_model_p:
            tr &= years >= 2008
        if te.sum() == 0 or tr.sum() < 50000:
            continue
        ds = lgb.Dataset(X[tr], y[tr], categorical_feature=cat_idx, free_raw_data=True)
        bst = lgb.train(params, ds, num_boost_round=args.rounds)
        pred[te] = bst.predict(X[te])
        if args.objective == "binary":
            pred[te] = 100.0 * (pred[te] * odds_all[te] - 1.0)  # implied expected net return per 100 yen
        imp = bst.feature_importance("gain")
        for n_, g in zip(feats + cats, imp):
            importances[n_] = importances.get(n_, 0.0) + float(g)
        log(f"year {yv}: train={tr.sum():,} test={te.sum():,} mean_pred={np.nanmean(pred[te]):.2f} ({time.time() - t0:.0f}s)")
        last_bst, last_year = bst, yv
        cutoffs[yv] = {"train_from": int(max(args.train_from, yv - args.train_window_years) if args.train_window_years else args.train_from),
                       "train_through": yv - 1}
        if args.save_last_model and yv >= 2019:
            bst.save_model(f"{args.save_last_model}_{yv}.txt")
    if args.save_last_model:
        last_bst.save_model(args.save_last_model + ".txt")
        pathlib.Path(args.save_last_model + ".spec.json").write_text(json.dumps(model_spec(
            objective=args.objective, feats=feats, cats=cats, cat_maps=cat_maps, last_year=last_year, params=params,
            rounds=args.rounds, train_from=args.train_from, drop_groups=args.drop_groups, training_cutoff_by_year=cutoffs,
            input_rows_sha256=file_sha256(rows_path)), ensure_ascii=False))
        log(f"saved last model (for year {last_year}) → {args.save_last_model}.txt")
    df["pred"] = pred
    ok = ~np.isnan(pred)
    d = df[ok].copy()
    # ---- diagnostics: monotonicity of realized ROI by predicted-return decile (per-year deciles → pooled)
    d["dec"] = d.groupby("year")["pred"].transform(lambda s: pd.qcut(s.rank(method="first"), 10, labels=False))
    dec = d.groupby("dec").agg(n=("ret", "size"), roi=("ret", lambda s: (s.mean() + 100.0) / 100.0),
                               mean_pred=("pred", "mean"), mean_odds=("odds", "mean")).reset_index()
    corr = float(np.corrcoef(d["pred"], d["ret"])[0, 1])
    # ---- policies
    stake_all = np.full(len(d), 100.0); payout_all = (d["ret"].to_numpy() + 100.0)
    arr_like = {"_day_idx": pd.factorize(d["race_date"])[0], "_year": d["year"].to_numpy()}
    policies = {}
    for tau in TAUS:
        for cap in (None, 21.0):
            m = d["pred"].to_numpy() > tau
            if cap is not None:
                m &= d["odds"].to_numpy() < cap
            name = f"pred>{tau:g}" + (f"&odds<{cap:g}" if cap else "")
            rec = {}
            for w, (a, b) in (("D", (args.start_year, 2007)), ("Q", (2008, 2018)), ("C", (2019, 2026))):
                sel = np.flatnonzero(m & (d["year"].to_numpy() >= a) & (d["year"].to_numpy() <= b))
                if len(sel) == 0:
                    rec[w] = {"n_bets": 0}; continue
                po = payout_all[sel]
                yrs = d["year"].to_numpy()[sel]
                yearly = {int(yy): round(float(po[yrs == yy].mean() / 100.0), 3) for yy in np.unique(yrs)}
                rec[w] = {"n_bets": int(len(sel)), "n_hits": int((po > 0).sum()), "roi": float(po.mean() / 100.0),
                          "n_days": int(len(np.unique(arr_like["_day_idx"][sel]))), "yearly": yearly,
                          "years_ge1": int(sum(v >= 1.0 for v in yearly.values())), "years_n": len(yearly),
                          "max_hit_share": float(po.max() / po.sum()) if po.sum() > 0 else None}
                if w == "C" and len(sel) >= 50:
                    rec["C_boot"] = ev.day_bootstrap(stake_all, payout_all, sel, arr_like, b=args.boot)
            policies[name] = rec
    # anti-policy: bottom decile (signal sanity)
    m = d["dec"].to_numpy() == 0
    sel = np.flatnonzero(m)
    policies["bottom_decile(anti)"] = {"ALL": {"n_bets": int(len(sel)), "roi": float(payout_all[sel].mean() / 100.0)}}
    m = d["dec"].to_numpy() == 9
    sel = np.flatnonzero(m)
    policies["top_decile"] = {"ALL": {"n_bets": int(len(sel)), "roi": float(payout_all[sel].mean() / 100.0)}}
    top_imp = sorted(importances.items(), key=lambda t: -t[1])[:25]
    tot = sum(importances.values()) or 1.0
    out = {"tag": tag, "params": params, "rounds": args.rounds, "features": feats + cats, "dropped": sorted(drop),
           "synthetic_q": args.synthetic_q,
           "years_scored": [int(d.year.min()), int(d.year.max())], "n_scored_rows": int(len(d)),
           "corr_pred_ret": corr, "deciles": dec.to_dict(orient="records"), "policies": policies,
           "top_importance_share": [(k, round(v / tot, 4)) for k, v in top_imp],
           "elapsed_s": round(time.time() - t0, 1)}
    (out_dir / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=ev._default))
    d[["race_id", "horse_id", "race_date", "year", "odds", "ret", "pred"]].to_parquet(out_dir / "predictions.parquet", index=False)
    log(f"done → {out_dir}; corr={corr:.4f}; top decile roi={policies['top_decile']['ALL']['roi']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
