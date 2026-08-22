"""Kill-test: 過去走の市場支持(人気/支持率)を「そのレースの格(クラス)」で条件付けると増分があるか。

問い: past_market(058)/pm_core_strength(069) は過去走の人気順位・支持率 s=log(q·N) を
クラスを見ずに平均している。「G1 で 1 番人気」と「未勝利で 1 番人気」を同じ s として数える。
クラス文脈を付けた市場支持は、既存軸(支持の平均 × 過去走の格の平均 × 昇降級)の上で
まだ情報を持つか。

方法(memory class-weighted-form-redundant と同じ 3 点セット。scripts/screen_axes.py の Oracle 流用):
  (1) 単独オラクル上限 = 固定モデル p を offset にしたレース内 softmax にセルダミーを足し
      winner NLL を in-sample 最小化
  (2) 既存軸に対する入れ子 = 既存軸セルの上に候補を積んだ増分
  (3) 順列ヌル = 同じ自由度でラベルをシャッフルした過学習の床。実測Δが床(最良)を下回って初めて情報あり

候補軸(全て strictly-before の過去走のみ。今走のオッズ・結果は不使用):
  C1 同格以上での支持    = 過去走のうち class_rank >= 今走 のレースの s 平均(該当無しは別セル)
  C2 格下での支持        = 過去走のうち class_rank <  今走 のレースの s 平均
  C3 前走支持 × 前走格差 = s_last のビン × (今走 − 前走 class_rank) の 昇/同/降
  C4 直近5走支持 × 平均格差 = s_mean5 のビン × 直近5走の平均 class_rank − 今走 のビン
  C5 格調整支持          = 直近5走の mean(s + γ·(cls_past − cls_today)) を γ∈{0.5,1.0} で(粗い連続補正)

既存軸(入れ子の参照):
  ref1 = s_mean5 ビン × 直近5走の class_rank 平均ビン          (= 069 + 056 asof_prize_avg 相当)
  ref2 = ref1 × class_transition(今走 − 前走)の 昇/同/降           (= 020 class_transition 相当)

使い方:
    cd training && uv run python ../scripts/killtest_class_support.py [--model lgbm-058-acc] [--n-null 8]
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from screen_axes import DB, Oracle as _Oracle  # noqa: E402


class Oracle(_Oracle):
    """入れ子の順列ヌルを『参照セル内シャッフル』に変更した版。

    screen_axes の素朴な全体シャッフルは、候補が参照軸と共線なとき結合セル数が実データより
    膨らみ(実 K=131 vs ヌル K≫)、ヌルの床が実測より深く出て自由度が揃わない。参照セルの
    内側でだけ候補を並べ替えると、結合セルの集合とサイズが実データと完全に一致したまま
    候補↔結果の関連(参照で説明されない分)だけが壊れる=正しい条件付きヌル。"""

    def screen(self, name: str, cells, nested=None, n_null: int = 8, rng=None):
        rng = rng or np.random.default_rng(0)
        cells = pd.Series(cells).astype(str).reset_index(drop=True)
        if nested is None:
            return super().screen(name, cells, None, n_null, rng)
        nested = pd.Series(nested).astype(str).reset_index(drop=True)
        ref, base_k = self.fit(nested)
        full, K = self.fit(nested + "|" + cells)
        real = full - ref
        groups = nested.groupby(nested).indices
        null = []
        for _ in range(n_null):
            perm = cells.to_numpy().copy()
            for idx in groups.values():
                perm[idx] = perm[rng.permutation(idx)]
            f, k2 = self.fit(nested + "|" + pd.Series(perm))
            assert k2 == K, (k2, K)  # 結合セル集合が保たれている
            null.append(f - ref)
        null = np.asarray(null)
        info = real < null.min()
        print(f"{name:34s} K={K - base_k:3d} 実測Δ={real:+.6f} "
              f"ヌル 平均{null.mean():+.6f}/最良{null.min():+.6f} "
              f"→ {'情報あり' if info else '情報なし'}  (超過 {real - null.min():+.6f})")
        return real, null.min(), info


class AdditiveOracle(Oracle):
    """参照軸を結合セルでなく**加法の主効果**(各軸のダミーの和)で入れる版。

    結合セルは軸を足すたびに K が掛け算で膨らみ(ref3 で K≈2,600)、過学習ノイズが効果を
    飲み込む。加法なら K は足し算(6+6+4+6+6 ≈ 28)で、候補の増分=『全参照軸の主効果の上で
    候補の主効果が持つ情報』。順列ヌルは結合参照セル内シャッフル(全参照軸との関連を保つ)。"""

    def fit_add(self, axes) -> tuple[float, int]:
        codes, sizes = [], []
        for ax in axes:
            c, u = pd.factorize(pd.Series(ax).astype(str))
            codes.append(c.astype(np.intp)); sizes.append(len(u))
        offs = np.cumsum([0, *sizes[:-1]])
        K = int(sum(sizes))
        rid, nR, lp, iswin = self.rid, self.nR, self.lp, self.iswin
        nrace = float(nR)

        def fg(b):
            s = lp.copy()
            for c, o in zip(codes, offs):
                s += b[o + c]
            m = np.full(nR, -np.inf); np.maximum.at(m, rid, s)
            e = np.exp(s - m[rid]); den = np.zeros(nR); np.add.at(den, rid, e)
            sm = e / den[rid]
            g = np.zeros(K)
            for c, o in zip(codes, offs):
                np.add.at(g, o + c[iswin], -1.0); np.add.at(g, o + c, sm)
            return -np.sum(np.log(sm[iswin])) / nrace, g / nrace

        from scipy.optimize import minimize
        r = minimize(fg, np.zeros(K), jac=True, method="L-BFGS-B",
                     options={"maxiter": 4000, "ftol": 1e-14, "gtol": 1e-10})
        return float(r.fun), K

    def screen_add(self, name, cand, ref_axes, n_null=8, rng=None):
        rng = rng or np.random.default_rng(0)
        cand = pd.Series(cand).astype(str).reset_index(drop=True)
        ref_axes = [pd.Series(a).astype(str).reset_index(drop=True) for a in ref_axes]
        ref, base_k = self.fit_add(ref_axes)
        full, K = self.fit_add([*ref_axes, cand])
        real = full - ref
        joint = ref_axes[0].copy()
        for a in ref_axes[1:]:
            joint = joint + "|" + a
        groups = joint.groupby(joint).indices
        null = []
        for _ in range(n_null):
            perm = cand.to_numpy().copy()
            for idx in groups.values():
                perm[idx] = perm[rng.permutation(idx)]
            f, _ = self.fit_add([*ref_axes, pd.Series(perm)])
            null.append(f - ref)
        null = np.asarray(null)
        info = real < null.min()
        print(f"{name:34s} K={K - base_k:3d} 実測Δ={real:+.6f} "
              f"ヌル 平均{null.mean():+.6f}/最良{null.min():+.6f} "
              f"→ {'情報あり' if info else '情報なし'}  (超過 {real - null.min():+.6f})")
        return real, null.min(), info


FROM, TO = "2024-01-01", "2026-12-31"
OUT = Path("out/class-support-killtest")

# features/extra_features._CLASS_RANK と同じ(障害 JG は NaN)。grade 列(netkeiba cutover 後は
# race_class=オープン + grade=G1.. に分裂している行が残る)も同じ序数に畳む。
_CLASS_RANK = {
    "新馬": 0, "未勝利": 0,
    "1勝": 1, "1勝クラス": 1, "500万": 1, "500万下": 1,
    "2勝": 2, "2勝クラス": 2, "1000万": 2, "1000万下": 2,
    "3勝": 3, "3勝クラス": 3, "1600万": 3, "1600万下": 3,
    "オープン": 4, "OP": 4, "OP(L)": 4, "L": 4, "リステッド": 4,
    "G3": 5, "GIII": 5, "G2": 6, "GII": 6, "G1": 7, "GI": 7,
}
_GRADE_RANK = {"G1": 7, "G2": 6, "G3": 5, "A": 7, "B": 6, "C": 5, "L": 4}


def _norm(v):
    return unicodedata.normalize("NFKC", v).strip() if isinstance(v, str) else v


def class_rank(race_class: pd.Series, grade: pd.Series) -> pd.Series:
    rc = race_class.map(_norm).map(_CLASS_RANK)
    gr = grade.map(_norm).map(_GRADE_RANK)
    return pd.concat([rc, gr], axis=1).max(axis=1).astype("float64")


def load_runs() -> pd.DataFrame:
    """全履歴の started 走(as-of 集約の元)。"""
    eng = create_engine(DB)
    with eng.connect() as c:
        r = pd.read_sql(text("""
            SELECT rh.race_id, rh.horse_id, r.race_date, r.race_class, r.grade, rh.odds,
                   r.track_type
            FROM race_horses rh JOIN races r USING (race_id)
            WHERE rh.entry_status = 'started'
        """), c)
    r["race_date"] = pd.to_datetime(r["race_date"])
    r["cls"] = class_rank(r["race_class"], r["grade"])
    # complete-field market support s = log(q·N) (069 と同じ規約: 1.0 <= O < 999.9 が全馬で揃う)
    o = pd.to_numeric(r["odds"], errors="coerce")
    r["_valid"] = o.notna() & np.isfinite(o) & (o >= 1.0) & (o < 999.9)
    by = r.groupby("race_id")["_valid"]
    r["_complete"] = by.transform("sum") == by.transform("size")
    inv = 1.0 / o.where(r["_complete"])
    den = inv.groupby(r["race_id"]).transform("sum")
    n = r.groupby("race_id")["horse_id"].transform("size")
    r["s"] = np.log(inv / den * n)
    return r[["race_id", "horse_id", "race_date", "cls", "s", "track_type"]]


def load_targets(model: str) -> pd.DataFrame:
    eng = create_engine(DB)
    with eng.connect() as c:
        d = pd.read_sql(text("""
            SELECT pru.race_id, rp.horse_id, rp.win_prob AS p, r.race_date, r.race_class, r.grade,
                   r.track_type,
                   (rr.result_status = 'finished' AND rr.finish_order = 1)::int AS is_win
            FROM race_predictions rp
            JOIN prediction_runs pru USING (prediction_run_id)
            JOIN races r ON r.race_id = pru.race_id
            JOIN race_horses rh ON rh.race_id = pru.race_id AND rh.horse_id = rp.horse_id
            LEFT JOIN race_results rr ON rr.race_id = pru.race_id AND rr.horse_id = rp.horse_id
            WHERE pru.model_version = :mv AND rh.entry_status = 'started'
              AND r.race_date BETWEEN :a AND :b
        """), c, params={"mv": model, "a": FROM, "b": TO})
    d = d[d["p"].notna() & (d["p"] > 0)].copy()
    d["race_date"] = pd.to_datetime(d["race_date"])
    # 平地のみ(障害は別ラダー)・勝者ちょうど 1 頭
    d = d[d["track_type"].astype(str).str.lower() != "jump"]
    w = d.groupby("race_id")["is_win"].sum()
    d = d[d["race_id"].isin(w[w == 1].index)].copy()
    d["cls"] = class_rank(d["race_class"], d["grade"])
    d = d[d["cls"].notna()].reset_index(drop=True)  # 今走のクラス不明は条件付けできない
    return d


def asof_axes(d: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    """対象行ごとに、strictly-before(同日除外)の過去走から候補軸を作る。

    行数 ~12 万 × 過去走 なので、馬ごとに過去走配列を持ち Python ループで集約する
    (1 回きりのスクリーニングなので速度より明快さ)。"""
    src = runs[runs["s"].notna() & runs["cls"].notna()].sort_values(["horse_id", "race_date"])
    hist = {h: (g["race_date"].to_numpy(), g["s"].to_numpy(), g["cls"].to_numpy())
            for h, g in src.groupby("horse_id", sort=False)}
    cols = {k: [] for k in (
        "n_obs", "s_last", "s_mean5", "cls_last", "cls_mean5",
        "s_ge", "n_ge", "s_lt", "n_lt", "s_adj05", "s_adj10", "s_career")}
    for h, dt, cls_today in zip(d["horse_id"], d["race_date"].to_numpy(), d["cls"]):
        rec = hist.get(h)
        if rec is None:
            k = 0
        else:
            dates, s, c = rec
            k = int(np.searchsorted(dates, dt, side="left"))  # race_date < dt (同日除外)
        if k == 0:
            for key in cols:
                cols[key].append(0 if key in ("n_obs", "n_ge", "n_lt") else np.nan)
            continue
        s5, c5 = s[max(0, k - 5):k], c[max(0, k - 5):k]
        sk, ck = s[:k], c[:k]
        ge, lt = ck >= cls_today, ck < cls_today
        cols["n_obs"].append(k)
        cols["s_career"].append(sk.mean())
        cols["s_last"].append(s[k - 1])
        cols["s_mean5"].append(s5.mean())
        cols["cls_last"].append(c[k - 1])
        cols["cls_mean5"].append(c5.mean())
        cols["n_ge"].append(int(ge.sum()))
        cols["s_ge"].append(sk[ge].mean() if ge.any() else np.nan)
        cols["n_lt"].append(int(lt.sum()))
        cols["s_lt"].append(sk[lt].mean() if lt.any() else np.nan)
        cols["s_adj05"].append((s5 + 0.5 * (c5 - cls_today)).mean())
        cols["s_adj10"].append((s5 + 1.0 * (c5 - cls_today)).mean())
    for key, v in cols.items():
        d[key] = v
    d["gap_last"] = d["cls"] - d["cls_last"]         # 今走 − 前走(正=昇級)
    d["gap_mean5"] = d["cls"] - d["cls_mean5"]
    return d


def _b(x: pd.Series, bins) -> pd.Series:
    return pd.cut(x, bins).astype(str)  # NaN -> "nan" セル(欠損も 1 セルとして扱う)


def _gap3(x: pd.Series) -> pd.Series:
    return pd.Series(np.select([x.isna(), x < 0, x > 0], ["none", "down", "up"], "same"),
                     index=x.index)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="lgbm-058-acc")
    ap.add_argument("--n-null", type=int, default=8)
    a = ap.parse_args()

    runs = load_runs()
    d = asof_axes(load_targets(a.model), runs)
    print(f"model={a.model} rows={len(d)} races={d['race_id'].nunique()} "
          f"{d['race_date'].min().date()}..{d['race_date'].max().date()}")
    print(f"支持履歴あり {np.mean(d['n_obs'] > 0):.1%} / 同格以上の過去走あり {np.mean(d['n_ge'] > 0):.1%} "
          f"/ 格下の過去走あり {np.mean(d['n_lt'] > 0):.1%}")
    print("今走 class_rank 分布:", d.groupby("cls")["race_id"].nunique().to_dict(), "\n")

    S = [-np.inf, -1.0, -0.5, 0.0, 0.5, 1.0, np.inf]       # 支持 s のビン(6)
    C = [-0.5, 0.5, 1.5, 2.5, 3.5, 4.5, 7.5]                 # 過去走クラス平均(6)
    G = [-np.inf, -1.5, -0.5, 0.5, 1.5, np.inf]              # 平均格差(5)

    ref1 = _b(d["s_mean5"], S) + "|" + _b(d["cls_mean5"], C)
    ref2 = ref1 + "|" + _gap3(d["gap_last"])
    cand = {
        "C1 同格以上での支持": _b(d["s_ge"], S),
        "C2 格下での支持": _b(d["s_lt"], S),
        "C3 前走支持×前走格差": _b(d["s_last"], S) + "|" + _gap3(d["gap_last"]),
        "C4 直近5走支持×平均格差": _b(d["s_mean5"], S) + "|" + _b(d["gap_mean5"], G),
        "C5a 格調整支持 γ=0.5": _b(d["s_adj05"], S),
        "C5b 格調整支持 γ=1.0": _b(d["s_adj10"], S),
        # クラスを見ない双子(対照): 候補の増分が「クラス文脈」か「別の支持集約」かを分ける
        "T1 [対照] 全過去走の支持平均(クラス無視)": _b(d["s_career"], S),
        "T2 [対照] 前走支持 s_last(クラス無視)": _b(d["s_last"], S),
    }
    marg = {
        "[既存] 直近5走支持 s_mean5": _b(d["s_mean5"], S),
        "[既存] 前走支持 s_last": _b(d["s_last"], S),
        "[既存] 過去走クラス平均": _b(d["cls_mean5"], C),
        "[既存] 昇降級(前走比)": _gap3(d["gap_last"]),
        "[既存] ref1 = 支持×クラス平均": ref1,
        "[既存] ref2 = ref1×昇降級": ref2,
    }
    o = Oracle(d)
    res = {"model": a.model, "rows": int(len(d)), "races": int(d["race_id"].nunique()),
           "baseline_nll": o.base, "single": {}, "nested_ref1": {}, "nested_ref2": {}}
    rng = np.random.default_rng(20260822)
    print(f"baseline winner NLL = {o.base:.6f}\n--- 単独オラクル(順列ヌルの床と比較) ---")
    for k, v in {**marg, **cand}.items():
        res["single"][k] = o.screen(k, v, n_null=a.n_null, rng=rng)
    print("\n--- 入れ子 ref1(支持×過去走クラス平均)の上に積む ---")
    for k, v in cand.items():
        res["nested_ref1"][k] = o.screen("+ " + k, v, nested=ref1, n_null=a.n_null, rng=rng)
    print("\n--- 入れ子 ref2(ref1×昇降級)の上に積む ---")
    for k, v in cand.items():
        res["nested_ref2"][k] = o.screen("+ " + k, v, nested=ref2, n_null=a.n_null, rng=rng)
    # 決定打: ref2 にクラス無視の支持集約(対照 T1/T2)まで積んだ上で、C1 がなお残るか
    ref3 = ref2 + "|" + _b(d["s_career"], S) + "|" + _b(d["s_last"], S)
    print("\n--- 入れ子 ref3(ref2×全過去走支持×前走支持 = クラス無視の支持集約を全部含む)の上に積む ---")
    res["nested_ref3"] = {}
    for k in ("C1 同格以上での支持", "C2 格下での支持", "C5a 格調整支持 γ=0.5"):
        res["nested_ref3"][k] = o.screen("+ " + k, cand[k], nested=ref3, n_null=a.n_null, rng=rng)

    # 加法版(決定打): 参照 = 支持 mean5 / 過去走クラス平均 / 昇降級 / 全過去走支持 / 前走支持 /
    # 今走クラス の主効果。候補の主効果が、クラス無視の支持集約をすべて含む参照の上で残るか。
    ao = AdditiveOracle(d)
    refA = [_b(d["s_mean5"], S), _b(d["cls_mean5"], C), _gap3(d["gap_last"]),
            _b(d["s_career"], S), _b(d["s_last"], S), d["cls"].astype(int).astype(str)]
    refA_nll, refA_k = ao.fit_add(refA)
    print(f"\n--- 加法オラクル: 参照 K={refA_k} Δ={refA_nll - ao.base:+.6f}(主効果の和)の上に候補の主効果を積む ---")
    res["additive"] = {}
    for k, v in cand.items():
        if k.startswith("T"):
            continue  # 対照は参照に含まれている
        res["additive"][k] = ao.screen_add("+ " + k, v, refA, n_null=a.n_null, rng=rng)
    # 交互作用版: 同格以上での支持 × 今走クラス(重賞での支持ほど意味が違う、を許す)
    res["additive"]["C1x 同格以上支持×今走クラス"] = ao.screen_add(
        "+ C1x 同格以上支持×今走クラス", _b(d["s_ge"], S) + "|" + d["cls"].astype(int).astype(str),
        refA, n_null=a.n_null, rng=rng)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{a.model}.json"
    path.write_text(json.dumps(
        {k: ({kk: [float(x[0]), float(x[1]), bool(x[2])] for kk, x in v.items()}
             if isinstance(v, dict) else v) for k, v in res.items()},
        ensure_ascii=False, indent=2))
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
