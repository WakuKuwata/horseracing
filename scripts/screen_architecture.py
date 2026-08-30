"""学習アーキ 3 レバーの kill-test スクリーニング(凍結 OOF 予測の上の分割オラクル)。

対象レバー
----------
A. 騎手・調教師の周辺 OOF-TE を **PL 尤度上の残差効果 s_i = f(x_i) + b_j + b_t** に置き換える
   (**加法**であって交差ではない)
B. **race-simplex 校正**: 現行 arm E(馬ごとの単調写像 → Σ=1 再正規化)の**後段に**
   レース単位の温度 τ(z_r) を足す。τ≡1 で現行を厳密に包含する形で測る
C. **PL の IIA 緩和**: スコアベクトルからは復元できない出走馬構成に依存する項があるか

測り方と、実行前に固定した選択
------------------------------
本番忠実な arm E の held-out OOF 予測を凍結し、`log p_i` を offset に固定して設計行列 X の
係数を winner NLL で推定し、held-out 側で評価する。

**offset は校正後 p を使う(事前登録)。** 測っているのは「arm E を置き換えた場合」ではなく
「**arm E の後段に足した場合の増分**」。raw と校正後の両方を試して良い方を採るのは選択リーク。
この選択は codex の設計勧告(arm E を壊さず τ(z) を後段に足す)と一致させたもの。

**分割は開催日ブロックの walk-forward(前半で推定 → 後半で評価)。**
レース単位のランダム半割は (a) に不適切だった: 同一開催日のレースが両側に入り、
**未来の騎乗成績で過去のレースを予測する**ので本番の strict-past より楽観的になる。

**バケット境界は fit 側だけで決める。** 全期間から決めると(ラベル非依存でも)未来の分布を使う。

**ヌルは条件付きパラメトリック**: 各レースの勝者を `Y_r ~ Categorical(p_r)` から引き直し、
ridge 選択まで含む手続き全体を反復する。セル割当のシャッフルはこの offset モデルの下での
正しい帰無ではない。

読み方(codex レビューを反映して弱めた)
--------------------------------------
- **正の held-out 改善** = そのセル族に残差構造がある強い証拠
- **ゼロ結果**: (b) と、条件を揃えた (a) には比較的強い。**(c) 全体に対しては弱い証拠**
  (現在のセルは相手構成の低次元射影にすぎない)
- (a) の負結果が否定できるのは「現行 TE 込み予測に残る、期間内で安定した加法的 ID 切片」まで。
  **TE 置換全体・時変効果・他特徴との交互作用は kill できない**。fit 側が本番の履歴長より
  はるかに短い(数千レース vs 2007 年以降)ので、希少 ID の効果は構造的に過小評価される
- (b) と (c) は**識別不能**。race-conditional な校正はそれ自体が IIA を緩めている。
  そこで (b) は**スコアベクトルから復元できる文脈だけ**、(c) は**復元できない相手構成**に
  限定し、**両方の入れ子順序**を測って共有成分を出す

**SCREENING ONLY — can_adopt=false。** この窓でレバー選択まで行うので、最終採用判定には
別の期間が要る。

    cd training && uv run python ../scripts/screen_architecture.py
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.optimize import minimize
from sqlalchemy import create_engine, text

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"

RIDGE_GRID = (0.0, 1.0, 10.0, 100.0, 1000.0)
MIN_RIDES = 30
N_NULL = 6


# --------------------------------------------------------------------------- data
def load(dump: str, feat: str) -> pd.DataFrame:
    d = pd.read_parquet(dump)
    d = d[d["eligible"] == 1].copy()
    with create_engine(DB).connect() as c:
        ent = pd.read_sql(text("SELECT race_id, horse_id, jockey_id, trainer_id "
                               "FROM race_horses WHERE entry_status='started'"), c)
    d = d.merge(ent, on=["race_id", "horse_id"], how="left")
    d = d.merge(pd.read_parquet(feat, columns=["race_id", "horse_id",
                                               "field_front_rate_ex_self"]),
                on=["race_id", "horse_id"], how="left")
    n_zero = int((d["p"] <= 0).sum())
    if n_zero:
        raise RuntimeError(f"p<=0 が {n_zero} 行。log offset では有限の係数で復活できない")
    d["lp"] = np.log(d["p"])
    d["lp_centered"] = d["lp"] - d.groupby("race_id")["lp"].transform("mean")
    plogp = -d["p"] * np.log(d["p"])
    d["race_entropy"] = d["race_id"].map(
        plogp.groupby(d["race_id"]).sum() / np.log(d.groupby("race_id")["p"].size()))
    p1 = d.groupby("race_id")["p"].max()
    p2 = d.groupby("race_id")["p"].apply(lambda s: s.nlargest(2).iloc[-1] if len(s) > 1 else 0.0)
    d["top_gap"] = d["race_id"].map(np.log(p1 / p2.clip(lower=1e-12)))
    return d.sort_values(["race_date", "race_id"], kind="stable").reset_index(drop=True)


def walk_forward_masks(d: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """開催日ブロックの walk-forward。fit=前半の開催日 / held-out=後半。

    inner(ridge 選択用)は fit のさらに前半 70%。
    """
    days = np.sort(d["race_date"].unique())
    cut, icut = days[len(days) // 2], days[int(len(days) * 0.5 * 0.7)]
    rd = d["race_date"].to_numpy()
    return rd < cut, rd < icut


def fit_side_buckets(d: pd.DataFrame, in_fit: np.ndarray, col: str, k: int,
                     per_race: bool) -> pd.Series:
    """境界を **fit 側だけ**で決めて全体に当てる。per_race=True はレース単位で分位を取る。"""
    src = d.loc[in_fit]
    vals = src.groupby("race_id")[col].first() if per_race else src[col]
    edges = np.unique(np.nanquantile(vals.dropna(), np.linspace(0, 1, k + 1)[1:-1]))
    x = d.groupby("race_id")[col].transform("first") if per_race else d[col]
    lab = pd.Series(np.digitize(x.to_numpy(), edges), index=d.index).astype(str)
    return lab.where(x.notna(), "na")


# ------------------------------------------------------------------- design blocks
def onehot(labels: pd.Series) -> sp.csr_matrix:
    codes, uniq = pd.factorize(labels.astype(str), use_na_sentinel=False)
    return sp.csr_matrix((np.ones(len(codes)), (np.arange(len(codes)), codes)),
                         shape=(len(codes), len(uniq)))


def slope_by(labels: pd.Series, x: np.ndarray) -> sp.csr_matrix:
    """文脈バケットごとに連続量 x の傾きを持たせる = レース単位の温度 τ(z_r)。"""
    codes, uniq = pd.factorize(labels.astype(str), use_na_sentinel=False)
    return sp.csr_matrix((x, (np.arange(len(codes)), codes)), shape=(len(codes), len(uniq)))


def hstack(*b) -> sp.csr_matrix:
    return sp.hstack([x for x in b if x is not None], format="csr")


# ------------------------------------------------------------------- split oracle
def _nll_grad(beta, X, rid, nR, lp, iw, lam):
    s = lp + X @ beta
    m = np.full(nR, -np.inf)
    np.maximum.at(m, rid, s)
    e = np.exp(s - m[rid])
    den = np.zeros(nR)
    np.add.at(den, rid, e)
    sm = e / den[rid]
    nll = -np.sum(np.log(np.maximum(sm[iw], 1e-300))) / nR
    g = -(np.asarray(X[iw].sum(axis=0)).ravel() - X.T @ sm) / nR
    return nll + 0.5 * lam * float(beta @ beta) / nR, g + lam * beta / nR


def _run(X, races, in_fit, in_inner, lp_all, win):
    K = X.shape[1]

    def part(mask):
        rid, _ = pd.factorize(races[mask])
        return X[mask], rid, int(rid.max()) + 1, lp_all[mask], win[mask]

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
    return (_nll_grad(r.x, *h, 0.0)[0] - _nll_grad(np.zeros(K), *h, 0.0)[0]), best_lam


def draw_null_winner(d: pd.DataFrame, rng) -> np.ndarray:
    """条件付き帰無: 各レースの勝者を Categorical(p_r) から引き直す(offset モデルが真)。"""
    win = np.zeros(len(d), dtype=bool)
    for _, idx in d.groupby("race_id", sort=False).indices.items():
        pr = d["p"].to_numpy()[idx]
        win[idx[rng.choice(len(idx), p=pr / pr.sum())]] = True
    return win


def oracle(d, in_fit, in_inner, X_full, X_base=None, n_null=N_NULL):
    races, lp_all = d["race_id"].to_numpy(), d["lp"].to_numpy()
    win = d["is_win"].to_numpy() == 1

    def inc(w):
        ref = _run(X_base, races, in_fit, in_inner, lp_all, w)[0] if X_base is not None else 0.0
        got, lam = _run(X_full, races, in_fit, in_inner, lp_all, w)
        return got - ref, lam

    real, lam = inc(win)
    nulls = [inc(draw_null_winner(d, np.random.default_rng(9000 + i)))[0]
             for i in range(n_null)]
    return real, float(np.min(nulls)), float(np.median(nulls)), X_full.shape[1], lam


def verdict(real: float, null_min: float) -> str:
    if real >= null_min:
        return "情報なし"
    if real <= -0.003:
        return "**生存**"
    return "曖昧"


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="../out/arme_oof.parquet")
    ap.add_argument("--features", default="../artifacts/features.parquet")
    ap.add_argument("--json", dest="json_out", default="../out/screen_architecture.json")
    args = ap.parse_args()

    print("*** SCREENING ONLY — can_adopt=false ***")
    print("offset=校正後 p(arm E の後段に足した増分)/ 分割=開催日 walk-forward /")
    print("バケット境界=fit 側のみ / ヌル=条件付きパラメトリック Y~Categorical(p)\n")

    d = load(args.dump, args.features)
    in_fit, in_inner = walk_forward_masks(d)
    print(f"rows={len(d):,}  races={d['race_id'].nunique():,}  "
          f"fit={d.loc[in_fit,'race_id'].nunique():,} races "
          f"({d.loc[in_fit,'race_date'].min()}..{d.loc[in_fit,'race_date'].max()})  "
          f"held-out={d.loc[~in_fit,'race_id'].nunique():,} races")

    def collapse(col):
        n = d.loc[in_fit, col].value_counts()
        return d[col].where(d[col].isin(set(n[n >= MIN_RIDES].index)), "other")

    d["jk"], d["tr"] = collapse("jockey_id"), collapse("trainer_id")
    d["fs"] = pd.cut(d["n_started"], [0, 9, 13, 99], labels=["<=9", "10-13", "14+"]).astype(str)
    d["en"] = fit_side_buckets(d, in_fit, "race_entropy", 3, per_race=True)
    d["gp"] = fit_side_buckets(d, in_fit, "top_gap", 3, per_race=True)
    d["cm"] = fit_side_buckets(d, in_fit, "field_front_rate_ex_self", 3, per_race=False)
    print(f"  jockey {d['jk'].nunique():,} / trainer {d['tr'].nunique():,} "
          f"(fit 側 min_rides={MIN_RIDES} で畳んだ)\n")

    x = d["lp_centered"].to_numpy()
    JK, TR = onehot(d["jk"]), onehot(d["tr"])
    G = sp.csr_matrix(x.reshape(-1, 1))                       # 全体温度
    B_ctx = hstack(slope_by(d["fs"], x), slope_by(d["en"], x), slope_by(d["gp"], x))
    C_ctx = slope_by(d["cm"], x)                              # スコアから復元できない相手構成
    res = []

    def row(name, X, base=None):
        r, nmin, nmed, K, lam = oracle(d, in_fit, in_inner, X, base)
        res.append({"axis": name, "real": r, "null_min": nmin, "null_median": nmed,
                    "K": K, "ridge": lam})
        print(f"  {name:<34} Δ={r:+.6f}  ヌル最小={nmin:+.6f} 中央={nmed:+.6f}  "
              f"K={K}  → {verdict(r, nmin)}")

    print("=== A. 騎手・調教師の残差効果(加法・現行 OOF-TE の上に残っているもの) ===")
    row("A1 騎手", JK)
    row("A2 調教師", TR)
    row("A3 調教師 (A1 の上・加法)", hstack(JK, TR), JK)

    print("\n=== B/C. レース内幾何 — 順序 A→B→C(B に有利) ===")
    row("B  温度×スコア文脈 (全体温度の上)", hstack(G, B_ctx), G)
    row("C  +相手構成 (B の上)", hstack(G, B_ctx, C_ctx), hstack(G, B_ctx))

    print("\n=== B/C. レース内幾何 — 順序 A→C→B(C に有利。差が共有成分) ===")
    row("C' 相手構成 (全体温度の上)", hstack(G, C_ctx), G)
    row("B' +スコア文脈 (C' の上)", hstack(G, C_ctx, B_ctx), hstack(G, C_ctx))

    print("\n=== 参考: 全体温度そのもの(自由度 1) ===")
    row("B0 全体温度", G)

    with open(args.json_out, "w") as fh:
        json.dump({"screening_only": True, "can_adopt": False, "dump": args.dump,
                   "offset": "calibrated_p", "split": "race_day_walk_forward",
                   "null": "conditional_parametric", "min_rides": MIN_RIDES,
                   "ridge_grid": list(RIDGE_GRID), "results": res}, fh, indent=2)
    print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
