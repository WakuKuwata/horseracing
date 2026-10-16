import pandas as pd
from sqlalchemy import create_engine, text
eng=create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
def q(sql, **kw):
    with eng.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        return pd.read_sql(text(sql), c, params=kw)
pd.set_option('display.width',250); pd.set_option('display.max_columns',50); pd.set_option('display.max_rows',300)
print("## exotic_quotes PRE-post only: minutes before post, by bet_type/month")
print(q("""select bet_type, date_trunc('month', r.race_date)::date m, count(*) n_pre,
 count(*) filter (where extract(epoch from (r.post_time-e.official_at))<=5*60) le5m,
 count(*) filter (where extract(epoch from (r.post_time-e.official_at))<=10*60) le10m,
 count(*) filter (where extract(epoch from (r.post_time-e.official_at))<=30*60) le30m,
 round(percentile_cont(0.5) within group (order by extract(epoch from (r.post_time-e.official_at))/60.0)::numeric,1) med_min
 from exotic_quotes e join races r on r.race_id=e.race_id where r.post_time is not null and e.official_at < r.post_time group by 1,2 order by 2,1"""))
print("## netkeiba official_at -> observed_at lag (seconds), 2026")
print(q("""select percentile_cont(array[0.1,0.5,0.9,0.99]) within group (order by extract(epoch from (observed_at-official_at))) from exotic_quotes e join races r on r.race_id=e.race_id where r.race_date>='2026-01-01'"""))
print("## dead heats for 1st place since 2007 (races, share)")
print(q("""select count(*) races, sum(dh) dead_heat_races, round(100.0*sum(dh)/count(*),3) pct from (select rr.race_id, case when count(*) filter (where finish_order=1)>1 then 1 else 0 end dh from race_results rr join races r on r.race_id=rr.race_id where r.race_date>='2007-01-01' group by rr.race_id) t"""))
print("## cancelled entries that still carry odds (would be refunded if bought) 2024+")
print(q("""select extract(year from r.race_date)::int y, count(*) filter (where entry_status='cancelled') cancelled, count(*) filter (where entry_status='cancelled' and odds is not null) cancelled_with_odds, count(*) filter (where entry_status='started') started from race_horses rh join races r on r.race_id=rh.race_id where r.race_date>='2024-01-01' group by 1 order by 1"""))
print("## attention_checkpoints")
print(q("select column_name,data_type from information_schema.columns where table_name='attention_checkpoints' order by ordinal_position"))
print(q("select count(*) from attention_checkpoints"))
print("## picks v2 since 2026-10-05 (prospective, official settlement)")
print(q("select count(*) n, count(distinct race_id) races, min(computed_at), max(computed_at) from attention_picks where computed_at>='2026-10-05'"))
