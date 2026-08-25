"""当日・当該競馬場の馬場バイアスの kill-test スクリーニング。

問い: 「その開催日・その競馬場で、**先に行われたレース**が示した傾向(内枠が来る/前が
残る)を完璧に使えたら、winner NLL がどれだけ下がるか」。

なぜ一度も測られていないか: 特徴層の as-of 機構は全て「daily cumsum − 当日」で
**同日を丸ごと除外**する。これは馬・騎手・種牡馬の累積成績に対しては正しいリーク防御
だが、同じ規約を機械的に全軸へ適用した結果、「同日・同開催の先行レースの結果」という
**発走前に合法的に手に入る情報**が構造的に一度も候補に上がらなかった。第 1R の結果は
第 5R の発走前に確定している。

レース内 softmax なのでレース定数(その日のバイアスそのもの)は主効果が完全に消える。
効くとすれば **バイアス × 自馬の該当属性**(内有利 × 自馬の枠 / 前有利 × 自馬の先行度)
の交互作用に限る。031/032/033 で 3 回続けて効いた「新情報を効く形に変換する」型。
race-constant 単独も対照として測り、消えることを確認する。

依存/実行: screen_axes.py と同じ(active でなく被覆の広い lgbm-058-acc を使う)。

    cd training && uv run python ../scripts/screen_track_bias.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from screen_axes import DB, MODEL_VERSION, Oracle  # noqa: E402

FROM, TO = "2024-01-01", "2026-12-31"
BIAS_FROM = "2023-10-01"  # バイアス側は当日内で閉じるので窓を広げる必要は無いが余裕を持つ
MIN_PRIOR = 3             # その日その場で先に終わったレースがこれ未満なら「不明」セル


def _load(sql: str, **kw) -> pd.DataFrame:
    eng = create_engine(DB)
    with eng.connect() as c:
        return pd.read_sql(text(sql), c, params=kw)


def load_pred() -> pd.DataFrame:
    """判定対象(予測が永続化されているレース)。"""
    d = _load("""
        SELECT pru.race_id, rp.horse_id, rp.win_prob AS p,
               r.race_date, r.venue_code, r.race_number, r.track_type,
               rh.horse_number, rh.frame,
               (rr.result_status = 'finished' AND rr.finish_order = 1)::int AS is_win
        FROM prediction_runs pru
        JOIN race_predictions rp USING (prediction_run_id)
        JOIN races r ON r.race_id = pru.race_id
        JOIN race_horses rh ON rh.race_id = pru.race_id AND rh.horse_id = rp.horse_id
        LEFT JOIN race_results rr ON rr.race_id = pru.race_id AND rr.horse_id = rp.horse_id
        WHERE pru.model_version = :mv
          AND rh.entry_status = 'started'
          AND r.race_date BETWEEN :a AND :b
    """, mv=MODEL_VERSION, a=FROM, b=TO)
    d["race_date"] = pd.to_datetime(d["race_date"])
    # 勝者が居ないレース(全馬取消・同着未確定)は winner NLL の標本にならない
    keep = d.groupby("race_id")["is_win"].transform("sum") == 1
    d = d[keep].copy()
    d["field_size"] = d.groupby("race_id")["horse_id"].transform("size")
    d["draw_rel"] = (d["horse_number"] - 1) / (d["field_size"] - 1).replace(0, np.nan)
    return d


def load_race_outcomes() -> pd.DataFrame:
    """全レースの「勝ち馬がどこを通って勝ったか」。当日バイアスの素。"""
    d = _load("""
        SELECT r.race_id, r.race_date, r.venue_code, r.race_number, r.track_type,
               rh.horse_number, rr.finish_order, rr.corner_orders,
               COUNT(*) OVER (PARTITION BY r.race_id) AS field_size
        FROM races r
        JOIN race_horses rh ON rh.race_id = r.race_id AND rh.entry_status = 'started'
        JOIN race_results rr ON rr.race_id = r.race_id AND rr.horse_id = rh.horse_id
        WHERE r.race_date >= :a AND rr.result_status = 'finished'
    """, a=BIAS_FROM)
    d["race_date"] = pd.to_datetime(d["race_date"])
    d["draw_rel"] = (d["horse_number"] - 1) / (d["field_size"] - 1).replace(0, np.nan)
    first = d["corner_orders"].map(lambda a: float(a[0]) if isinstance(a, list) and a else np.nan)
    d["early_rel"] = (first - 1) / (d["field_size"] - 1).replace(0, np.nan)
    return d


def day_bias(out: pd.DataFrame) -> pd.DataFrame:
    """(開催日, 競馬場) 内で **レース番号が小さい側だけ** を使う expanding 平均。

    勝ち馬 1 頭の枠位置・道中位置を、その日その場で先に終わったレース全部で平均する。
    低い = 内/前が勝っている日。target レース自身は必ず除く(shift ではなく cumsum−自分)。
    """
    w = out[out["finish_order"] == 1].copy()
    w = w.sort_values(["race_date", "venue_code", "race_number"], kind="stable")
    g = w.groupby(["race_date", "venue_code"], sort=False)
    for col in ("draw_rel", "early_rel"):
        v = w[col]
        # 欠損を含む列の「自分より前の平均」= (累積和 − 自分) / (累積件数 − 自分)
        s = g[col].cumsum() - v.fillna(0.0)
        n = g[col].transform(lambda x: x.notna().cumsum()) - v.notna().astype(int)
        w[f"bias_{col}"] = np.where(n >= MIN_PRIOR, s / n.replace(0, np.nan), np.nan)
    w["n_prior"] = g.cumcount()
    return w[["race_id", "bias_draw_rel", "bias_early_rel", "n_prior"]]


def horse_style(out: pd.DataFrame) -> pd.DataFrame:
    """自馬の as-of 先行度(直近 3 走の道中位置パーセンタイル平均・strictly-before)。

    特徴層の front_runner_rate と同型の量だが、ここは DB だけで閉じる近似で足りる。
    """
    d = _load("""
        SELECT rh.horse_id, r.race_id, r.race_date, rr.corner_orders,
               COUNT(*) OVER (PARTITION BY r.race_id) AS field_size
        FROM races r
        JOIN race_horses rh ON rh.race_id = r.race_id AND rh.entry_status = 'started'
        JOIN race_results rr ON rr.race_id = r.race_id AND rr.horse_id = rh.horse_id
        WHERE rr.result_status = 'finished'
    """)
    d["race_date"] = pd.to_datetime(d["race_date"])
    first = d["corner_orders"].map(lambda a: float(a[0]) if isinstance(a, list) and a else np.nan)
    d["early_rel"] = (first - 1) / (d["field_size"] - 1).replace(0, np.nan)
    d = d.sort_values(["horse_id", "race_date", "race_id"], kind="stable")
    g = d.groupby("horse_id", sort=False)["early_rel"]
    d["own_early"] = g.transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    return d[["race_id", "horse_id", "own_early"]]


def q(s: pd.Series, k: int, label: str) -> pd.Series:
    """欠損を独立セルに逃がした分位。欠損を混ぜると『情報あり』が欠損パターン由来になる。"""
    out = pd.Series(f"{label}=na", index=s.index, dtype=object)
    ok = s.notna()
    if ok.sum() > k:
        out[ok] = label + "=" + pd.qcut(s[ok], k, labels=False, duplicates="drop").astype(str)
    return out


def main() -> None:
    d = load_pred()
    out = load_race_outcomes()
    d = d.merge(day_bias(out), on="race_id", how="left")
    d = d.merge(horse_style(out), on=["race_id", "horse_id"], how="left")
    d = d.reset_index(drop=True)

    n_ok = d.groupby("race_id")["bias_draw_rel"].first().notna().sum()
    print(f"rows={len(d)} races={d['race_id'].nunique()} "
          f"{d['race_date'].min().date()}..{d['race_date'].max().date()}")
    print(f"当日バイアスが定義できたレース = {n_ok} "
          f"({100 * n_ok / d['race_id'].nunique():.1f}%, 先行 {MIN_PRIOR} 鞍以上)")
    print(f"自馬の as-of 先行度あり = {100 * d['own_early'].notna().mean():.1f}%\n")

    o = Oracle(d)
    print(f"baseline winner NLL = {o.base:.6f}\n")

    bd = q(d["bias_draw_rel"], 3, "内外")
    be = q(d["bias_early_rel"], 3, "前後")
    dr = q(d["draw_rel"], 4, "自枠")
    oe = q(d["own_early"], 4, "自先行")

    print("--- 対照(レース定数は softmax で消えるはず) ---")
    o.screen("C1 当日の内外バイアス単独", bd)
    o.screen("C2 当日の前後バイアス単独", be)

    print("\n--- 単独オラクル(交互作用) ---")
    o.screen("T1 内外バイアス × 自馬の枠", bd + "|" + dr)
    o.screen("T2 前後バイアス × 自馬の先行度", be + "|" + oe)

    print("\n--- 既存軸の上に積んだ増分(本命の判定) ---")
    print("    nested = 自馬の枠 / 自馬の先行度(いずれもモデルが既に持っている情報)")
    o.screen("T1n 内外バイアスの増分", bd, nested=dr)
    o.screen("T2n 前後バイアスの増分", be, nested=oe)

    turf = d["track_type"].astype(str).str.contains("芝")
    print(f"\n--- 芝限定({int(turf.sum())} 行) ---")
    dt = d[turf].reset_index(drop=True)
    ot = Oracle(dt)
    print(f"baseline winner NLL = {ot.base:.6f}")
    bdt, drt = q(dt["bias_draw_rel"], 3, "内外"), q(dt["draw_rel"], 4, "自枠")
    bet, oet = q(dt["bias_early_rel"], 3, "前後"), q(dt["own_early"], 4, "自先行")
    ot.screen("T3n 内外バイアスの増分(芝)", bdt, nested=drt)
    ot.screen("T4n 前後バイアスの増分(芝)", bet, nested=oet)


if __name__ == "__main__":
    main()
