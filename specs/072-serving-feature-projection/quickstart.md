# Quickstart: validating serving-time as-of feature projection

Prereqs: local Postgres `horseracing` (aiuma/aiuma @ localhost:15432), `DATABASE_URL` exported.
Pick a real race with a started field, e.g. `202603020607`.

## 1. Per-block byte-parity (the adoption gate)

For the block under conversion (start with pace), prove projected == full-restricted on the real DB:

```python
# pseudocode — see contracts/projection.md
full = build_pace_features(frames)
proj = build_pace_features(frames, target_race_ids=frozenset({R}))
assert_frame_equal(full[full.race_id==R].sort_values(KEYS).reset_index(drop=True),
                   proj.sort_values(KEYS).reset_index(drop=True),
                   check_exact=True, check_dtype=True)   # MUST pass
```

Already measured for pace: full `_rolling_asof` 6.58s → projected 0.0049s, byte-identical (SC-005).
Already measured for human_form (cross-entity taxonomy): projected-by-entity == full on target rows, byte-identical.

## 2. Edge-case cohort (unit, DB-free)

Using `make_frames`, assert parity for: normal multi-history horse; debut (0 prior); low-history; a cancelled entry in the field; and the R3 case — a horse AND a jockey each appearing in two SAME-DAY races, both after prior finished appearances. All must be `check_exact` equal to the full build.

## 3. End-to-end prediction parity (active model)

```python
model = load_serving_model(session, None)          # lgbm-063
full = build_feature_matrix(session, end_date=DATE, wanted=frozenset(model.feature_cols))
proj = build_feature_matrix(session, end_date=DATE, wanted=frozenset(model.feature_cols),
                            target_race_ids=frozenset({R}))
pf,_,_ = predict_race(model, R, full); pp,_,_ = predict_race(model, R, proj)
# win/top2/top3 byte-identical, max|Δ| == 0.0   (SC-003)
```

## 4. Non-serving consumers unchanged

```bash
cd features && uv run python -m pytest tests -q     # 281+ pass, incl. test_asof_real_db / test_repair_parity
```
Materialize / training / backfill pass `target_race_ids=None` → byte-unchanged (SC-004). The 025 real-DB parity test is the guard.

## 5. Timing (goal)

Time `build_feature_matrix(..., target_race_ids={R})` for the active model on the real DB; expect the converted blocks to collapse to ~ms and the total to move from ~43s toward ~10–15s as blocks land (floor = `load_frames` ~6s + race-level primitives). Record per-block wall-clock in the phase's PR.

## Expected outcomes

- Every converted block: zero mismatches vs the full build on target rows.
- Active-model predictions: byte-identical.
- features/serving/training suites: green.
- Single-race cold build materially faster, staged per block.
