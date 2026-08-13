"""候補特徴軸の kill-test スクリーニング(オラクル上限 + 順列ヌル)。

問い: 「軸 X を完璧に使える特徴があったら winner NLL がどれだけ下がるか」。
方法: active モデルの永続化予測 p を offset に固定し、軸 X のセルダミーだけを
レース内 softmax の対数スコアに足して winner NLL を in-sample 最小化する
(= conditional logit の offset モデル)。これは「その軸を完璧に使えた場合の上限」。

in-sample なので自由度を足すだけで必ず下がる。そこで**同じ自由度でラベルを
シャッフルした順列ヌル**を並走させ、過学習の床を測る。実測Δが床を下回って
初めて「情報あり」と言える。

さらに `nested=` を渡すと「既存軸の上に積んだ増分」を測る(こちらが本命の判定)。

使い方:
    cd training && uv run python ../scripts/screen_axes.py

依存: training パッケージの env(pandas + scipy + sqlalchemy)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sqlalchemy import create_engine, text

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
FROM, TO = "2024-01-01", "2026-12-31"

# 判定に使うモデル。active(lgbm-064-f02acc)は永続化予測が 289 レースしか無く
# スクリーニングに耐えないため、窓を広く覆う版を使う(2026-08 時点で 8,703 レース)。
# **これは candidate であって active ではない**。読み方の非対称性に注意:
#   「情報なし」= 古いモデル(=残差の余地が大きい側)ですら拾えない → その軸は死んでいる
#   「情報あり」= 曖昧。真に欠けた情報か、単にこのモデルが古いだけかを区別できない
MODEL_VERSION = "lgbm-058-acc"

# JRA 10 場の回り(新潟の直線1000mは無視した近似。スクリーニング目的には十分)
TURN = {"01": "R", "02": "R", "03": "R", "04": "L", "05": "L",
        "06": "R", "07": "L", "08": "R", "09": "R", "10": "R"}


def load() -> pd.DataFrame:
    """active モデルの予測 + 判定に要る素の列を 1 フレームに。"""
    eng = create_engine(DB)
    sql = text("""
        SELECT pru.race_id, rp.horse_id, rp.win_prob AS p,
               r.race_date, r.venue_code, r.race_name, r.race_class, r.distance,
               rh.trainer_id, rh.jockey_id, rh.frame, rh.horse_number,
               h.owner_name, h.sire_name,
               (rr.result_status = 'finished' AND rr.finish_order = 1)::int AS is_win
        FROM race_predictions rp
        JOIN prediction_runs pru USING (prediction_run_id)
        JOIN races r ON r.race_id = pru.race_id
        JOIN race_horses rh ON rh.race_id = pru.race_id AND rh.horse_id = rp.horse_id
        JOIN horses h ON h.horse_id = rp.horse_id
        LEFT JOIN race_results rr ON rr.race_id = pru.race_id AND rr.horse_id = rp.horse_id
        WHERE pru.model_version = :mv
          AND rh.entry_status = 'started'
          AND r.race_date BETWEEN :a AND :b
    """)
    with eng.connect() as c:
        d = pd.read_sql(sql, c, params={"a": FROM, "b": TO, "mv": MODEL_VERSION})
    d = d[d["p"].notna() & (d["p"] > 0)].copy()
    d["race_date"] = pd.to_datetime(d["race_date"])
    # 勝者がちょうど1頭のレースだけ(winner NLL の定義に合わせる)
    w = d.groupby("race_id")["is_win"].sum()
    d = d[d["race_id"].isin(w[w == 1].index)].reset_index(drop=True)
    d["field_size"] = d.groupby("race_id")["horse_id"].transform("size")
    return d


def add_axes(d: pd.DataFrame) -> pd.DataFrame:
    """スクリーニング対象の軸を組む(全て発走前に既知=リーク無し)。"""
    # A: 同厩舎の複数出走(レース内構成・自馬除外)
    d["stable_mates"] = d.groupby(["race_id", "trainer_id"])["horse_id"].transform("size") - 1
    d["owner_mates"] = d.groupby(["race_id", "owner_name"])["horse_id"].transform("size") - 1
    d["sire_mates"] = d.groupby(["race_id", "sire_name"])["horse_id"].transform("size") - 1
    # B: 回り替わり(前走 started との比較)
    d["turn"] = d["venue_code"].astype(str).str.zfill(2).map(TURN)
    prev = (d.sort_values(["horse_id", "race_date"], kind="stable")
              .groupby("horse_id")[["turn", "venue_code", "race_date"]].shift(1))
    d["prev_turn"] = prev["turn"]
    d["prev_venue"] = prev["venue_code"]
    d["days_since_last"] = (d["race_date"] - prev["race_date"]).dt.days
    d["turn_switch"] = np.where(d["prev_turn"].isna(), "none",
                                np.where(d["turn"] == d["prev_turn"], "same", "switch"))
    d["venue_repeat"] = np.where(d["prev_venue"].isna(), "none",
                                 np.where(d["venue_code"] == d["prev_venue"], "same", "diff"))
    # C: 牝馬限定戦
    d["himba"] = d["race_name"].fillna("").str.contains("牝").astype(int)
    # D: 相対枠(頭数で正規化)
    d["draw_rel"] = (d["horse_number"] - 1) / (d["field_size"] - 1).replace(0, np.nan)
    return d


class Oracle:
    """p を offset に固定した race-softmax の winner NLL 最小化。"""

    def __init__(self, d: pd.DataFrame) -> None:
        self.rid, _ = pd.factorize(d["race_id"])
        self.nR = int(self.rid.max()) + 1
        self.lp = np.log(d["p"].to_numpy())
        self.iswin = d["is_win"].to_numpy() == 1
        self.base = self.fit(pd.Series(np.zeros(len(d))))[0]

    def fit(self, cells) -> tuple[float, int]:
        codes, uniq = pd.factorize(pd.Series(cells).astype(str))
        codes = codes.astype(np.intp)
        K = len(uniq)
        rid, nR, lp, iswin = self.rid, self.nR, self.lp, self.iswin
        nrace = float(nR)

        def fg(b):
            s = lp + b[codes]
            m = np.full(nR, -np.inf)
            np.maximum.at(m, rid, s)
            e = np.exp(s - m[rid])
            den = np.zeros(nR)
            np.add.at(den, rid, e)
            sm = e / den[rid]
            gw = np.zeros(K)
            np.add.at(gw, codes[iswin], 1.0)
            ge = np.zeros(K)
            np.add.at(ge, codes, sm)
            return -np.sum(np.log(sm[iswin])) / nrace, -(gw - ge) / nrace

        r = minimize(fg, np.zeros(K), jac=True, method="L-BFGS-B",
                     options={"maxiter": 2000, "ftol": 1e-14, "gtol": 1e-10})
        return float(r.fun), K

    def screen(self, name: str, cells, nested=None, n_null: int = 8, rng=None):
        """nested を渡すと『既存軸の上に積んだ増分』、無ければ単独オラクル。"""
        rng = rng or np.random.default_rng(0)
        cells = pd.Series(cells).astype(str).reset_index(drop=True)
        if nested is None:
            ref, base_k = self.base, 0
            full, K = self.fit(cells)
        else:
            nested = pd.Series(nested).astype(str).reset_index(drop=True)
            ref, base_k = self.fit(nested)
            full, K = self.fit(nested + "|" + cells)
        real = full - ref
        null = []
        for _ in range(n_null):
            perm = cells.to_numpy().copy()
            rng.shuffle(perm)
            f, _k = self.fit(pd.Series(perm) if nested is None
                             else nested + "|" + pd.Series(perm))
            null.append(f - ref)
        null = np.asarray(null)
        info = real < null.min()
        print(f"{name:34s} K={K - base_k:3d} 実測Δ={real:+.6f} "
              f"ヌル 平均{null.mean():+.6f}/最良{null.min():+.6f} "
              f"→ {'情報あり' if info else '情報なし'}")
        return real, null.min(), info


def main() -> None:
    d = add_axes(load())
    print(f"rows={len(d)} races={d['race_id'].nunique()} "
          f"{d['race_date'].min()}..{d['race_date'].max()}\n")
    o = Oracle(d)
    print(f"baseline winner NLL = {o.base:.6f}\n--- 単独オラクル ---")
    B = lambda s, bins: pd.cut(d[s], bins)  # noqa: E731
    o.screen("A1 同厩舎の複数出走", B("stable_mates", [-1, 0, 1, 2, 99]))
    o.screen("A2 同馬主の複数出走", B("owner_mates", [-1, 0, 1, 99]))
    o.screen("A3 同種牡馬の複数出走", B("sire_mates", [-1, 0, 1, 2, 99]))
    o.screen("B1 回り替わり", d["turn_switch"])
    o.screen("B2 同一開催場リピート", d["venue_repeat"])
    o.screen("C1 牝馬限定戦", d["himba"])
    o.screen("D1 相対枠", B("draw_rel", [-0.01, 0.25, 0.5, 0.75, 1.01]))
    o.screen("E1 [既存列]レース間隔", B("days_since_last", [-1, 14, 28, 56, 120, 9999]))


if __name__ == "__main__":
    main()


def nested_checks() -> None:
    """単独で床を超えた軸が、既存軸(通算勝率 x キャリア長)の上でも残るかを見る。

    days_since_last は **既に特徴列にある**ので、ここで残る=『欠けている特徴』ではなく
    『既存列をモデルが使い切れていない』(= 再学習/再校正の領域)を意味する。"""
    d = add_axes(load())
    eng = create_engine(DB)
    with eng.connect() as c:  # as-of 通算成績(strictly-before)
        h = pd.read_sql(text("""
            SELECT r.race_id, rh.horse_id, r.race_date,
                   (rr.result_status='finished' AND rr.finish_order=1)::int AS w
            FROM race_horses rh JOIN races r USING (race_id)
            LEFT JOIN race_results rr ON rr.race_id=r.race_id AND rr.horse_id=rh.horse_id
            WHERE rh.entry_status='started'"""), c)
    h["race_date"] = pd.to_datetime(h["race_date"])
    h = h.sort_values(["horse_id", "race_date"], kind="stable")
    g = h.groupby("horse_id", sort=False)
    h["pw"] = g["w"].cumsum() - h["w"]
    h["ps"] = g.cumcount()
    h["prior_wr"] = np.where(h["ps"] > 0, h["pw"] / h["ps"], np.nan)
    d = d.merge(h[["race_id", "horse_id", "prior_wr", "ps"]], on=["race_id", "horse_id"], how="left")

    o = Oracle(d)
    ref = (pd.cut(d["prior_wr"], [-0.001, 0, .08, .15, .25, 1.]).astype(str)
           + "|" + pd.cut(d["ps"], [-1, 3, 8, 15, 25, 9999]).astype(str))
    print("\n--- 入れ子(既存軸=通算勝率 x キャリア長 の上に積む) ---")
    o.screen("既存軸のみ(参考:単独)", ref)
    o.screen("+ A3 同種牡馬の複数出走", pd.cut(d["sire_mates"], [-1, 0, 1, 2, 99]), nested=ref)
    o.screen("+ D1 相対枠", pd.cut(d["draw_rel"], [-.01, .25, .5, .75, 1.01]), nested=ref)
    o.screen("+ E1 [既存列]レース間隔", pd.cut(d["days_since_last"], [-1, 14, 28, 56, 120, 9999]), nested=ref)
