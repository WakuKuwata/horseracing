-- Feature 081 candidate features per (race_id, horse_id), as-of / strictly-before.
-- Emits the 8 pre-registered candidates (gate_config_hash c696cec79c95). All lag columns are
-- strictly-before by window ordering; same-day exclusion is intrinsic (a horse's prior race is a
-- different, earlier race). Output is JOINed to OOF p in Python. NaN encoded as NULL.
WITH started AS (
  SELECT rh.race_id, rh.horse_id, rh.sex, rh.weight, rh.weight_diff, rh.horse_number,
         r.race_date, r.venue_code, r.track_type, r.going,
         (SELECT count(*) FROM race_horses x WHERE x.race_id=rh.race_id AND x.entry_status='started') AS field_size,
         EXTRACT(DOY FROM r.race_date)::float AS doy,
         (EXTRACT(YEAR FROM r.race_date)::int % 4 = 0
          AND (EXTRACT(YEAR FROM r.race_date)::int % 100 <> 0
               OR EXTRACT(YEAR FROM r.race_date)::int % 400 = 0)) AS is_leap
  FROM race_horses rh JOIN races r ON r.race_id=rh.race_id
  WHERE rh.entry_status='started' AND r.track_type IN ('芝','ダ')
),
lagged AS (
  SELECT s.*,
    lag(s.race_date)   OVER w AS pv_date,
    lag(s.race_date,2) OVER w AS pv2_date,
    lag(s.race_id)     OVER w AS pv_race_id
  FROM started s
  WINDOW w AS (PARTITION BY s.horse_id ORDER BY s.race_date, s.race_id)
),
with_pvfin AS (
  SELECT l.*, rr.finish_order AS pv_fin
  FROM lagged l
  LEFT JOIN race_results rr ON rr.race_id = l.pv_race_id AND rr.horse_id = l.horse_id
)
SELECT race_id, horse_id,
  -- doy fraction (leap-safe, continuous)
  (doy - 1) / (CASE WHEN is_leap THEN 366.0 ELSE 365.0 END) AS doy_frac,
  -- 1) tataki_2: prior race was a layoff (>70d) return
  CASE WHEN pv2_date IS NULL THEN NULL
       WHEN (pv_date - pv2_date) > 70 THEN 1.0 ELSE 0.0 END AS tataki_2,
  -- 2) prior_gap_log: log(1 + prior race's own gap)
  CASE WHEN pv2_date IS NULL THEN NULL
       ELSE ln(1 + (pv_date - pv2_date)) END AS prior_gap_log,
  -- 3) seasonal_sex: female × sin/cos(2π doy_frac)   (two columns)
  CASE WHEN sex IS NULL THEN NULL
       ELSE (CASE WHEN sex='牝' THEN 1.0 ELSE 0.0 END) * sin(2*pi()*(doy-1)/(CASE WHEN is_leap THEN 366.0 ELSE 365.0 END)) END AS seasonal_sex_sin,
  CASE WHEN sex IS NULL THEN NULL
       ELSE (CASE WHEN sex='牝' THEN 1.0 ELSE 0.0 END) * cos(2*pi()*(doy-1)/(CASE WHEN is_leap THEN 366.0 ELSE 365.0 END)) END AS seasonal_sex_cos,
  -- 4) current_gap_shape: log(1+gap), hinge<14, hinge>70   (three columns)
  CASE WHEN pv_date IS NULL THEN NULL ELSE ln(1 + (race_date - pv_date)) END AS gap_log,
  CASE WHEN pv_date IS NULL THEN NULL ELSE greatest(0, 14 - (race_date - pv_date)) END AS gap_hinge_short,
  CASE WHEN pv_date IS NULL THEN NULL ELSE greatest(0, (race_date - pv_date) - 70) END AS gap_hinge_long,
  -- 5) prev_finish_reversion: 1[2..3], 1[6..9]   (two columns)
  CASE WHEN pv_fin IS NULL THEN NULL WHEN pv_fin BETWEEN 2 AND 3 THEN 1.0 ELSE 0.0 END AS prev_fin_2_3,
  CASE WHEN pv_fin IS NULL THEN NULL WHEN pv_fin BETWEEN 6 AND 9 THEN 1.0 ELSE 0.0 END AS prev_fin_6_9,
  -- 6) draw_venue: horse_number percentile (interaction with venue×surface done in Python via cell)
  CASE WHEN horse_number IS NULL OR field_size < 2 THEN NULL
       ELSE (horse_number - 1.0)/(field_size - 1.0) END AS draw_pct,
  venue_code || ':' || track_type AS draw_cell,
  -- 7) body_mass_going: 1[weight<440] × going-cell   (interaction cell in Python)
  CASE WHEN weight IS NULL THEN NULL WHEN weight < 440 THEN 1.0 ELSE 0.0 END AS light_body,
  -- pre-registered cells: {turf-firm, turf-off, dirt} (dirt collapses going)
  CASE WHEN track_type='ダ' THEN 'dirt'
       WHEN going='良' THEN 'turf-firm' ELSE 'turf-off' END AS body_cell,
  -- 8) weight_gain: 1[weight_diff >= +11]
  CASE WHEN weight_diff IS NULL THEN NULL WHEN weight_diff >= 11 THEN 1.0 ELSE 0.0 END AS weight_gain,
  -- timing family tag
  weight IS NOT NULL AS has_weight
FROM with_pvfin;
