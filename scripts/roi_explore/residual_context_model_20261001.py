"""文脈の学習: 手で条件を列挙する代わりに、候補馬(市場連動モデル ER>1.0)の残差を文脈特徴から学習し、
walk-forward で 1 本だけ測る(2026-10-01・事前登録)。

  - 候補行 = market_er > 1.0(別セッションの凍結 refit 予測)・2010 年以降
  - 2 段目 = LightGBM 二値(won)、init_score = logit(p̂)(p̂ = market_er / odds)。学習するのは文脈による対数オッズ補正 f(x)。
    EV2 = sigmoid(logit p̂ + f(x)) × odds
  - 特徴 = 別セッションの 20 文脈列 + 基本列(オッズ・人気・頭数・間隔・キャリア・前走・騎手/調教師勝率・条件カテゴリ)
  - 年 y は 2010..y−1 の候補行で学習(y=2013..2026)。ハイパーパラメータ固定・チューニングなし。
  - 政策(事前固定): 主 = EV2>1.2 均等。副 = EV2>1.1 / EV2>1.3 / 年内 EV2 上位 10%。
  - 対照: (a) 文脈列を除いた基本列のみの 2 段目 (b) 文脈列を年内で行シャッフルした 2 段目(配管が ROI を捏造しないこと)
  - 窓: D 2013–2015 / Q 2016–2018 / C 2019–2026。主検定 = C の帰無中心化 day-cluster bootstrap 片側 p(1 本)。

    cd training && uv run python ../scripts/roi_explore/residual_context_model_20261001.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = pathlib.Path("/Users/kuwatawaku/workspace/horseracing")
sys.path.insert(0, str(ROOT / "scripts" / "roi_explore"))
import evaluate as evx  # noqa: E402  (day_bootstrap / centered_pvalue)
import explore_new_patterns_20260930 as ev  # noqa: E402
import new_pattern_features_20260930 as features  # noqa: E402

OUT = ROOT / "artifacts" / "roi_new_patterns_20260930" / "residual_model_20261001"
BASE_NUM = ["odds", "market_er", "popularity", "field_size", "days_since_last", "career_starts", "age", "prev_finish",
            "prev2_finish", "avg_last3_finish", "prev_popularity", "prev_odds", "dist_change", "class_change",
            "jockey_win_rate_365", "trainer_win_rate_365", "month", "race_number", "prize_money", "q_entropy_norm",
            "career_win_rate", "jockey_weight", "horse_number", "frame"]
BASE_CAT = ["track_type", "dist_band", "race_class_canon", "venue_code", "prev_running_style", "sex"]
CTX = list(features.NEW_COLUMNS)  # 20 columns (+prev_gap/prev2_gap)
PARAMS = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 500, "feature_fraction": 0.8,
          "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 10.0, "verbose": -1, "num_threads": 8, "seed": 1}
ROUNDS = 200
WIN = {"D": (2013, 2015), "Q": (2016, 2018), "C": (2019, 2026)}
POLICIES = {"ev2>1.2": 1.2, "ev2>1.1": 1.1, "ev2>1.3": 1.3}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load() -> pd.DataFrame:
    cols = (set(BASE_NUM + BASE_CAT) - {"market_er"}) | set(features.REQUIRED_COLUMNS) | {"race_id", "horse_id", "race_date", "year", "odds", "won", "race_ok", "dead_heat"}
    raw = pd.read_parquet(ev.ROWS, columns=sorted(cols))
    d = features.build_features(raw); del raw
    pred = pd.read_parquet(ev.PRED, columns=["race_id", "horse_id", "pred"])
    d = d.merge(pred, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    d = d.loc[d.race_ok & ~d.dead_heat].sort_values(["race_date", "race_id", "horse_id"]).reset_index(drop=True)
    d["ret"] = np.where(d.won, d.odds * 100, 0) - 100
    d["market_er"] = 1 + d.pred / 100
    return d


def design(d: pd.DataFrame, use_ctx: bool, shuffle_ctx_seed: int | None) -> tuple[np.ndarray, list[str], list[int]]:
    num = BASE_NUM + (CTX if use_ctx else [])
    X = d[num].astype(float).copy()
    if use_ctx and shuffle_ctx_seed is not None:
        rng = np.random.default_rng(shuffle_ctx_seed)
        for y in d["year"].unique():
            idx = np.flatnonzero(d["year"].to_numpy() == y)
            perm = rng.permutation(idx)
            X.loc[idx, CTX] = X.loc[perm, CTX].to_numpy()
    cats = []
    for c in BASE_CAT:
        codes, _ = pd.factorize(d[c].astype(object).where(d[c].notna(), None))
        X[c] = codes.astype(float); X.loc[codes < 0, c] = np.nan; cats.append(c)
    feats = list(X.columns)
    return X.to_numpy(np.float32), feats, [feats.index(c) for c in cats]


def walk_forward(d: pd.DataFrame, X: np.ndarray, cat_idx: list[int]) -> np.ndarray:
    years = d["year"].to_numpy(); y = d["won"].to_numpy(float)
    p_hat = np.clip(d["market_er"].to_numpy(float) / d["odds"].to_numpy(float), 1e-5, 1 - 1e-5)
    offset = np.log(p_hat / (1 - p_hat))
    raw = np.full(len(d), np.nan)
    for yv in range(2013, 2027):
        tr = (years >= 2010) & (years < yv); te = years == yv
        if te.sum() == 0:
            continue
        ds = lgb.Dataset(X[tr], y[tr], init_score=offset[tr], categorical_feature=cat_idx, free_raw_data=True)
        bst = lgb.train(PARAMS, ds, num_boost_round=ROUNDS)
        raw[te] = bst.predict(X[te], raw_score=True)
    p2 = 1 / (1 + np.exp(-(offset + raw)))
    return p2 * d["odds"].to_numpy(float)  # EV2 (NaN before 2013)


def score(d: pd.DataFrame, ev2: np.ndarray, tag: str, b: int) -> dict:
    years = d["year"].to_numpy(); odds = d["odds"].to_numpy(float); pay = d["ret"].to_numpy(float) + 100.0
    day_idx = pd.factorize(d["race_date"])[0]
    out = {}
    pol = dict(POLICIES)
    # top decile within year among candidates
    ev2s = pd.Series(ev2)
    thr_dec = ev2s.groupby(years).transform(lambda s: s.quantile(0.9)).to_numpy()
    masks = {k: np.nan_to_num(ev2, nan=-1) > t for k, t in pol.items()}
    masks["ev2_top10%"] = np.nan_to_num(ev2, nan=-1) >= thr_dec
    masks["base_er>1.2"] = d["market_er"].to_numpy() > 1.2  # reference: 1-stage rule on same rows
    for k, m in masks.items():
        rec = {}
        for w, (lo, hi) in WIN.items():
            sel = np.flatnonzero(m & (years >= lo) & (years <= hi))
            if len(sel) == 0:
                rec[w] = {"n": 0}; continue
            yrs = years[sel]
            rec[w] = {"n": int(len(sel)), "hits": int((pay[sel] > 0).sum()), "roi": float(pay[sel].sum() / (100.0 * len(sel))),
                      "yearly": {int(yy): round(float(pay[sel][yrs == yy].mean() / 100.0), 3) for yy in np.unique(yrs)}}
            if w == "C":
                stake = np.full(len(d), 100.0); payout = pay
                bt = evx.day_bootstrap(stake, payout, sel, {"_day_idx": day_idx}, b=b)
                ds_, dp_ = evx.day_aggregates(stake, payout, sel, day_idx)
                rec[w]["ci"] = [bt["ci_lo"], bt["ci_hi"]]; rec[w]["p_one_sided"] = evx.centered_pvalue(ds_, dp_, b=b)["p_one_sided"]
        out[k] = rec
        print(f"  [{tag}] {k:12s} " + " | ".join(f"{w} {rec[w].get('roi', float('nan')):.3f} (n={rec[w].get('n', 0)})" for w in WIN)
              + (f"  C ci=[{rec['C']['ci'][0]:.3f},{rec['C']['ci'][1]:.3f}] p={rec['C']['p_one_sided']:.3f}" if "ci" in rec.get("C", {}) else ""), flush=True)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    d = load()
    cand = d.loc[(d["market_er"] > 1.0) & (d["year"] >= 2010)].reset_index(drop=True)
    log(f"candidates {len(cand):,} rows / years {cand.year.min()}..{cand.year.max()} / base ER>1.0 ROI {(cand.ret.mean() + 100) / 100:.3f}")
    results = {}
    for tag, use_ctx, shuf in (("with_context", True, None), ("base_only", False, None), ("shuffled_context", True, 20261001)):
        X, feats, cat_idx = design(cand, use_ctx, shuf)
        ev2 = walk_forward(cand, X, cat_idx)
        log(f"{tag}: walk-forward done ({time.time() - t0:.0f}s); scoring")
        results[tag] = score(cand, ev2, tag, b=10000)
        if tag == "with_context":
            # importance of context features in the last model (diagnostic)
            years = cand["year"].to_numpy(); y = cand["won"].to_numpy(float)
            p_hat = np.clip(cand["market_er"].to_numpy(float) / cand["odds"].to_numpy(float), 1e-5, 1 - 1e-5)
            off = np.log(p_hat / (1 - p_hat)); tr = years < 2026
            bst = lgb.train(PARAMS, lgb.Dataset(X[tr], y[tr], init_score=off[tr], categorical_feature=cat_idx), num_boost_round=ROUNDS)
            imp = dict(zip(feats, bst.feature_importance("gain")))
            tot = sum(imp.values()) or 1.0
            results["importance_gain_share_2026"] = {k: round(v / tot, 4) for k, v in sorted(imp.items(), key=lambda t: -t[1])[:25]}
            results["context_gain_share"] = round(sum(imp.get(c, 0) for c in CTX) / tot, 4)
    results["config"] = {"params": PARAMS, "rounds": ROUNDS, "windows": WIN, "policies": POLICIES, "n_candidates": int(len(cand)),
                         "features_base": BASE_NUM + BASE_CAT, "features_context": CTX}
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=float))
    log(f"done {time.time() - t0:.0f}s → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
