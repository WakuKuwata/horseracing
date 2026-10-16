import pandas as pd, json
from sqlalchemy import create_engine, text
eng=create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
def q(sql, **kw):
    with eng.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        return pd.read_sql(text(sql), c, params=kw)
pd.set_option('display.width',250); pd.set_option('display.max_columns',50); pd.set_option('display.max_rows',300)
print("## attention_picks prospective only (result_pending_at_compute)")
print(q("""select kind, count(*) n, count(distinct race_id) races,
 count(*) filter (where seconds_to_post<=300) le5m, count(*) filter (where seconds_to_post<=600) le10m,
 count(*) filter (where seconds_to_post<=1800) le30m, count(*) filter (where seconds_to_post<=3600) le60m,
 percentile_cont(0.5) within group (order by seconds_to_post)/3600.0 med_h
 from attention_picks where result_pending_at_compute group by 1"""))
print("## exotic_quotes: official_at and observed_at vs post_time (2026)")
print(q("""select bet_type, count(*) n,
 count(*) filter (where official_at is not null) has_official,
 percentile_cont(array[0.1,0.25,0.5,0.75,0.9]) within group (order by extract(epoch from (r.post_time-e.official_at))/60.0) off_min_to_post_pct,
 count(*) filter (where extract(epoch from (r.post_time-e.official_at))<=10*60) le10m,
 count(*) filter (where extract(epoch from (r.post_time-e.official_at))<=30*60) le30m,
 count(*) filter (where extract(epoch from (r.post_time-e.official_at))<=60*60) le60m,
 count(*) filter (where e.official_at>r.post_time) after_post
 from exotic_quotes e join races r on r.race_id=e.race_id where r.race_date>='2026-01-01' and r.post_time is not null group by 1"""))
print(q("""select e.race_id, e.bet_type, e.n_combinations, e.official_at, e.observed_at, r.post_time, e.source from exotic_quotes e join races r on r.race_id=e.race_id where r.race_date>='2026-09-01' order by e.observed_at desc limit 8"""))
row=q("select quotes from exotic_quotes e join races r on r.race_id=e.race_id where r.race_date>='2026-09-01' and bet_type='wide' limit 1")
js=row.iloc[0,0]; js=js if isinstance(js,(dict,list)) else json.loads(js)
print("wide quotes sample type",type(js), (list(js.items())[:3] if isinstance(js,dict) else js[:3]))
print("## chaos_snapshots field sample keys")
row=q("select field, n, seconds_to_post, capture_strength from chaos_snapshots where status='active' order by captured_at desc limit 1")
f=row.iloc[0,0]; f=f if isinstance(f,(dict,list)) else json.loads(f)
print(type(f), (f[:2] if isinstance(f,list) else {k:(v if not isinstance(v,list) else v[:2]) for k,v in f.items()}))
print(q("select capture_strength, count(*) from chaos_snapshots group by 1"))
print("## post_time congestion: races per day with another race's post within +-2min / +-5min (2026)")
print(q("""with p as (select race_id, race_date, post_time from races where race_date>='2026-01-01' and post_time is not null)
select count(*) races,
 count(*) filter (where exists (select 1 from p b where b.race_date=a.race_date and b.race_id<>a.race_id and abs(extract(epoch from (b.post_time-a.post_time)))<=120)) within2m,
 count(*) filter (where exists (select 1 from p b where b.race_date=a.race_date and b.race_id<>a.race_id and abs(extract(epoch from (b.post_time-a.post_time)))<=300)) within5m,
 count(*) filter (where exists (select 1 from p b where b.race_date=a.race_date and b.race_id<>a.race_id and abs(extract(epoch from (b.post_time-a.post_time)))<=60)) within1m
 from p a"""))
print(q("""select race_date, min(post_time) first_post, max(post_time) last_post, count(*) n from races where race_date>='2026-09-01' and post_time is not null group by 1 order by 1 limit 6"""))
print("## race_horses weight availability (started, 2026) and final odds")
print(q("""select count(*) n, count(weight) w, count(odds) o from race_horses rh join races r on r.race_id=rh.race_id where r.race_date>='2026-01-01' and rh.entry_status='started'"""))
print("## races with results but no official payout 2026-07-04..08-09 and 06-27..07-03")
print(q("""select count(*) from races r join race_results rr on rr.race_id=r.race_id left join official_win_payouts o on o.race_id=r.race_id where r.race_date between '2026-06-27' and '2026-08-09' and o.race_id is null"""))
print(q("""select count(distinct r.race_id) from races r join race_results rr on rr.race_id=r.race_id left join official_win_payouts o on o.race_id=r.race_id where r.race_date between '2026-06-27' and '2026-08-09' and o.race_id is null"""))
