"""乖離の条件付き検定(Stage 1): q を知った後、p は追加情報を持つか。

問い(codex 3 レンズ検証 2026-08-31 の帰結)
------------------------------------------
正しい帰無は「p の LogLoss が悪い」ではなく **P(Y|q,p,Z) = P(Y|q,Z)**。
既存のブレンド a≈0.0085 は**単一のグローバル係数**なので、広域的・単調な追加信号しか
否定していない。局所的・非線形・符号反転する追加情報(乖離 d = log(p/q) の関数)は
一度も測られていない(103 にも「乖離の有用性は未実測」と明記)。

測り方
------
offset = log q(レース内 devig 正規化した市場確率)を固定し、乖離 d の関数 g を
レース内 softmax の winner NLL で推定。**開催日 walk-forward**(前半 fit / 後半評価)、
ridge は fit 内側 70% で選択。ヌルは条件付きパラメトリック Y ~ Categorical(q)。
p は本番忠実 arm E の walk-forward OOF(26,410 レース / 2019-2026・feature 104 の副産物)。

設計(事前登録・fit 側分位で切る)
--------------------------------
  G  d のグローバル傾き(ブレンド a の再現 = 配線の妥当性アンカー)
  N  d の五分位ごとの傾き(非線形 g(d))          … G の上
  Q  q 帯(047 の 0.05/0.15 境界)× d の傾き        … G+N の上
  S  d の符号別の傾き(モデル強気/弱気の非対称)     … G+N の上

判定規則(実行前に固定)
----------------------
利益水準の換算: ROI=1.0 に必要な ΔR² ≈ 0.018(Benter 整合)。pseudo-R² の分母
L_uniform ≈ E[ln(頭数)] ≈ 2.6 なので **利益水準 = ΔNLL ≈ 0.047**。
参考: exotic プール情報(有意だが利益の 1/10)= ΔNLL ≈ 0.0044。

  held-out Δ >= ヌル最小            → 条件付き情報なし。**軸 C を閉じる**
                                       (政策学習 Stage 3 は前提を失い死ぬ)
  有意だが |Δ| < 0.0044             → 情報はあるが exotic プール未満。実質閉じ
  0.0044 <= |Δ| < 0.047             → exotic プール級。単独では利益にならないが報告
  |Δ| >= 0.047                      → 利益水準(事前確率は極めて低い)

**SCREENING ONLY — can_adopt=false。** 2019-2026 窓は 104 の verdict で使用済みなので、
ここで何が出ても最終判定には別窓が要る。

    cd training && uv run python ../scripts/screen_divergence.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.optimize import minimize
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from screen_architecture import RIDGE_GRID, _nll_grad, slope_by  # noqa: E402

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
PROFIT_GRADE = 0.047
EXOTIC_GRADE = 0.0044
N_NULL = 6


def load(dump: str) -> pd.DataFrame:
    d = pd.read_parquet(dump)
    with create_engine(DB).connect() as c:
        o = pd.read_sql(text("select race_id, horse_id, odds, "
                             "(rr.result_status='finished' and rr.finish_order=1)::int as is_win "
                             "from race_horses rh "
                             "left join race_results rr using(race_id, horse_id) "
                             "where rh.entry_status='started'"), c)
    d = d.merge(o, on=["race_id", "horse_id"], how="left")
    # 勝者がちょうど 1 頭のレースのみ(winner NLL の定義)
    d = d[d.groupby("race_id")["is_win"].transform("sum") == 1].copy()
    inv = 1.0 / d["odds"]
    d["q"] = inv / inv.groupby(d["race_id"]).transform("sum")     # devig 正規化
    d["lq"] = np.log(d["q"].clip(lower=1e-12))
    d["lp"] = np.log(d["win"].clip(lower=1e-12))
    dd = d["lp"] - d["lq"]
    d["d"] = dd - dd.groupby(d["race_id"]).transform("mean")      # レース内センタリング
    return d.sort_values(["race_date", "race_id"], kind="stable").reset_index(drop=True)


def masks(d: pd.DataFrame):
    days = np.sort(d["race_date"].unique())
    rd = d["race_date"].to_numpy()
    return rd < days[len(days) // 2], rd < days[int(len(days) * 0.5 * 0.7)]


def fit_quantiles(x: pd.Series, in_fit: np.ndarray, k: int) -> pd.Series:
    edges = np.nanquantile(x[in_fit], np.linspace(0, 1, k + 1)[1:-1])
    return pd.Series(np.digitize(x.to_numpy(), np.unique(edges)), index=x.index).astype(str)


def run_oracle(d, X_full, X_base, in_fit, in_inner, *, lq, n_null=N_NULL):
    races = d["race_id"].to_numpy()
    win = d["is_win"].to_numpy() == 1

    def one(w, X):
        def part(mask):
            rid, _ = pd.factorize(races[mask])
            return X[mask], rid, int(rid.max()) + 1, lq[mask], w[mask]
        K = X.shape[1]
        best, best_lam = np.inf, RIDGE_GRID[0]
        for lam in RIDGE_GRID:
            r = minimize(_nll_grad, np.zeros(K), args=(*part(in_inner), lam), jac=True,
                         method="L-BFGS-B", options={"maxiter": 1500, "ftol": 1e-13})
            v = _nll_grad(r.x, *part(in_fit & ~in_inner), 0.0)[0]
            if v < best:
                best, best_lam = v, lam
        r = minimize(_nll_grad, np.zeros(K), args=(*part(in_fit), best_lam), jac=True,
                     method="L-BFGS-B", options={"maxiter": 1500, "ftol": 1e-13})
        h = part(~in_fit)
        return _nll_grad(r.x, *h, 0.0)[0] - _nll_grad(np.zeros(K), *h, 0.0)[0]

    def inc(w):
        ref = one(w, X_base) if X_base is not None else 0.0
        return one(w, X_full) - ref

    real = inc(win)
    nulls = []
    for i in range(n_null):
        rng = np.random.default_rng(11000 + i)
        wn = np.zeros(len(d), dtype=bool)
        for _, idx in d.groupby("race_id", sort=False).indices.items():
            q = d["q"].to_numpy()[idx]
            wn[idx[rng.choice(len(idx), p=q / q.sum())]] = True
        nulls.append(inc(wn))
    return real, float(np.min(nulls)), float(np.median(nulls)), X_full.shape[1]


def grade(delta: float, null_min: float) -> str:
    if delta >= null_min:
        return "情報なし(軸を閉じる)"
    if abs(delta) < EXOTIC_GRADE:
        return "exotic プール未満(実質閉じ)"
    if abs(delta) < PROFIT_GRADE:
        return "exotic プール級(利益未満)"
    return "**利益水準**"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="../out/104_active.parquet")
    ap.add_argument("--json", dest="json_out", default="../out/screen_divergence.json")
    args = ap.parse_args()

    print("*** SCREENING ONLY — can_adopt=false ***")
    print(f"利益水準 = ΔNLL {PROFIT_GRADE} / exotic プール級 = {EXOTIC_GRADE}\n")

    d = load(args.dump)
    in_fit, in_inner = masks(d)
    lq = d["lq"].to_numpy()
    print(f"races={d['race_id'].nunique():,} rows={len(d):,}  "
          f"fit={d.loc[in_fit,'race_id'].nunique():,} "
          f"({d.loc[in_fit,'race_date'].min()}..{d.loc[in_fit,'race_date'].max()})  "
          f"held-out={d.loc[~in_fit,'race_id'].nunique():,}")
    print(f"  d の分布: sd={d['d'].std():.3f}  |d|>0.5 の行 {100*(d['d'].abs()>0.5).mean():.1f}%\n")

    x = d["d"].to_numpy()
    G = sp.csr_matrix(x.reshape(-1, 1))
    d["dq"] = fit_quantiles(d["d"], in_fit, 5)
    N = slope_by(d["dq"], x)
    qb = pd.cut(d["q"], [0, 0.05, 0.15, 1.0], labels=["long", "mid", "fav"]).astype(str)
    Q = slope_by(qb, x)
    S = slope_by(pd.Series(np.where(x > 0, "pos", "neg"), index=d.index), x)

    res = []
    print(f"{'軸':<28} {'Δ(held-out NLL)':>16} {'ヌル最小':>10} {'中央':>10}  判定")
    for name, Xf, Xb in (
        ("G  d のグローバル傾き", G, None),
        ("N  d 五分位の傾き (G の上)", sp.hstack([G, N], format="csr"), G),
        ("Q  q帯×d (G+N の上)", sp.hstack([G, N, Q], format="csr"),
         sp.hstack([G, N], format="csr")),
        ("S  符号別 (G+N の上)", sp.hstack([G, N, S], format="csr"),
         sp.hstack([G, N], format="csr")),
        ("ALL G+N+Q+S (市場のみの上)", sp.hstack([G, N, Q, S], format="csr"), None),
    ):
        r, nmin, nmed, K = run_oracle(d, Xf, Xb, in_fit, in_inner, lq=lq)
        res.append({"axis": name, "delta": r, "null_min": nmin, "null_median": nmed, "K": K})
        print(f"{name:<28} {r:>+16.6f} {nmin:>+10.6f} {nmed:>+10.6f}  {grade(r, nmin)}")

    # 直接の答え: q 帯 × d 五分位の実現勝率(モデルが市場より強気な馬は本当に勝つのか)
    print("\n=== 実現勝率の表(q帯 × d 五分位・held-out のみ・診断) ===")
    ho = d[~in_fit]
    tab = ho.groupby([qb[~in_fit], ho["dq"]], observed=True).agg(
        n=("is_win", "size"), win=("is_win", "mean"),
        q=("q", "mean"), p=("win", "count"))
    tab["t/q"] = ho.groupby([qb[~in_fit], ho["dq"]], observed=True)["is_win"].mean() / \
        ho.groupby([qb[~in_fit], ho["dq"]], observed=True)["q"].mean()
    print("  (t/q > 1.257 のセルだけが利益になりうる。d5=モデル最強気)")
    piv = tab["t/q"].unstack()
    print(piv.round(3).to_string())
    ns = tab["n"].unstack()
    profitable = [(str(i), str(c), float(piv.loc[i, c]), int(ns.loc[i, c]))
                  for i in piv.index for c in piv.columns
                  if pd.notna(piv.loc[i, c]) and piv.loc[i, c] > 1.257]
    print(f"\n  t/q > 1.257 のセル: {len(profitable)}")
    for row in profitable:
        print(f"    q帯={row[0]} d分位={row[1]}  t/q={row[2]:.3f}  n={row[3]:,}")

    with open(args.json_out, "w") as fh:
        json.dump({"screening_only": True, "can_adopt": False, "dump": args.dump,
                   "profit_grade_nll": PROFIT_GRADE, "exotic_grade_nll": EXOTIC_GRADE,
                   "results": res,
                   "tq_table": {f"{i}|{c}": (None if pd.isna(piv.loc[i, c])
                                             else round(float(piv.loc[i, c]), 4))
                                for i in piv.index for c in piv.columns},
                   "profitable_cells": profitable}, fh, indent=2)
    print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
