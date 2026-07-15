# Phase 1 Data Model: Serving-time as-of feature projection

No persisted schema. This feature adds one in-memory parameter and a parity contract. "Entities" here are the compute-time objects the projection reasons about.

## Entities

### `target_race_ids: frozenset[str] | None`
The restriction threaded through the build. `None` = full build (byte-parity oracle; materialize / training / backfill). A set of 12-digit JRA-VAN race ids = the race(s) serving is predicting.
- **Validation**: each id is an existing race id in `frames.races` (a non-existent id yields an empty target set, not an error — serving already guards race existence upstream).
- **Race-atomic**: a race id implies its entire started field; blocks never receive a partial field.

### Target field
Derived, not passed: `frames.race_horses[race_id ∈ target_race_ids & entry_status == started]`. The population the projected matrix emits. For per-horse blocks the source-filter key set = these horses' `horse_id`; for cross-entity blocks = these horses' `jockey_id` / `trainer_id` / `sire_name` / `owner_name` / `breeder_name`.

### As-of block (existing `build_*_features(frames)`)
Gains `target_race_ids: frozenset[str] | None = None`. Behavior:
- `None` → unchanged full output (**byte-parity oracle**).
- set → compute full-field race-level primitives over ALL past races, then restrict the per-key rolling / merge_asof / cumsum SOURCE to the target keys (R2 taxonomy) and emit only the target rows. Row & column order and dtypes match `full.loc[target_keys]` exactly.

### Parity oracle
`build_asof_features(frames)` / `assemble_feature_matrix(frames)` with `target_race_ids=None`. Every projected block is measured against this. The relation is an equality, not an improvement: `projected == oracle.loc[target_keys]` (`check_exact=True, check_dtype=True`).

## Invariants

- **INV-P1 (byte parity)**: for any block B and race set T, `B(frames, target_race_ids=T) == B(frames).loc[keys(T)]` byte-for-byte (values, dtype, row order, column order).
- **INV-P2 (oracle unchanged)**: `B(frames, target_race_ids=None)` is byte-identical to B before this feature (materialize/training/backfill see no change).
- **INV-P3 (leak boundary preserved)**: the projected path reads the same historical source semantics — strictly-before `race_date`, whole-current-day exclusion via unchanged daily-cumsum-minus-current-day, pedigree self-exclusion. Source is filtered by KEY, never by a `race_date < cutoff` shortcut (R3).
- **INV-P4 (race-atomic, structural)**: 031 / 059 receive the target race's full started field. This is guaranteed by construction — `target_race_ids` derives the whole started field from `race_horses`, so the public API cannot pass a partial field. The "partial field" case is an internal-helper contract only: a direct-call test truncates the field and shows the within-race function yields DIFFERENT values (documenting why the field must be whole), not a runtime rejection.
- **INV-P5 (composition)**: projection composes with `wanted=` leaf-skip — a block may be skipped entirely (leaf, unwanted) OR kept-and-projected; the two are orthogonal.
- **INV-P6 (no version drift)**: FEATURE_VERSION, `source_fingerprint`, materialized parquet, compat pins, load order, sort keys — all unchanged.

## State / flow

```
run_serving(race_id) / run_serving_backfill(day)
   └─ target_race_ids = {race_id}  |  {race ids of day}
        └─ build_feature_matrix(..., target_race_ids)
             └─ assemble_feature_matrix(..., target_race_ids)
                  ├─ build_static_features (current-race only; restrict to target field)
                  └─ build_asof_features(frames, skip_blocks=…, target_race_ids)
                       └─ each converted block(frames, target_race_ids)
                            ├─ full-field primitive over ALL past races
                            └─ per-key source filter → roll/merge_asof/cumsum → target rows
   (materialize / training / backfill pass target_race_ids=None → full build, byte-unchanged)
```
