import pandas as pd, sqlalchemy as sa
eng=sa.create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
with eng.connect() as c:
    c.execute(sa.text("SET TRANSACTION READ ONLY"))
    q=lambda s: pd.read_sql(sa.text(s),c)
    print(q("select column_name from information_schema.columns where table_name='race_results'").column_name.tolist())
    print(q("select column_name from information_schema.columns where table_name='ingestion_jobs'").column_name.tolist())
    print(q("select horse_id,finish_order,result_status from race_results where race_id='202604020609' order by finish_order nulls last limit 4").to_string())
    print("official payouts for race:", len(q("select * from official_win_payouts where race_id='202604020609'")))
    print(q("select count(*) n, min(r.race_date) mn, max(r.race_date) mx from official_win_payouts p join races r using(race_id)").to_string())
    print(q("select r.race_date, count(distinct r.race_id) races, count(distinct p.race_id) with_payout from races r left join official_win_payouts p using(race_id) where r.race_date in ('2026-08-02','2026-08-09','2026-08-15','2026-08-16','2026-10-04') group by 1 order by 1").to_string())
    print("odds updated_at by date for 08-02/08-09 races:", q("select r.race_date, date_trunc('hour',rh.updated_at) h, count(*) from race_horses rh join races r using(race_id) where r.race_date in ('2026-08-02','2026-08-09') group by 1,2 order by 1,2").to_string())
    print(q("select job_type,status,started_at,finished_at,left(summary::text,200) s from ingestion_jobs where started_at between '2026-08-12 08:00' and '2026-08-12 11:00' order by started_at").to_string())
