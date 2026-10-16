import pandas as pd
from sqlalchemy import create_engine, text
eng=create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
def q(sql, **kw):
    with eng.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        return pd.read_sql(text(sql), c, params=kw)
pd.set_option('display.width',250); pd.set_option('display.max_columns',50); pd.set_option('display.max_rows',300)
print("## chaos_snapshots seconds_to_post distribution (status, trigger)")
print(q("""select status, capture_trigger, count(*) n, min(captured_at)::date d0, max(captured_at)::date d1,
 count(*) filter (where seconds_to_post<=60) le1m,
 count(*) filter (where seconds_to_post<=120) le2m,
 count(*) filter (where seconds_to_post<=300) le5m,
 count(*) filter (where seconds_to_post<=600) le10m,
 count(*) filter (where seconds_to_post<=1800) le30m,
 count(*) filter (where seconds_to_post<=3600) le60m,
 count(*) filter (where seconds_to_post is null) null_stp,
 percentile_cont(0.5) within group (order by seconds_to_post)/3600.0 med_h
 from chaos_snapshots group by 1,2 order by 1,2"""))
print(q("""select percentile_cont(array[0.05,0.1,0.25,0.5,0.75,0.9]) within group (order by seconds_to_post) from chaos_snapshots where status='active'"""))
print("## chaos distinct races and days")
print(q("select count(distinct race_id) races, count(distinct captured_at::date) days from chaos_snapshots"))
print("## attention_picks seconds_to_post")
print(q("""select rule_set_version, kind, count(*) n, count(distinct race_id) races,
 count(*) filter (where seconds_to_post<=300) le5m, count(*) filter (where seconds_to_post<=600) le10m,
 count(*) filter (where seconds_to_post<=1800) le30m, count(*) filter (where seconds_to_post<=3600) le60m,
 count(*) filter (where seconds_to_post is null) null_stp,
 percentile_cont(0.5) within group (order by seconds_to_post)/3600.0 med_h,
 min(computed_at)::date d0, max(computed_at)::date d1
 from attention_picks group by 1,2 order by 1,2"""))
print("## attention_race_scans: first scan per race seconds_to_post")
print(q("""with f as (select race_id, min(computed_at) c0 from attention_race_scans where result_pending_at_compute group by 1)
select count(*) races, count(*) filter (where extract(epoch from (s.post_time - f.c0))<=600) le10m,
 count(*) filter (where extract(epoch from (s.post_time - f.c0))<=1800) le30m,
 count(*) filter (where extract(epoch from (s.post_time - f.c0))<=3600) le60m,
 percentile_cont(0.5) within group (order by extract(epoch from (s.post_time - f.c0))/3600.0) med_h
 from f join attention_race_scans s on s.race_id=f.race_id and s.computed_at=f.c0 where s.post_time is not null"""))
print("## market_ev_predictions: result_pending rows seconds to post (via races.post_time)")
print(q("""select count(distinct m.race_id) races,
 count(distinct m.race_id) filter (where extract(epoch from (r.post_time - m.odds_observed_at))<=600) le10m,
 count(distinct m.race_id) filter (where extract(epoch from (r.post_time - m.odds_observed_at))<=1800) le30m,
 count(distinct m.race_id) filter (where extract(epoch from (r.post_time - m.odds_observed_at))<=3600) le60m,
 count(distinct m.race_id) filter (where extract(epoch from (r.post_time - m.odds_observed_at))<0) after_post
 from market_ev_predictions m join races r on r.race_id=m.race_id where m.result_pending_at_compute and r.post_time is not null"""))
print("## official_win_payouts coverage")
print(q("""select count(*) rows, count(distinct race_id) races, min(race_id) r0, max(race_id) r1 from official_win_payouts"""))
print(q("""select date_trunc('month', r.race_date)::date m, count(distinct r.race_id) races_with_result,
 count(distinct o.race_id) races_with_payout
 from races r join race_results rr on rr.race_id=r.race_id left join official_win_payouts o on o.race_id=r.race_id
 where r.race_date>='2026-06-01' group by 1 order by 1"""))
print("## exotic_odds coverage by bet_type and month (confirmed dividends, 080)")
print(q("""select bet_type, date_trunc('month', r.race_date)::date m, count(distinct e.race_id) races, count(*) rows
 from exotic_odds e join races r on r.race_id=e.race_id group by 1,2 order by 2,1"""))
print("## exotic_quotes coverage")
print(q("select column_name,data_type from information_schema.columns where table_name='exotic_quotes' order by ordinal_position"))
print(q("""select bet_type, date_trunc('month', r.race_date)::date m, count(distinct e.race_id) races, count(*) rows
 from exotic_quotes e join races r on r.race_id=e.race_id group by 1,2 order by 2,1"""))
print("## purchase_records")
print(q("select kind, count(*) n, count(distinct race_id) races, min(recorded_at)::date d0, max(recorded_at)::date d1 from purchase_records group by 1"))
print("## post_time coverage by year, races per year (flat only?)")
print(q("""select extract(year from race_date)::int y, count(*) races, count(post_time) with_post_time, sum(case when track_type='障害' or track_type ilike '%jump%' or track_type='steeple' then 1 else 0 end) jump
 from races where race_date>='2007-01-01' group by 1 order by 1"""))
print(q("select track_type, count(*) from races where race_date>='2024-01-01' group by 1"))
print("## race days per year")
print(q("select extract(year from race_date)::int y, count(distinct race_date) days, round(count(*)::numeric/count(distinct race_date),1) races_per_day from races where race_date>='2023-01-01' group by 1 order by 1"))
