import pandas as pd, sqlalchemy as sa
eng=sa.create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
with eng.connect() as c:
    c.execute(sa.text("SET TRANSACTION READ ONLY"))
    q=lambda s: pd.read_sql(sa.text(s),c)
    print(q("select race_id,race_date,post_time,venue_code,race_number from races where race_id='202604020609'").to_string())
    print(q("select horse_number,horse_id,odds,popularity,entry_status,updated_at from race_horses where race_id='202604020609' order by horse_number").to_string())
    print(q("select horse_id,finish_order,status from race_results where race_id='202604020609' and finish_order<=3 order by finish_order").to_string())
    print("official payouts for race:", q("select * from official_win_payouts where race_id='202604020609'").to_string())
    print(q("select count(*) n, min(r.race_date), max(r.race_date) from official_win_payouts p join races r using(race_id)").to_string())
    print("payout coverage by date 08-02/08-09/08-15:", q("select r.race_date, count(distinct r.race_id) races, count(distinct p.race_id) with_payout from races r left join official_win_payouts p using(race_id) where r.race_date in ('2026-08-02','2026-08-09','2026-08-15','2026-08-16') group by 1 order by 1").to_string())
    print("odds updated_at distribution 08-09:", q("select date_trunc('hour',updated_at) h, count(*) from race_horses rh join races r using(race_id) where r.race_date='2026-08-09' group by 1 order by 1").to_string())
    print("jobs around 08-09..08-12:", q("select job_type,status,created_at,summary::text from ingestion_jobs where created_at between '2026-08-09' and '2026-08-13' and job_type in ('results','odds','entries','refresh_range','final_odds') order by created_at").head(40).to_string())
