import pandas as pd
from sqlalchemy import create_engine, text
e=create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
with e.connect() as c:
    c.execute(text("SET TRANSACTION READ ONLY"))
    print(pd.read_sql(text("""select rh.race_id, r.race_date, r.post_time, rh.horse_number, rh.entry_status, rh.odds, rh.popularity, rh.updated_at, rr.finish_order
      from race_horses rh join races r using(race_id) left join race_results rr on rr.race_id=rh.race_id and rr.horse_id=rh.horse_id
      where rh.race_id in ('202604020609','202609040607') and (rr.finish_order<=3 or rh.horse_number in (15,9)) order by rh.race_id, rr.finish_order"""),c).to_string())
    print(pd.read_sql(text("select * from official_win_payouts where race_id in ('202604020609','202609040607')"),c).to_string())
    # odds update timestamps vs race date for 2026-08-02/08-09 races: fraction of rows whose odds updated_at is after race day
    print(pd.read_sql(text("""select r.race_date, count(*) n, sum(case when rh.updated_at::date > r.race_date then 1 else 0 end) upd_after_day,
       sum(case when rh.updated_at::date = r.race_date then 1 else 0 end) upd_same_day
       from race_horses rh join races r using(race_id) where r.race_date in ('2026-08-02','2026-08-09','2026-09-20') and rh.entry_status='started' group by 1 order by 1"""),c).to_string())
