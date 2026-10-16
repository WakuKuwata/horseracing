"""判断時点データの seconds_to_post 分布・R02 pairs の時間帯別 保持率と価格ずれ・捕捉設計の前提(2026-10-07・読み取りのみ)。

実行: cd training && uv run python <this file>
出力: 同ディレクトリ near_post_measurements.json
"""
import json, pathlib
import numpy as np, pandas as pd, sqlalchemy as sa

OUT = pathlib.Path(__file__).with_name("near_post_measurements.json")
DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
PAIRS = "/Users/kuwatawaku/workspace/horseracing/artifacts/roi_explore/missed_20261004/R02_odds_drift/pairs.parquet"
res = {}

e = sa.create_engine(DB)
with e.connect() as c:
    c.execute(sa.text("SET TRANSACTION READ ONLY"))
    q = lambda s: pd.read_sql(sa.text(s), c)
    cs = q("select race_id, seconds_to_post, status, capture_trigger from chaos_snapshots")
    s = cs.seconds_to_post.dropna()
    res["chaos_snapshots"] = {"n": int(len(cs)), "trigger": cs.capture_trigger.value_counts().to_dict(),
        "stp_min_quantiles": {str(k): round(v/60, 1) for k, v in s.quantile([.01,.05,.25,.5,.75]).items()},
        "n_le_seconds": {str(t): int((s <= t).sum()) for t in [120, 300, 600, 1800, 3600]}}
    ap = q("select race_id, seconds_to_post, result_pending_at_compute from attention_picks")
    pp = ap[ap.result_pending_at_compute == True]
    res["attention_picks"] = {"n": int(len(ap)), "pending_picks": int(len(pp)), "pending_races": int(pp.race_id.nunique()),
        "pending_n_le_seconds": {str(t): int((pp.seconds_to_post <= t).sum()) for t in [120, 300, 600, 1800, 3600]}}
    res["official_win_payouts"] = q("select count(*) n, count(distinct o.race_id) races, min(r.race_date)::text mn, max(r.race_date)::text mx from official_win_payouts o join races r on r.race_id=o.race_id").iloc[0].to_dict()
    res["exotic_odds_dividends"] = q("select bet_type, count(distinct x.race_id) races, min(r.race_date)::text mn from exotic_odds x join races r on r.race_id=x.race_id group by bet_type order by 1").to_dict("records")
    d = q("select post_time, place_odds_official_at, place_odds_observed_at from races where place_odds_official_at is not null")
    pre = d[(d.post_time - d.place_odds_observed_at).dt.total_seconds() > 0]
    lag = (pre.place_odds_observed_at - pre.place_odds_official_at).dt.total_seconds()/60
    prem = (pre.post_time - pre.place_odds_official_at).dt.total_seconds()/60
    res["place_quotes"] = {"races": int(len(d)), "pre_post": int(len(pre)),
        "api_staleness_min_quantiles_prepost": {str(k): round(v, 2) for k, v in lag.quantile([.1,.5,.9]).items()},
        "pre_post_n_le_min": {str(t): int((prem <= t).sum()) for t in [5, 10, 30, 60]}}
    x = q("select x.race_id, x.bet_type, max(x.observed_at) obs, r.post_time from exotic_quotes x join races r using(race_id) group by 1,2,4")
    x["pre"] = (x.post_time - x.obs).dt.total_seconds()/60
    res["exotic_quotes_pre_post_races"] = {bt: int((g.pre > 0).sum()) for bt, g in x.groupby("bet_type")}
    r = q("select race_date, post_time from races where race_date>='2026-01-01' and post_time is not null order by 1,2")
    rows = []
    for dte, g in r.groupby("race_date"):
        t = g.post_time.sort_values().reset_index(drop=True)
        gaps = t.diff().dt.total_seconds().dropna()/60
        rows.append({"n": len(t), "span_h": (t.max()-t.min()).total_seconds()/3600, "gaps_lt2": int((gaps < 2).sum()), "min_gap": gaps.min()})
    o = pd.DataFrame(rows)
    res["race_days_2026"] = {"days": int(len(o)), "races_per_day_mean": round(o.n.mean(), 1), "span_h_mean": round(o.span_h.mean(), 2),
        "post_gaps_lt2min_total": int(o.gaps_lt2.sum()), "min_gap_median_min": float(o.min_gap.median())}

df = pd.read_parquet(PAIRS)
df["mins"] = df.hours*60
one_pair = df.drop_duplicates("pair_id")
res["r02_pairs"] = {"pairs": int(len(one_pair)), "races": int(one_pair.race_id.nunique()), "days": int(one_pair.race_date.nunique()),
    "races_within_min": {str(t): int(one_pair.loc[one_pair.mins <= t, "race_id"].nunique()) for t in [1, 2, 5, 10, 30, 60]}}
df["hb"] = pd.cut(df.mins, [0,10,30,60,180,600,1440,2000])
band = []
for hb, g in df.groupby("hb", observed=True):
    row = {"band_min": str(hb), "races": int(g.race_id.nunique())}
    for s_ in ["S1", "S3"]:
        J = g[f"J_{s_}"].astype(bool); F = g[f"F_{s_}"].astype(bool)
        row[f"{s_}_J"] = int(J.sum()); row[f"{s_}_keep"] = round((J & F).sum()/max(J.sum(), 1), 3)
    lo = np.log(g.O_F/g.O_J).replace([np.inf, -np.inf], np.nan).dropna()
    row["logOdds_drift_sd"] = round(lo.std(), 3)
    row["EVratio_median"] = round((g.EVe_F/g.EVe_J).replace([np.inf,-np.inf],np.nan).dropna().median(), 3)
    band.append(row)
res["r02_by_band"] = band
one = df.sort_values("hours").drop_duplicates(["race_id", "horse_id"], keep="first")
dd = np.log(one.O_F/one.O_J)
res["r02_drift_picks_vs_rest"] = {s_: {"picks": int(one[f"J_{s_}"].sum()), "median_logdrift_picks": round(dd[one[f"J_{s_}"].astype(bool)].median(), 3),
    "median_logdrift_nonpicks": round(dd[~one[f"J_{s_}"].astype(bool)].median(), 3)} for s_ in ["S1", "S3"]}
OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
