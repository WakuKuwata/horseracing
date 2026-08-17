"""過去市場特徴(058/F02-F05)が読む生列に、供給元切替で何が起きたかの監査。

なぜ: 070 の 3 バンドルは全体では -0.0015〜-0.0023 だが 2026 単独では -0.0044〜-0.0066 と
2〜4 倍。機構があるのか雑音なのかで次の一手が変わる。**この監査で何も変わっていなければ
その線は閉じる。** 変わっていれば 091 型のレジームリプレイ(58 日の部分集団ではなく全 821 日で
同じ問いに答える)を設計する根拠になる。

脚質のときと同じ手順である: 列名が同じでも意味が同じとは限らないので、分布を年別に出して
比較する([[source-cutover-feature-decay]] で `running_style` はこれで初めて見つかった)。

読む生列は `race_horses.odds` と `race_horses.popularity` の 2 つだけ。過去市場特徴はこれを
strictly-before で as-of 集約したものなので、この 2 列の意味が変われば特徴の意味も変わる。

見る指標:
  1. カバレッジ            — 非欠損率。単純に取れているか
  2. 過剰ラウンド Σ(1/odds) — **オッズの「種類」の指紋**。JRA の単勝控除率 20% なら確定オッズで
                              約 1.25。朝のオッズや別段階のオッズだと系統的にずれる
  3. rank(odds) == popularity — 2 列の内部整合。片方が別由来なら崩れる
  4. max(popularity) == 出走頭数 — popularity が全出走馬に振られているか
  5. 1番人気の勝率          — 市場の質そのもの。オッズの取得時点が変われば動く

使い方:
    cd training && uv run python ../scripts/past_market_source_audit.py
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, text

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
FROM = "2019-01-01"


def load(engine) -> pd.DataFrame:
    sql = text("""
        SELECT r.race_id, r.race_date, rh.horse_id, rh.odds, rh.popularity,
               (rr.result_status = 'finished' AND rr.finish_order = 1)::int AS is_win
        FROM race_horses rh
        JOIN races r ON r.race_id = rh.race_id
        LEFT JOIN race_results rr
               ON rr.race_id = rh.race_id AND rr.horse_id = rh.horse_id
        WHERE rh.entry_status = 'started' AND r.race_date >= :a
    """)
    df = pd.read_sql(sql, engine, params={"a": FROM})
    df["year"] = pd.to_datetime(df["race_date"]).dt.year
    df["odds"] = pd.to_numeric(df["odds"], errors="coerce")
    return df


def main() -> None:
    df = load(create_engine(DB))
    print(f"対象: {len(df):,} 行 / {df['race_id'].nunique():,} レース / {FROM} 以降\n")

    # --- 1. カバレッジ ---------------------------------------------------------------
    print("【1】カバレッジ(started 行に対する非欠損率)")
    cov = df.groupby("year").agg(
        rows=("horse_id", "size"),
        odds=("odds", lambda s: 100 * s.notna().mean()),
        popularity=("popularity", lambda s: 100 * s.notna().mean()),
    ).round(1)
    print(cov.to_string(), "\n")

    # --- 2. 過剰ラウンド = オッズの種類の指紋 ------------------------------------------
    # 完全な出走全馬にオッズがあるレースだけで測る(欠損があると総和が過小になる)
    valid = df[df["odds"].notna() & (df["odds"] > 0)]
    per_race = valid.groupby(["year", "race_id"]).agg(
        n_with_odds=("odds", "size"), inv=("odds", lambda s: (1.0 / s).sum())
    )
    field = df.groupby(["year", "race_id"]).size().rename("n_started")
    per_race = per_race.join(field)
    full = per_race[per_race["n_with_odds"] == per_race["n_started"]]
    print("【2】過剰ラウンド Σ(1/odds) — 全出走馬にオッズがあるレースのみ")
    print("     (単勝控除率 20% の確定オッズなら約 1.25。ずれ = 取得時点/種類が違う)")
    ov = full.groupby("year")["inv"].agg(
        races="size", median="median", p10=lambda s: s.quantile(0.10),
        p90=lambda s: s.quantile(0.90),
    ).round(4)
    print(ov.to_string(), "\n")

    # --- 3. rank(odds) と popularity の整合 --------------------------------------------
    print("【3】rank(odds) == popularity の一致率(両方ある行のみ・同値は除外)")
    both = df[df["odds"].notna() & df["popularity"].notna() & (df["odds"] > 0)].copy()
    both["rank_by_odds"] = both.groupby("race_id")["odds"].rank(method="min").astype(int)
    # 同オッズの馬がいるレースは順位が一意に決まらないので除外
    dup = both.groupby("race_id")["odds"].transform(lambda s: s.duplicated(keep=False))
    uniq = both[~dup]
    agree = uniq.groupby("year").apply(
        lambda g: pd.Series({
            "rows": len(g),
            "一致率%": round(100 * (g["rank_by_odds"] == g["popularity"]).mean(), 2),
        }), include_groups=False)
    print(agree.to_string(), "\n")

    # --- 4. popularity が全出走馬を覆っているか ----------------------------------------
    print("【4】max(popularity) == 出走頭数 のレース割合")
    pm = df.groupby(["year", "race_id"]).agg(
        n_started=("horse_id", "size"), max_pop=("popularity", "max"),
        n_pop=("popularity", "count"))
    ok = pm.assign(ok=(pm["max_pop"] == pm["n_started"]) & (pm["n_pop"] == pm["n_started"]))
    print(ok.groupby("year")["ok"].agg(races="size", 覆っている率=lambda s: round(100*s.mean(), 1))
          .to_string(), "\n")

    # --- 5. 市場の質そのもの -----------------------------------------------------------
    print("【5】1番人気の勝率(市場の質。オッズの取得時点が変われば動く)")
    fav = df[(df["popularity"] == 1) & df["is_win"].notna()]
    q = fav.groupby("year").agg(
        n=("is_win", "size"), 勝率=("is_win", lambda s: round(100 * s.mean(), 2)))
    print(q.to_string(), "\n")

    # --- 判定の目安 --------------------------------------------------------------------
    med = ov["median"]
    if len(med) >= 2:
        base = med.loc[med.index <= 2024].median()
        cur = med.loc[med.index >= 2026]
        if len(cur):
            d = float(cur.iloc[0]) - float(base)
            print(f"→ 過剰ラウンドの 2026 と 2024 以前の差: {d:+.4f}")
            print("  |差| が 0.01 未満ならオッズの種類は実質同じ = この線は閉じる。"
                  "\n  それ以上なら取得時点/種類が変わっている = レジームリプレイ設計の根拠。")


if __name__ == "__main__":
    main()
