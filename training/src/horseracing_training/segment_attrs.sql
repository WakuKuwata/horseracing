-- Feature 082: result-blind attributes per (race_id, horse_id) for mask assignment.
-- Everything here is either a static race/horse attribute or a strictly-before lag value
-- (a prior race's own result is strictly before the target race => result-blind for the
-- target). The target race's own result NEVER appears. q is the closing-leaning vote share
-- computed ONLY when the complete started field has valid odds (market-complete; else NULL).
WITH started AS (
  SELECT rh.race_id, rh.horse_id, rh.sex, rh.horse_number, rh.weight, rh.weight_diff,
         rh.odds,
         r.race_date, r.venue_code, r.track_type, r.distance, r.race_class, r.going,
         EXTRACT(YEAR FROM r.race_date)::int AS year,
         EXTRACT(MONTH FROM r.race_date)::int AS month
  FROM race_horses rh JOIN races r ON r.race_id = rh.race_id
  WHERE rh.entry_status = 'started'
),
fld AS (
  SELECT race_id, count(*) AS field_size,
         count(*) FILTER (WHERE odds IS NULL OR odds <= 0) AS n_bad_odds,
         sum(1.0/NULLIF(odds, 0)) AS inv_sum
  FROM started GROUP BY race_id
),
lagged AS (
  SELECT s.*,
    lag(s.race_date)   OVER w AS pv_date,
    lag(s.race_date,2) OVER w AS pv2_date,
    lag(s.race_id)     OVER w AS pv_race_id,
    (row_number() OVER w) - 1 AS n_prior_starts,
    count(s.odds) FILTER (WHERE s.odds > 0)
      OVER (PARTITION BY s.horse_id ORDER BY s.race_date, s.race_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS n_prior_odds_obs
  FROM started s
  WINDOW w AS (PARTITION BY s.horse_id ORDER BY s.race_date, s.race_id)
)
SELECT l.race_id, l.horse_id,
  l.year, l.month, l.venue_code, l.track_type, l.distance, l.race_class,
  f.field_size,
  l.sex, l.weight, l.weight_diff,
  CASE WHEN l.track_type = 'ダ' THEN 'dirt'
       WHEN l.going = '良' THEN 'turf-firm' ELSE 'turf-off' END AS body_cell,
  CASE WHEN l.horse_number IS NULL OR f.field_size < 2 THEN NULL
       ELSE (l.horse_number - 1.0)/(f.field_size - 1.0) END AS draw_pct,
  (l.race_date - l.pv_date) AS days_since_last,
  (l.pv_date - l.pv2_date) AS prior_gap_days,
  rr.finish_order AS prev_finish,
  l.n_prior_starts, COALESCE(l.n_prior_odds_obs, 0) AS n_prior_odds_obs,
  CASE WHEN f.n_bad_odds = 0 AND f.inv_sum > 0
       THEN (1.0/l.odds)/f.inv_sum END AS q
FROM lagged l
JOIN fld f USING (race_id)
LEFT JOIN race_results rr ON rr.race_id = l.pv_race_id AND rr.horse_id = l.horse_id;
