import pandas as pd, numpy as np
from sqlalchemy import create_engine, text
e = create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
with e.connect() as c:
    c.execute(text("SET TRANSACTION READ ONLY"))
    r = pd.read_sql(text("select race_id, race_date, venue_code, post_time, place_odds_observed_at obs, place_odds_official_at off from races where race_date >= '2026-01-01' and place_odds_observed_at is not null"), c)
r["pt"] = pd.to_datetime(r.race_date.astype(str)+" "+r.post_time.astype(str)).dt.tz_convert("Asia/Tokyo")
r["obs"]=pd.to_datetime(r.obs, utc=True).dt.tz_convert("Asia/Tokyo"); r["off"]=pd.to_datetime(r.off, utc=True).dt.tz_convert("Asia/Tokyo")
print(r[["pt","obs","off"]].head(3))
r["stale_min"]=(r.obs-r.off).dt.total_seconds()/60
r["to_post_min"]=(r.pt-r.obs).dt.total_seconds()/60
pre = r[r.to_post_min>0]
print("n with obs", len(r), "pre-post", len(pre))
print("stale pre: median", pre.stale_min.median(), "p90", pre.stale_min.quantile(.9), "max", pre.stale_min.max(), "min", pre.stale_min.min())
print("to_post pre: median", pre.to_post_min.median(), "p10", pre.to_post_min.quantile(.1), "<=3min", (pre.to_post_min<=3).sum(), "<=10", (pre.to_post_min<=10).sum())
print(pre.groupby("race_date").size())
# stale within 10 min of post
near = pre[pre.to_post_min<=10]
print("near(<=10min) n", len(near), "stale median", near.stale_min.median(), "p90", near.stale_min.quantile(.9))
# same-time pairs
all_ = pd.read_sql(text("select race_id, race_date, venue_code, post_time from races where race_date >= '2026-01-01'"), e.connect())
dup = all_.groupby(["race_date","post_time"]).size(); print("same post_time pairs", (dup>1).sum(), dup[dup>1].head(10))
