"""ROI 広域探索(2026-09-23)— 単一 parquet の構築。

DB(1986〜2026・平地・started)から 1 行 = 1 出走馬の表を作り、レース文脈・市場・静的属性・
strictly-before の履歴・騎手/調教師 as-of・同日先行レース動態・モデル OOF(108 束)・結果(精算専用)
を列として保存する。列の語彙は docs/roi-pattern-exploration-20260923/design.md §1 が正本。

    cd training && uv run python ../scripts/roi_explore/build_dataset.py

出力: artifacts/roi_explore/rows.parquet / dividends.parquet / quotes.parquet / build_report.json
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

REPO = pathlib.Path(__file__).resolve().parents[2]
OUT = REPO / "artifacts" / "roi_explore"
BUNDLE = (
    REPO
    / "artifacts/oof/8bdde26857f62c5571ef45b02954836dacae7e4a2ea9174c12eb0c9209fb691f/bundle.json"
)
DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
)

SQL = """
select rh.race_id, rh.horse_id, rh.horse_number, rh.frame, rh.sex, rh.age, rh.weight, rh.weight_diff,
       rh.jockey_weight, rh.odds, rh.popularity, rh.running_style, rh.jockey_id, rh.trainer_id,
       rh.place_odds_low, rh.place_odds_high,
       r.race_date, r.venue_code, r.race_number, r.distance, r.track_type, r.going, r.weather,
       r.race_class, r.grade, r.prize_money,
       rs.finish_order, rs.result_status, extract(epoch from rs.finish_time_diff) as margin_sec,
       rs.last_3f,
       h.sire_line, h.damsire_line
from race_horses rh
join races r using(race_id)
left join race_results rs on rs.race_id = rh.race_id and rs.horse_id = rh.horse_id
left join horses h on h.horse_id = rh.horse_id
where rh.entry_status = 'started' and r.race_date >= '1986-01-01' and r.track_type <> '障'
order by rh.race_id, rh.horse_number
"""

_CLASS_RANK = {"debut": 0, "maiden": 1, "C1": 2, "C2": 3, "C3": 4, "OP": 5}
_CLASS_CANON = {
    "新馬": "debut", "未出走": "debut", "未勝利": "maiden",
    "1勝": "C1", "１勝": "C1", "300万": "C1", "400万": "C1", "500万": "C1", "５００万": "C1",
    "2勝": "C2", "２勝": "C2", "600万": "C2", "700万": "C2", "800万": "C2", "900万": "C2",
    "1000万": "C2", "１０００万": "C2",
    "3勝": "C3", "３勝": "C3", "1400万": "C3", "1500万": "C3", "1600万": "C3", "１６００万": "C3",
    "ｵｰﾌﾟﾝ": "OP", "オープン": "OP", "OP": "OP", "OP(L)": "OP", "L": "OP", "重賞": "OP",
    "Ｇ３": "OP", "Ｇ２": "OP", "Ｇ１": "OP", "G3": "OP", "G2": "OP", "G1": "OP",
}


def canon_class(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    s = str(raw).strip()
    if s in _CLASS_CANON:
        return _CLASS_CANON[s]
    for k, v in _CLASS_CANON.items():
        if s.startswith(k):
            return v
    return None


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def dist_band(d: pd.Series) -> pd.Series:
    return pd.cut(d, [0, 1400, 1800, 2200, 10000], right=False,
                  labels=["sprint", "mile", "mid", "long"]).astype(object)


def rolling_window_counts(df: pd.DataFrame, key: str, window_days: int = 365) -> pd.DataFrame:
    """Per (key, race_date): starts/wins strictly before the date, all-time and within window."""
    daily = (df.groupby([key, "race_date"], sort=True)
               .agg(n=("won", "size"), w=("won", "sum"), eq=("q", "sum")).reset_index())
    daily["race_date"] = pd.to_datetime(daily["race_date"])
    out_all_n = np.zeros(len(daily)); out_all_w = np.zeros(len(daily))
    out_win_n = np.zeros(len(daily)); out_win_w = np.zeros(len(daily))
    out_all_q = np.zeros(len(daily)); out_win_q = np.zeros(len(daily))
    eq = daily["eq"].fillna(0.0).to_numpy(dtype=float)
    keys = daily[key].to_numpy()
    dates = daily["race_date"].to_numpy().astype("datetime64[D]").astype(np.int64)
    n = daily["n"].to_numpy(dtype=float); w = daily["w"].to_numpy(dtype=float)
    # group boundaries (daily is sorted by key then date)
    change = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1], True])
    for a, b in zip(change[:-1], change[1:]):
        d = dates[a:b]; cn = np.r_[0.0, np.cumsum(n[a:b])]; cw = np.r_[0.0, np.cumsum(w[a:b])]
        cq = np.r_[0.0, np.cumsum(eq[a:b])]
        # strictly before: cumulative up to (excluding) index i
        idx = np.arange(b - a)
        out_all_n[a:b] = cn[idx]; out_all_w[a:b] = cw[idx]; out_all_q[a:b] = cq[idx]
        lo = np.searchsorted(d, d - window_days, side="left")
        out_win_n[a:b] = cn[idx] - cn[lo]; out_win_w[a:b] = cw[idx] - cw[lo]; out_win_q[a:b] = cq[idx] - cq[lo]
    daily["all_n"] = out_all_n; daily["all_w"] = out_all_w; daily["all_q"] = out_all_q
    daily["win_n"] = out_win_n; daily["win_w"] = out_win_w; daily["win_q"] = out_win_q
    return daily.drop(columns=["n", "w", "eq"])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log("loading rows from DB …")
    with create_engine(DB_URL).connect() as c:
        df = pd.read_sql(text(SQL), c)
    log(f"rows={len(df):,} races={df.race_id.nunique():,}")

    # ---- types
    df["race_date"] = pd.to_datetime(df["race_date"])
    for col in ("odds", "jockey_weight", "last_3f", "place_odds_low", "place_odds_high", "margin_sec"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    for col in ("frame", "age", "weight", "weight_diff", "popularity", "distance", "race_number",
                "prize_money", "finish_order", "horse_number"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df["race_id"] = df["race_id"].astype(str)
    df["horse_id"] = df["horse_id"].astype(str)
    df["finished"] = (df["result_status"] == "finished")
    df["won"] = df["finished"] & (df["finish_order"] == 1.0)
    df["finish_order"] = np.where(df["finished"], df["finish_order"], np.nan)
    df["year"] = df["race_date"].dt.year.astype(int)
    df["month"] = df["race_date"].dt.month.astype(int)
    df["dow"] = df["race_date"].dt.dayofweek.astype(int)
    df["dist_band"] = dist_band(df["distance"])
    df["race_class_canon"] = [canon_class(x) for x in df["race_class"]]
    df["class_rank"] = df["race_class_canon"].map(_CLASS_RANK).astype(float)
    df["is_graded"] = df["grade"].isin(["A", "B", "C", "G1", "G2", "G3"]) | df["race_class"].isin(
        ["Ｇ１", "Ｇ２", "Ｇ３"])
    df["grade"] = df["grade"].map({"A": "G1", "B": "G2", "C": "G3", "L": "L", "G1": "G1", "G2": "G2",
                                   "G3": "G3"}).astype(object)

    # ---- race level market structure
    log("race-level market …")
    g = df.groupby("race_id", sort=False)
    df["field_size"] = g["horse_id"].transform("size").astype(float)
    df["race_ok"] = g["odds"].transform(lambda s: bool(s.notna().all() and (s > 0).all()))
    df["n_winners"] = g["won"].transform("sum").astype(int)
    inv = 1.0 / df["odds"]
    df["q"] = inv / g_transform_sum(inv, df["race_id"])
    # ranks by odds with horse_number tiebreak → use sort
    order = df.sort_values(["race_id", "odds", "horse_number"]).index
    rank = np.empty(len(df), dtype=float)
    rank[order] = df.loc[order].groupby("race_id", sort=False).cumcount().to_numpy() + 1
    df["odds_rank"] = rank
    fav = df[df["odds_rank"] == 1].set_index("race_id")["odds"]
    sec = df[df["odds_rank"] == 2].set_index("race_id")["odds"]
    df["fav_odds"] = df["race_id"].map(fav).astype(float)
    df["second_odds"] = df["race_id"].map(sec).astype(float)
    df["odds_gap12"] = df["second_odds"] - df["fav_odds"]
    df["fav_q"] = g["q"].transform("max")
    df["q_share_of_fav"] = df["q"] / df["fav_q"]
    qlog = -(df["q"] * np.log(df["q"].clip(lower=1e-12)))
    ent = g_transform_sum(qlog, df["race_id"])
    df["q_entropy_norm"] = ent / np.log(df["field_size"].clip(lower=2))
    df["n_fav_under_2"] = g_transform_sum((df["odds"] < 2.0).astype(float), df["race_id"])
    df["n_odds_under_10"] = g_transform_sum((df["odds"] < 10.0).astype(float), df["race_id"])
    df["is_fav"] = df["odds_rank"] == 1
    # first/last race of day at venue
    rn = df.groupby(["race_date", "venue_code"])["race_number"]
    df["is_last_race"] = df["race_number"] == rn.transform("max")
    df["is_first_race"] = df["race_number"] == rn.transform("min")
    # last3f rank within race (finished only)
    l3 = df["last_3f"].where(df["finished"])
    df["last3f_rank"] = l3.groupby(df["race_id"]).rank(method="min", ascending=True)
    df.loc[df["won"], "margin_sec"] = 0.0
    df["front_style"] = df["running_style"].isin(["逃げ", "先行"])
    # place hit (Harville field rule): >=8 → top3, 5-7 → top2, <=4 → no place bet
    fs = df["field_size"]
    df["place_hit"] = np.where(fs >= 8, df["finish_order"] <= 3,
                               np.where(fs >= 5, df["finish_order"] <= 2, np.nan)).astype(float)
    df["dead_heat"] = df["n_winners"] != 1

    # ---- horse history (strictly before; one start per day so shift is strictly-before)
    log("horse history …")
    df = df.sort_values(["horse_id", "race_date", "race_id"]).reset_index(drop=True)
    gh = df.groupby("horse_id", sort=False)
    df["career_starts"] = gh.cumcount().astype(float)
    df["career_wins"] = gh["won"].cumsum().astype(float) - df["won"].astype(float)
    top3 = (df["finish_order"] <= 3).astype(float)
    df["career_top3"] = top3.groupby(df["horse_id"]).cumsum() - top3
    df["career_win_rate"] = np.where(df["career_starts"] > 0, df["career_wins"] / df["career_starts"], np.nan)
    df["career_top3_rate"] = np.where(df["career_starts"] > 0, df["career_top3"] / df["career_starts"], np.nan)
    df["is_debut"] = df["career_starts"] == 0
    lag = lambda col, k=1: gh[col].shift(k)  # noqa: E731
    df["prev_finish"] = lag("finish_order")
    df["prev2_finish"] = lag("finish_order", 2)
    df["prev3_finish"] = lag("finish_order", 3)
    f1, f2, f3, f4, f5 = (lag("finish_order", k) for k in range(1, 6))
    df["avg_last3_finish"] = pd.concat([f1, f2, f3], axis=1).mean(axis=1)
    df["best_finish_last5"] = pd.concat([f1, f2, f3, f4, f5], axis=1).min(axis=1)
    w1, w2, w3, w4, w5 = (lag("won", k).astype(float) for k in range(1, 6))
    df["wins_last5"] = pd.concat([w1, w2, w3, w4, w5], axis=1).sum(axis=1, min_count=1)
    t3 = top3.groupby(df["horse_id"])
    df["top3_last5"] = pd.concat([t3.shift(k) for k in range(1, 6)], axis=1).sum(axis=1, min_count=1)
    df["prev_popularity"] = lag("popularity")
    df["prev_odds"] = lag("odds")
    df["prev_q"] = lag("q")
    df["prev_field_size"] = lag("field_size")
    df["prev_finish_pct"] = df["prev_finish"] / df["prev_field_size"]
    df["prev_beat_market"] = df["prev_popularity"] - df["prev_finish"]
    df["prev_distance"] = lag("distance")
    df["prev_track_type"] = lag("track_type")
    df["prev_venue_code"] = lag("venue_code")
    df["prev_class_canon"] = lag("race_class_canon")
    df["dist_change"] = df["distance"] - df["prev_distance"]
    df["class_change"] = np.sign(df["class_rank"] - lag("class_rank"))
    prev_date = lag("race_date")
    df["days_since_last"] = (df["race_date"] - prev_date).dt.days.astype(float)
    prev2_date = lag("race_date", 2)
    df["tataki_2"] = np.where(prev2_date.isna(), np.nan, ((prev_date - prev2_date).dt.days > 70).astype(float))
    df["prev_weight"] = lag("weight")
    df["weight_change_vs_prev"] = df["weight"] - df["prev_weight"]
    df["prev_running_style"] = lag("running_style")
    df["prev_last3f_rank"] = lag("last3f_rank")
    df["prev_margin_sec"] = lag("margin_sec")
    df["last_won"] = lag("won").astype(float)
    df["jockey_change"] = np.where(lag("jockey_id").isna(), np.nan,
                                   (df["jockey_id"] != lag("jockey_id")).astype(float))

    # ---- jockey / trainer / combo as-of
    log("jockey/trainer as-of …")
    df["combo_id"] = df["jockey_id"].astype(str) + "|" + df["trainer_id"].astype(str)
    for key, pref in (("jockey_id", "jockey"), ("trainer_id", "trainer"), ("combo_id", "combo")):
        daily = rolling_window_counts(df[[key, "race_date", "won", "q"]], key)
        daily = daily.rename(columns={"all_n": f"{pref}_starts_all", "all_w": f"{pref}_wins_all",
                                      "win_n": f"{pref}_starts_365", "win_w": f"{pref}_wins_365",
                                      "all_q": f"{pref}_q_all", "win_q": f"{pref}_q_365"})
        df = df.merge(daily, on=[key, "race_date"], how="left")
        if pref in ("jockey", "trainer"):
            # market-excess form (codex 2026-09-23): wins − Σq over the same rides = 市場期待を上回った勝ち数
            df[f"{pref}_excess_365"] = df[f"{pref}_wins_365"] - df[f"{pref}_q_365"]
            df[f"{pref}_excess_all"] = df[f"{pref}_wins_all"] - df[f"{pref}_q_all"]
        df[f"{pref}_win_rate_all"] = np.where(df[f"{pref}_starts_all"] > 0,
                                              df[f"{pref}_wins_all"] / df[f"{pref}_starts_all"], np.nan)
        df[f"{pref}_win_rate_365"] = np.where(df[f"{pref}_starts_365"] > 0,
                                              df[f"{pref}_wins_365"] / df[f"{pref}_starts_365"], np.nan)
    # same-day, same-venue: jockey wins in earlier races
    df = df.sort_values(["race_date", "venue_code", "race_number", "race_id", "horse_number"]).reset_index(drop=True)
    gj = df.groupby(["race_date", "venue_code", "jockey_id"], sort=False)["won"]
    df["jockey_wins_today_before"] = (gj.cumsum() - df["won"]).astype(float)
    df["jockey_rides_today_before"] = gj.cumcount().astype(float)

    # ---- same-day dynamics at race level (earlier races, same venue)
    log("same-day dynamics …")
    races = (df.groupby(["race_date", "venue_code", "race_number", "race_id"], sort=True)
               .agg(n=("won", "size")).reset_index())
    win_rows = df[df["won"] & (df["n_winners"] == 1)][["race_id", "popularity", "front_style", "is_fav"]]
    win_rows = win_rows.drop_duplicates("race_id").set_index("race_id")
    races["fav_won"] = races["race_id"].map(win_rows["is_fav"]).astype(float)
    races["winner_pop"] = races["race_id"].map(win_rows["popularity"]).astype(float)
    races["winner_front"] = races["race_id"].map(win_rows["front_style"]).astype(float)
    grp = races.groupby(["race_date", "venue_code"], sort=False)
    races["day_prev_n"] = grp.cumcount().astype(float)
    for col, out in (("fav_won", "day_prev_fav_win_rate"), ("winner_pop", "day_prev_winner_pop_mean"),
                     ("winner_front", "day_prev_winner_style_front_share")):
        cs = grp[col].cumsum() - races[col].fillna(0.0)
        cnt = grp[col].transform(lambda s: s.notna().cumsum()) - races[col].notna().astype(float)
        races[out] = np.where(cnt > 0, cs / cnt.replace(0, np.nan), np.nan)
    df = df.merge(races[["race_id", "day_prev_n", "day_prev_fav_win_rate", "day_prev_winner_pop_mean",
                         "day_prev_winner_style_front_share"]], on="race_id", how="left")

    # ---- model OOF (108 bundle)
    log("model OOF bundle …")
    b = json.loads(BUNDLE.read_text())
    recs = []
    for rid, horses in b["predictions"].items():
        for hid, v in horses.items():
            recs.append((rid, hid, float(v["win"]), float(v["top2"]), float(v["top3"])))
    mp = pd.DataFrame(recs, columns=["race_id", "horse_id", "p", "p_top2", "p_top3"])
    del b, recs
    df = df.merge(mp, on=["race_id", "horse_id"], how="left")
    df["in_bundle"] = df["race_id"].isin(set(mp["race_id"]))
    gm = df.groupby("race_id", sort=False)
    order = df.sort_values(["race_id", "p", "horse_number"], ascending=[True, False, True]).index
    prank = np.full(len(df), np.nan)
    prank[order] = df.loc[order].groupby("race_id", sort=False).cumcount().to_numpy() + 1
    df["p_rank"] = np.where(df["p"].notna(), prank, np.nan)
    df["ev"] = df["p"] * df["odds"]
    df["p_over_q"] = df["p"] / df["q"]
    p1 = df[df["p_rank"] == 1].set_index("race_id")["p"]
    p2 = df[df["p_rank"] == 2].set_index("race_id")["p"]
    df["p_gap12"] = df["race_id"].map(p1).astype(float) - df["race_id"].map(p2).astype(float)
    mf = df[df["p_rank"] == 1].set_index("race_id")["is_fav"]
    df["model_fav_is_market_fav"] = df["race_id"].map(mf).astype(float)

    # ---- final ordering / dtypes / save
    df = df.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    drop_cols = ["result_status", "finished", "class_rank", "front_style", "last3f_rank", "combo_id",
                 "jockey_wins_all", "trainer_wins_all", "combo_wins_all", "jockey_wins_365",
                 "trainer_wins_365", "combo_wins_365", "jockey_q_all", "trainer_q_all", "combo_q_all",
                 "jockey_q_365", "trainer_q_365", "combo_q_365", "margin_sec", "last_3f", "running_style",
                 "prize_money"]
    df["prize_money"] = pd.to_numeric(df["prize_money"], errors="coerce")
    keep_prize = df["prize_money"].copy()
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])
    df["prize_money"] = keep_prize
    df["race_date"] = df["race_date"].dt.strftime("%Y-%m-%d")
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(object).where(df[c].notna(), None)
    df.to_parquet(OUT / "rows.parquet", index=False)
    log(f"saved rows.parquet {df.shape}")

    # ---- dividends + quotes
    log("dividends / quotes …")
    with create_engine(DB_URL).connect() as c:
        dv = pd.read_sql(text("select race_id, bet_type, selection::text as selection, odds from exotic_odds"), c)
        qt = pd.read_sql(text("select q.race_id, q.bet_type, q.quotes::text as quotes, q.official_at, r.post_time "
                              "from exotic_quotes q join races r using(race_id)"), c)
    dv["odds"] = dv["odds"].astype(float)
    dv["selection"] = [json.dumps(sorted(json.loads(s)) if bt in ("place", "quinella", "wide", "trio") else json.loads(s))
                       for s, bt in zip(dv["selection"], dv["bet_type"])]
    dv.to_parquet(OUT / "dividends.parquet", index=False)
    rows = []
    for rid, bt, qs, off, post in zip(qt["race_id"], qt["bet_type"], qt["quotes"], qt["official_at"], qt["post_time"]):
        d = json.loads(qs)
        for k, v in d.items():
            rows.append((rid, bt, k, v[0], v[1], v[2], str(off), str(post)))
    pd.DataFrame(rows, columns=["race_id", "bet_type", "combo", "quote_low", "quote_high", "quote_pop",
                                "official_at", "post_time"]).to_parquet(OUT / "quotes.parquet", index=False)
    rep = {
        "rows": int(len(df)), "races": int(df.race_id.nunique()),
        "years": [int(df.year.min()), int(df.year.max())],
        "race_ok_rate": float(df.race_ok.mean()), "dead_heat_races": int(df[df.dead_heat].race_id.nunique()),
        "in_bundle_races": int(df[df.in_bundle].race_id.nunique()),
        "columns": list(df.columns), "dividend_rows": int(len(dv)), "quote_rows": int(len(rows)),
        "elapsed_s": round(time.time() - t0, 1),
    }
    (OUT / "build_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    log(f"done in {rep['elapsed_s']}s")
    return 0


def g_transform_sum(values: pd.Series, keys: pd.Series) -> pd.Series:
    return values.groupby(keys).transform("sum")


if __name__ == "__main__":
    sys.exit(main())
