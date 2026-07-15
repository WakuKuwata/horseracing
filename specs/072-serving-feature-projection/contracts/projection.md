# Contract: as-of block target projection

Internal Python contract (no external API / OpenAPI / schema). Governs the `features/` block functions, the builder, and the serving wiring.

## Block function contract

```python
def build_<X>_features(
    frames: Frames,
    *,
    <existing kwargs>,
    target_race_ids: frozenset[str] | None = None,
) -> pd.DataFrame: ...
```

- `target_race_ids=None` (default): output is **byte-identical** to the pre-feature function (INV-P2). All existing callers that don't pass it are unaffected.
- `target_race_ids={…}`: output equals `build_<X>_features(frames).loc[keys]` restricted to the started horses of those races, byte-for-byte (INV-P1), same row/column order and dtype.

### Per-kind implementation rule (R2)

- **Per-horse**: `src = src[src.horse_id.isin(target_horses)]` BEFORE the groupby-rolling / cumsum / merge_asof. Race-level primitives (e.g. pace in-race relative means) computed on the FULL frame first.
- **Cross-entity**: `src = src[src[entity_key].isin(target_entities)]` where `entity_key ∈ {jockey_id, trainer_id, sire_name, owner_name, breeder_name}`. Keeps other-offspring / other-mounts and whole-day exclusion intact.
- **Race-atomic (031/059)**: consume the projected pace / assembled-ability of the target race's FULL field; never a single horse.

### Forbidden

- Replacing `daily cumsum − current day` with `race_date < cutoff` (R3 — leaks same-day).
- Changing any `sort_values` key or loader `ORDER BY` (R5 — value change).
- Filtering the source by `race_date` window in a way that drops a target key's earlier rows (breaks career/cumulative).

## Builder threading

```python
assemble_feature_matrix(frames, *, target_race_ids: frozenset[str] | None = None, wanted=None, …)
build_feature_matrix(session, *, target_race_ids: frozenset[str] | None = None, wanted=None, …)
build_asof_features(frames, *, skip_blocks=…, target_race_ids: frozenset[str] | None = None)
```

- Composes with `wanted=` (leaf-skip) — orthogonal (INV-P5).
- When `target_race_ids` is set, `assemble_feature_matrix` restricts the emitted population to those races' started horses (static features computed for the field only), and each converted block receives `target_race_ids`. Un-converted blocks still run full and are `.loc`-restricted at assembly (correct but not yet fast) — this is how a phase ships one block at a time without breaking the matrix.
- `target_race_ids=None` everywhere = today's behavior exactly.

## Serving wiring

- `run_serving(race_id=R)` → `build_feature_matrix(..., target_race_ids=frozenset({R}), wanted=frozenset(model.feature_cols))`.
- `run_serving_backfill(day)` → `target_race_ids = frozenset(day's race ids)`.
- `predict_race` unchanged (already slices `model.feature_cols`); predictions byte-identical (SC-003).

## Parity gate (per block, adoption bar — R6)

A block is wired into serving projection ONLY after:

1. **Unit (DB-free, `make_frames`)**: projected == full-restricted, `check_exact=True, check_dtype=True`, for: normal multi-history horse, debut (0 prior), low-history, cancelled entry in field, and **same-day multiple appearances** (a horse and an entity each in two same-day races) — the R3 gate.
2. **Integration (real DB)**: `build_<X>_features(frames, target_race_ids={R}) == build_<X>_features(frames).loc[keys(R)]`, `check_exact`, on a real race; block wall-clock recorded (expect ≫10× drop for the heavy blocks).
3. **End-to-end**: active-model `win/top2/top3` byte-identical between full and projected `build_feature_matrix` for a real race (max|Δ| 0.0).

Failing any → the block is NOT converted; the full path remains its behavior.

## Non-goals (unchanged)

FEATURE_VERSION, `source_fingerprint`, materialized parquet parity, compat pins, persisted values, audit envelope, schema, OpenAPI — all untouched (INV-P6).
