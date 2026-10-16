import pandas as pd, numpy as np
from sqlalchemy import create_engine, text
e = create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
with e.connect() as c:
    c.execute(text("SET TRANSACTION READ ONLY"))
    cols = pd.read_sql(text("select column_name from information_schema.columns where table_name='races'"), c)
    print(sorted(cols.column_name))
    r = pd.read_sql(text("select race_id, race_date, post_time, place_odds_observed_at, place_odds_official_at as official_at from races where race_date >= '2026-01-01' and race_date <= '2026-10-07'"), c)
print("races 2026", len(r), "post_time notnull", r.post_time.notna().mean())
r = r.dropna(subset=["post_time"])
r["pt"] = pd.to_datetime(r.race_date.astype(str) + " " + r.post_time.astype(str))
g = r.groupby("race_date")
print("days", g.ngroups, "mean races/day", g.size().mean(), "min", g.size().min(), "max", g.size().max())
span = (g.pt.max() - g.pt.min()).dt.total_seconds()/3600
print("span h mean", span.mean(), "median", span.median(), "max", span.max())
gaps=[]
for d, grp in g:
    pts = np.sort(grp.pt.values)
    gaps += list(np.diff(pts).astype("timedelta64[s]").astype(float)/60)
gaps=np.array(gaps)
print("n gaps", len(gaps), "median", np.median(gaps), "p10", np.percentile(gaps,10), "<2min", (gaps<2).sum(), "<1min", (gaps<1).sum(), "==0", (gaps==0).sum(), "<3min", (gaps<3).sum(), "<5", (gaps<5).sum())
# freshness
f = r.dropna(subset=["place_odds_observed_at","official_at"]).copy()
f["obs"]=pd.to_datetime(f.place_odds_observed_at, utc=True); f["off"]=pd.to_datetime(f.official_at, utc=True)
f["d"]=(f.obs-f.off).dt.total_seconds()/60
print("obs rows", len(f), "pre (obs<off)", (f.d<0).sum(), "post", (f.d>=0).sum())
pre = -f.d[f.d<0]
print("pre: median", pre.median(), "p90", pre.quantile(.9), "max", pre.max(), "n", len(pre))
print(pre.describe())
# by date distribution of pre
print(f[f.d<0].groupby("race_date").size().sort_values(ascending=False).head(10))
