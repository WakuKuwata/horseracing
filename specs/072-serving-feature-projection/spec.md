# Feature Specification: Serving-time as-of feature projection (split-build)

**Feature Branch**: `072-serving-feature-projection`

**Created**: 2026-07-15

**Status**: Draft

**Input**: User description: cut on-demand single-race prediction latency by computing each as-of feature block for ONLY the target race's rows instead of the full ~957k-row pool, while keeping the existing full build as the untouched byte-parity oracle.

## Context & Background

The on-demand "predict this race" path (race-detail page → ops → serving CLI) has been progressively sped up:

1. `load_topk_samples` N+1 → bulk (fit_stage_discount 57s → 3s).
2. ops precompute predict-ahead (entries land → a prediction is computed ahead of the click; the detail page reads a persisted run).
3. Track 1 vectorization — byte-identical kernels for `extra._recent_form` and `pm_core_strength` trend (feature 072's sibling, already merged on the perf branch).
4. F02-skip projected builder — `build_asof_features(skip_blocks=)` + `assemble/build_feature_matrix(wanted=model.feature_cols)` skip whole optional **leaf** blocks the loaded model never reads. Active `lgbm-063` (features-017) does not read F02 (pm_core_strength), so serving now skips it: full build 54s → 43s, predictions byte-identical.

**Remaining bottleneck.** Even after (1)–(4), serving one race still computes every *retained* as-of block over the **entire ~957k-row history pool** and then filters to the ~16 started horses of the target race. The per-row work for the other ~957k rows is thrown away. Profiled block costs on the full pool (post-Track-1): pace 9.7s, lowcost 3.7s, past_market 3.4s, extra 3.2s, pedigree 2.6s, speed_figure 1.8s, corner 1.5s, history 1.2s; plus `load_frames` ~6s and the merge/assembly ~4.5s.

**Feasibility proven.** For pace (the single biggest retained block) the expensive part is `_rolling_asof` over all targets = 6.58s. Restricting the *source* frame to the target race's horses (the rolling aggregation is per-horse independent) makes it **0.0049s** and **byte-identical** to the full build on the target rows (real DB, race `202603020607`, `assert_frame_equal(check_exact=True, check_dtype=True)`). The same shape applies to every per-horse-independent as-of block: compute race-level primitives that need the full past field first, then restrict the per-horse rolling / merge_asof / cumsum to the target rows.

This feature turns that proof into a staged, per-block, parity-gated capability.

## Constitution & Boundaries

- **II (leak boundary)**: the projected path must preserve strictly-before-`race_date` semantics, same-day exclusion, pedigree self-exclusion (other-offspring), and cross-entity (jockey/trainer/sire) same-day handling. Restricting *output* rows never relaxes what the *history* may contribute. Odds/results never become features.
- **III (parity as the adoption gate)**: each block is adopted only when its projected output is **byte-identical** to the full build restricted to the same keys — this is the same-formula-fewer-rows discipline, not a second implementation.
- **025 single as-of implementation**: `build_asof_features` stays THE single source; projection is a row-selection option on the existing block functions, so the in-memory builder / serving fallback / materialized fast path never drift.
- **VI**: no schema change, no FEATURE_VERSION bump, `source_fingerprint` and materialize parquet parity and compat pins untouched. Serving-only projection.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fast cold single-race prediction (Priority: P1)

An operator opens the race-detail page for a race whose active-model prediction was not precomputed (or clicks 予測生成), and the prediction job completes materially faster than today, with identical numbers.

**Why this priority**: This is the whole point — the cold on-demand path is the slow one users feel. Pace alone is the largest retained block and is already proven safe, so P1 = "pace block gains a target-projected path, wired into serving, byte-identical."

**Independent Test**: On the real DB, build the feature matrix for one race two ways — full (`targets=None`) and projected (target = the race's started horses) — and assert the pace columns are byte-identical on the target rows (`check_exact=True, check_dtype=True`); measure the pace block wall-clock drop; run the active model through both and assert win/top2/top3 byte-identical.

**Acceptance Scenarios**:

1. **Given** the active model and a real race, **When** serving builds features with target-projection enabled for pace, **Then** the pace feature values for the target horses equal the full-build values byte-for-byte and the pace block time drops from ~seconds to ~milliseconds.
2. **Given** `targets=None` (materialize / training / backfill), **When** the pace block runs, **Then** its output is byte-identical to the pre-feature behavior (no change for non-serving consumers).
3. **Given** a race with a debut horse (no prior starts) and a horse with partial history, **When** projected, **Then** the projected values equal the full-build values (NaN placement included).

### User Story 2 - Staged extension to the next-heaviest blocks (Priority: P2)

After pace lands and is gated, the same target-projection is extended block-by-block to the next retained blocks, each behind its own parity gate, re-profiling between blocks to stop when the remaining gain no longer justifies the risk. In-scope blocks (matching the Context cost profile and research R2 taxonomy):
- **per-horse** (source-filter by `horse_id`): extra(020), lowcost(030), past_market(058), speed_figure(061), corner(041), history.
- **cross-entity** (source-filter by entity key, keeping self-exclusion + whole-day exclusion): human_form(020 jockey/trainer), pedigree(026 sire), owner_breeder(056), debut_pedigree(032).

The stopping rule (research R7) applies: a block whose full-pool cost is already sub-second (e.g. human_form ~0.37s) may be skipped as not worth the parity-gate effort — this is a plan-time decision recorded in re-profiling, not a scope removal.

**Why this priority**: The bulk of the remaining time is spread across several blocks; capturing them multiplies the P1 win, but each carries its own leak/parity nuance (self-exclusion, cross-entity same-day) so they must be added one at a time, not in a single sweep.

**Independent Test**: For each newly converted block, the per-block parity gate (projected == full-restricted, byte-identical) passes on the real DB across the edge-case cohort (debut, low-history, cancelled entry, missing odds, same-day multiple starts), and end-to-end active-model predictions stay byte-identical.

**Acceptance Scenarios**:

1. **Given** a converted block, **When** its projected output is compared to the full build restricted to the target keys, **Then** they are byte-identical (row + column order, dtype).
2. **Given** a cross-entity block (human_form / pedigree), **When** projected with a cutoff, **Then** same-day other-race results and the horse's own same-day / self contributions are excluded exactly as in the full build.

### User Story 3 - Race-atomic within-race blocks (Priority: P2)

The within-race blocks (031 pace_scenario, 059 relative_ability) are projected by passing the target race's **entire started field**, never a single horse, so leave-one-out and the pl_topk softmax group stay intact.

**Why this priority**: These blocks read other horses in the same race; a per-horse target would corrupt the leave-one-out and the race-level softmax group. They can still be projected — the unit of projection is the race, not the horse.

**Independent Test**: Project with the full started field of the target race and assert byte-identity to the full build on those rows; assert that passing a strict subset of the field is either rejected or yields the documented (different) values, so the race-atomic contract is explicit.

**Acceptance Scenarios**:

1. **Given** the target race's full started field as targets, **When** 031/059 are projected, **Then** values are byte-identical to the full build for those rows.

### Edge Cases

- **Same-day multiple target races** (a serving day with many races): the projected cutoff must reproduce the full build's "daily cumsum − current day" semantics, i.e. a target horse that ran earlier the same day in another race must contribute to later same-day races exactly as in the full build — projection must not silently switch to a coarser `race_date < cutoff_date` rule where the full build used same-day-aware exclusion. This is the highest-risk parity point and must be covered by a dedicated test.
- **Debut / zero prior starts**: projected NaN/`is_debut` values equal the full build.
- **Cancelled / excluded entry in the target race**: population selection (started only) is unchanged.
- **Missing odds** (market-offset / F02 columns): unaffected — projection is orthogonal to leaf-skip; a race with no odds still behaves as today.
- **Future race not in the materialized parquet**: the projected path composes with the existing in-memory serving fallback.
- **Target horse whose only history is on the cutoff day**: excluded (strictly-before), identical to full build.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each converted as-of block MUST accept an optional target restriction that, when absent, produces output **byte-identical** to the current full build (the parity oracle for materialize / training / backfill).
- **FR-002**: When a target restriction is supplied, a block MUST compute any race-level primitive that depends on the full past field (e.g. pace in-race relative means, complete-field q/s) over ALL past races, and only then restrict per-horse rolling / merge_asof / cumsum output to the target rows.
- **FR-003**: For every converted block, the projected output MUST equal the full build restricted to the same `(race_id, horse_id)` keys, verified with `check_exact=True, check_dtype=True` and identical row/column order — this is the adoption gate; a block that cannot meet it is NOT converted.
- **FR-004**: The projected path MUST preserve the leak boundary exactly: strictly-before `race_date`, same-day exclusion, pedigree self-exclusion, and cross-entity same-day handling — including the same-day-multiple-races case (FR-009).
- **FR-005**: Within-race blocks (031 pace_scenario, 059 relative_ability) MUST be projected race-atomically. Race-atomicity is **structural**: the public knob is `target_race_ids` (race ids), and the target field is always DERIVED whole from `race_horses` (started) — the public API cannot express a partial field, so a per-horse projection is impossible by construction. The test asserts this two ways: (a) byte-parity of the projected block vs the full build on the whole field; (b) an internal-helper test that calls the within-race function with an artificially truncated field and shows it yields DIFFERENT values — documenting WHY the field must be whole, not a runtime rejection path.
- **FR-006**: Serving (single-race `run_serving` and per-day `run_serving_backfill`) MUST pass the target restriction; materialize, training, and any full-matrix caller MUST pass none, keeping their output byte-unchanged.
- **FR-007**: Projection MUST compose with the existing leaf-skip projected builder (`wanted=`), so serving gets both unused-leaf skipping and target-row projection in one path.
- **FR-008**: The feature MUST NOT change feature values, FEATURE_VERSION, `source_fingerprint`, materialized parquet parity, compat pins, load order, or sort keys.
- **FR-009**: The system MUST have an explicit test that builds with `target_race_ids` holding **≥2 same-day races** (the real `run_serving_backfill` per-day scenario) and asserts byte-parity for BOTH races against the full build — not only a synthetic single-block same-day fixture. The test MUST cover a target horse and a cross-entity key (jockey/sire) each appearing in two of those same-day races, proving the earlier same-day appearance contributes to the later one identically to the full build (the "daily cumsum − current day" mechanism preserved, never a `race_date < cutoff` shortcut).
- **FR-010**: Rollout MUST be staged: pace first, gated and re-profiled, then extended block-by-block; the codebase MUST never contain a half-converted block that is wired into serving without passing FR-003.

### Key Entities *(include if feature involves data)*

- **Target restriction**: the set of `(race_id, horse_id)` rows a block must emit — for serving, the started horses of the race(s) being predicted. It never widens or narrows the historical source a block may read (that is fixed by the leak boundary), only which output rows are materialized.
- **As-of block**: an existing `build_*_features(frames)` function producing per-`(race_id, horse_id)` columns; gains an optional target restriction with a byte-parity contract.
- **Parity oracle**: the full build (`targets=None`) — the ground truth every projected block is measured against.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Active-model single-race cold feature build drops from ~43s toward ~10–15s (floor = `load_frames` ~6s + un-restrictable race-level primitives), measured on the real DB.
- **SC-002**: For every converted block, projected output is byte-identical to the full build on the target rows (`check_exact`) across the edge-case cohort — zero mismatches.
- **SC-003**: End-to-end active-model win/top2/top3 predictions are byte-identical between full and projected builds for a real race (max |Δ| = 0.0).
- **SC-004**: Materialize / training / backfill output is byte-unchanged (existing real-DB parity tests stay green), confirming non-serving consumers are unaffected.
- **SC-005**: The reducible part of pace — `_rolling_asof` over the full pool (~6.6s) — drops to <50ms when projected to one race. The `build_pace_features` block as a whole drops from ~9.4s to ~0.6s; the residual ~0.5s is the `_pace_runs` in-race relative primitive computed over the full past field (un-reducible without a state store, R7 floor). Verified: 9.38s → 0.60s, byte-identical (real DB).

## Assumptions

- Per-row as-of features are pool-end independent (proven by the fact that materialization is byte-identical to the in-memory build) — therefore "compute history fully, emit only target rows" is mathematically safe for per-horse-independent blocks; cross-entity and within-race blocks need the race-atomic / cutoff-aware handling above.
- Serving builds one race (or one day's races) at a time with a known started field available before feature build (true today: the model and race are known at `run_serving`).
- `load_frames` (~6s) and race-level primitives that genuinely need the full past field are NOT reducible by this feature and remain in the floor; a stateful sufficient-statistics store that could remove them is explicitly out of scope.
- The existing `wanted=` leaf-skip path (feature-sibling, already merged) is the composition point; this feature adds row-projection beneath it.

## Out of Scope

- Changing any feature value, FEATURE_VERSION, schema, or fingerprint.
- A stateful sufficient-statistics / windowed-state store (would remove `load_frames` + primitive floor, but is a much larger, higher-risk design).
- Entity-scoped SQL loading (loading only the target entities' rows from the DB).
- Track 1 vectorization (already done) and the leaf-skip projected builder (already done).
- Any load-order or sort-key change (would be a value change; must not be mixed into a perf change).
- Materialize / training / backfill behavior (they keep `targets=None`).
