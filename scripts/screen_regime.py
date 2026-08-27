"""レジーム軸のオラクル上限を測る(feature 102 の kill-test)。

問い: 「行が**どのレジームで生まれたか**と**レジームで壊れた入力がどう欠けているか**を
完璧に使えたら、winner NLL がどれだけ下がるか」。

セルの切り方が結論を左右する
----------------------------
オラクルが与えるのは「**選んだセル表現の中での**上限」であって、任意の交互作用の数学的上限では
ない(codex 指摘)。レジーム・開催日・レース欠損率はいずれも**レース定数**なので、単独で切ると
レース内 softmax で主効果が消えて必ず「情報なし」になる。**馬単位のセル**で切らなければ
測ったことにならない。

3 軸を使う。すべて**ラベルを読まない**(active の永続化予測とレース属性だけ)。

  S  供給状態(馬単位): 過去走のテン3F が
       none          … 一度も取れていない
       derived_only  … 取れているのは 1200m(恒等式で導出できる距離)だけ
       has_feed      … 1200m 以外でも取れている = 旧供給の実測を含む
  B  active 予測の強さ(馬単位): レース内の p 順位を 1 / 2-3 / 4-6 / 7+ に事前固定
  V  非欠損時のテン3F 由来値の三分位(値の意味変化を捕まえる)

入れ子は S -> S×B -> +V×B。レジーム差は `post × S × B` にしか入りようがない
(`post` 単独はレース定数なので消える)。

判定は 2 段構え
---------------
1. **in-sample オラクル + 順列ヌル**: 従来の形。ただし K を増やすとヌルも大きく下がるので、
   K が大きい領域では検出力が消える(K=12 でヌルが −0.018)。
2. **分割オラクル(本命)**: レースを半分に割り、fit 側で係数を推定して **held-out 側**の改善を
   測る。過学習は out-of-sample では利得にならないのでヌルが 0 付近に落ち、K が大きくても
   検出力が残る。

    cd training && uv run python ../scripts/screen_regime.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from screen_axes import DB, MODEL_VERSION, Oracle  # noqa: E402

REGIME_FROM = "2025-07-01"
DERIVABLE_DISTANCE = 1200
RECENT_N = 5


def load() -> pd.DataFrame:
    eng = create_engine(DB)
    with eng.connect() as c:
        d = pd.read_sql(text("""
            SELECT pru.race_id, rp.horse_id, rp.win_prob AS p, r.race_date,
                   (rr.result_status='finished' AND rr.finish_order=1)::int AS is_win
            FROM prediction_runs pru
            JOIN race_predictions rp USING (prediction_run_id)
            JOIN races r ON r.race_id = pru.race_id
            JOIN race_horses rh ON rh.race_id=pru.race_id AND rh.horse_id=rp.horse_id
            LEFT JOIN race_results rr ON rr.race_id=pru.race_id AND rr.horse_id=rp.horse_id
            WHERE pru.model_version = :mv AND rh.entry_status='started'
        """), c, params={"mv": MODEL_VERSION})
        hist = pd.read_sql(text("""
            SELECT rh.horse_id, r.race_date, r.distance,
                   (rr.first_3f IS NOT NULL)::int AS has_f3, rr.first_3f
            FROM races r
            JOIN race_horses rh ON rh.race_id=r.race_id AND rh.entry_status='started'
            JOIN race_results rr ON rr.race_id=r.race_id AND rr.horse_id=rh.horse_id
            WHERE rr.result_status='finished'
        """), c)
    d["race_date"] = pd.to_datetime(d["race_date"])
    hist["race_date"] = pd.to_datetime(hist["race_date"])
    d = d[d.groupby("race_id")["is_win"].transform("sum") == 1].copy()

    hist = hist.sort_values(["horse_id", "race_date"], kind="stable")
    hist["feed"] = ((hist["has_f3"] == 1) & (hist["distance"] != DERIVABLE_DISTANCE)).astype(int)
    g = hist.groupby("horse_id", sort=False)
    hist["n_f3"] = g["has_f3"].transform(lambda s: s.shift(1).rolling(RECENT_N, min_periods=1).sum())
    hist["n_feed"] = g["feed"].transform(lambda s: s.shift(1).rolling(RECENT_N, min_periods=1).sum())
    hist["v_f3"] = g["first_3f"].transform(
        lambda s: s.shift(1).rolling(RECENT_N, min_periods=1).mean())

    d = pd.merge_asof(
        d.sort_values("race_date"),
        hist[["horse_id", "race_date", "n_f3", "n_feed", "v_f3"]].sort_values("race_date"),
        on="race_date", by="horse_id", direction="backward", allow_exact_matches=False,
    ).reset_index(drop=True)

    d["S"] = np.where(d["n_f3"].isna() | (d["n_f3"] == 0), "none",
                      np.where(d["n_feed"].fillna(0) > 0, "has_feed", "derived_only"))
    rank = d.groupby("race_id")["p"].rank(ascending=False, method="first")
    d["B"] = np.select([rank == 1, rank <= 3, rank <= 6], ["1st", "2-3", "4-6"], default="7+")
    d["post"] = np.where(d["race_date"] >= pd.Timestamp(REGIME_FROM), "post", "pre")
    return d


def v_tercile(d: pd.DataFrame) -> pd.Series:
    """非欠損馬だけ三分位。欠損は独立セル(欠損パターン由来の情報と混ぜない)。"""
    out = pd.Series("V=na", index=d.index, dtype=object)
    ok = d["v_f3"].notna()
    if ok.sum() > 3:
        out[ok] = "V=" + pd.qcut(d.loc[ok, "v_f3"], 3, labels=False,
                                 duplicates="drop").astype(str)
    return out


def _race_softmax_nll(b, rid, n_races, lp, iswin, codes):
    s = lp + b[codes]
    m = np.full(n_races, -np.inf)
    np.maximum.at(m, rid, s)
    e = np.exp(s - m[rid])
    den = np.zeros(n_races)
    np.add.at(den, rid, e)
    sm = e / den[rid]
    return -np.sum(np.log(sm[iswin])) / float(n_races), sm


def split_oracle(d: pd.DataFrame, cells, *, nested=None, seed: int = 0, n_null: int = 8):
    """レースを半分に割り、fit 側で係数を推定して **held-out 側**の改善を測る。

    in-sample オラクルは K を増やすほど順列ヌルも下がり、K が大きい領域で検出力が消える。
    held-out なら過学習が利得にならないのでヌルが 0 付近に落ち、検出力が保たれる。
    """
    races = d["race_id"].to_numpy()
    uniq = np.unique(races)
    fit_set = set(np.random.default_rng(seed).choice(
        uniq, size=len(uniq) // 2, replace=False).tolist())
    in_fit = np.array([r in fit_set for r in races])
    p_log = np.log(d["p"].to_numpy())
    win = d["is_win"].to_numpy() == 1

    def _part(mask, codes):
        rid, _ = pd.factorize(races[mask])
        return rid, int(rid.max()) + 1, p_log[mask], win[mask], codes[mask]

    def _fit_eval(cell_series):
        codes, uniq_c = pd.factorize(pd.Series(cell_series).astype(str))
        codes, K = codes.astype(np.intp), len(uniq_c)
        rid, nR, lp, iw, cd = _part(in_fit, codes)

        def fg(b):
            nll, sm = _race_softmax_nll(b, rid, nR, lp, iw, cd)
            gw = np.zeros(K)
            np.add.at(gw, cd[iw], 1.0)
            ge = np.zeros(K)
            np.add.at(ge, cd, sm)
            return nll, -(gw - ge) / float(nR)

        r = minimize(fg, np.zeros(K), jac=True, method="L-BFGS-B",
                     options={"maxiter": 2000, "ftol": 1e-14})
        h = _part(~in_fit, codes)
        base, _ = _race_softmax_nll(np.zeros(K), *h)
        got, _ = _race_softmax_nll(r.x, *h)
        return got - base, K

    cells = pd.Series(cells).astype(str).reset_index(drop=True)
    nst = None if nested is None else pd.Series(nested).astype(str).reset_index(drop=True)
    ref = 0.0
    if nst is not None:
        ref, _ = _fit_eval(nst)
    real, K = _fit_eval(cells if nst is None else nst + "|" + cells)
    real -= ref
    nulls = []
    for i in range(n_null):
        perm = cells.to_numpy().copy()
        np.random.default_rng(1000 + i).shuffle(perm)
        v, _ = _fit_eval(pd.Series(perm) if nst is None else nst + "|" + pd.Series(perm))
        nulls.append(v - ref)
    return real, float(np.min(nulls)), K


def main() -> None:
    d = load()
    print(f"rows={len(d)} races={d['race_id'].nunique()} "
          f"{d['race_date'].min().date()}..{d['race_date'].max().date()}")
    print("S の分布:", d["S"].value_counts().to_dict())
    print("post 比率:", round((d["post"] == "post").mean() * 100, 1), "%\n")

    S, B, post = d["S"], d["B"], d["post"]
    V = v_tercile(d)
    cases = [
        ("C1 レジーム指示単独(レース定数)", post, None),
        ("N1 S 単独", S, None),
        ("N2 S×B の増分", S + "|" + B, B),
        ("N3 +V×B の増分", V + "|" + B, S + "|" + B),
        ("P1 post×S×B の増分", post + "|" + S + "|" + B, S + "|" + B),
        ("B0 B 単独(対照)", B, None),
    ]

    o = Oracle(d)
    print(f"baseline winner NLL = {o.base:.6f}")
    print("\n=== in-sample オラクル(参考・K が大きいと検出力が消える)===")
    for name, cel, nst in cases:
        o.screen(name, cel, nested=nst)

    print("\n=== 分割オラクル(本命・過学習 envelope なし)===")
    print("  レースを半分に割り、fit 側で係数を推定して held-out 側の改善を測る。負が改善。")
    for name, cel, nst in cases:
        real, nullmin, K = split_oracle(d, cel, nested=nst)
        # **改善している(負)ことが先**。real も null も正のとき「real < null」で
        # 「情報あり」と言うのは無意味 — どちらも baseline より悪いのだから。
        info = real < 0.0 and real < nullmin
        print(f"  {name:30s} K={K:3d} held-outΔ={real:+.6f} "
              f"ヌル最良={nullmin:+.6f} → {'情報あり' if info else '情報なし'}")


if __name__ == "__main__":
    main()
