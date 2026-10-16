import json, pandas as pd
from sqlalchemy import create_engine, text
e=create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
ROOT="/Users/kuwatawaku/workspace/horseracing/"
u=json.load(open(ROOT+"artifacts/roi_comprehensive_20261005/calibration_roi/underround_sensitivity.json"))
rm=u["race_metadata"]
print(type(rm), (list(rm.keys())[:3] if isinstance(rm,dict) else rm[:2]))
with e.connect() as c:
    c.execute(text("SET TRANSACTION READ ONLY"))
    # official payouts coverage by month
    print(pd.read_sql(text("select date_trunc('month',r.race_date)::date m, count(distinct o.race_id) n_races from official_win_payouts o join races r using(race_id) group by 1 order by 1"),c))
    # 2026 flat races with results per month and how many have payouts
    print(pd.read_sql(text("""select date_trunc('month',r.race_date)::date m, count(distinct r.race_id) races_with_result,
      count(distinct o.race_id) with_payout from races r join race_results rr using(race_id) left join official_win_payouts o using(race_id)
      where r.race_date>='2025-10-01' group by 1 order by 1"""),c))
    # q-only 8 races: cancelled horses odds
    ids=[k for k in (rm.keys() if isinstance(rm,dict) else [x['race_id'] for x in rm])][:40]
    q=text("""select rh.race_id, r.race_date, rh.horse_number, rh.entry_status, rh.odds from race_horses rh join races r using(race_id)
      where rh.race_id = any(:ids) order by rh.race_id, rh.horse_number""")
    df=pd.read_sql(q,c,params={"ids":list(ids)})
    g=df.groupby("race_id").apply(lambda x: pd.Series({"date":x.race_date.iloc[0],"S_started":(1/x.loc[x.entry_status=='started','odds']).sum(),"S_all":(1/x.odds).sum(),"n_cancel":(x.entry_status=='cancelled').sum(),"cancel_odds":x.loc[x.entry_status=='cancelled','odds'].tolist()}))
    print(g.sort_values("date").to_string())
