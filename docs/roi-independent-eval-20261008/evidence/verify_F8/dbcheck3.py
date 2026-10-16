import pandas as pd, sqlalchemy as sa, numpy as np
ROOT="/Users/kuwatawaku/workspace/horseracing/"
eng=sa.create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
with eng.connect() as c:
    c.execute(sa.text("SET TRANSACTION READ ONLY"))
    q=lambda s: pd.read_sql(sa.text(s),c)
    print(q("select job_type,status,scope,scope_value,started_at,completed_at,left(summary::text,160) s from ingestion_jobs where started_at between '2026-08-12 01:00' and '2026-08-12 12:00' order by started_at").to_string())
    # plausibility: on archive-covered dates, stored odds updated after post_time vs result page odds
    rh=q("""select rh.race_id,rh.horse_number,rh.odds stored_now,rh.updated_at,r.post_time,r.race_date from race_horses rh join races r using(race_id) where r.race_date between '2026-08-15' and '2026-10-04' and rh.entry_status='started'""")
a=pd.read_csv(ROOT+"artifacts/roi_explore/missed_20261004/R05_settlement/affected_races.csv",dtype={"race_id":str})
print("affected cols",a.columns.tolist())
print(a[a.race_id=="202604020609"].to_string())
if "race_date" in a:
    g=a[a.race_date.isin(["2026-08-02","2026-08-09"])]
    print(g.groupby("race_date").agg({c:"sum" for c in g.columns if g[c].dtype==bool or c in("affected","correctable")} | {"race_id":"size"}).to_string())
arc=pd.read_csv(ROOT+"artifacts/roi_explore/missed_20261004/R05_settlement/archived_final_odds.csv",dtype={"race_id":str,"horse_id":str})
print("archive dates",arc.race_date.min(),arc.race_date.max(),"rows",len(arc),"differs sum",arc.differs.sum(),"affected races",arc.loc[arc.affected.astype(bool),"race_id"].nunique())
# stored_odds (pre-repair, as archived in R05) vs result_page_odds, by whether the DB updated_at was after post_time
m=arc.merge(rh,on=["race_id","horse_number"],how="inner")
m["after_post"]=m.updated_at>m.post_time
m["eq"]=np.isclose(m.stored_odds,m.result_page_odds)
print(m.groupby("after_post").agg(n=("eq","size"),eq_rate=("eq","mean")).to_string())
print("note: stored_odds in csv is pre-139-repair value; stored_now is DB after repair. eq(now,result_page):",np.isclose(m.stored_now,m.result_page_odds).mean())
