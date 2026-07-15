# Phase 0 Research: Serving-time as-of feature projection

All NEEDS CLARIFICATION from the spec are resolved here. Each decision is backed by the current code and, where noted, a real-DB measurement (race `202603020607`, 957,355 history rows).

## R1 — Where does the time go, and what can projection remove?

**Decision**: Restrict the OUTPUT rows a block emits (and, for per-key blocks, the SOURCE rows it rolls over) to the target race(s). Keep every race-level primitive that needs the full past field.

**Rationale**: profiled `build_asof_features` = ~46–58s; ~53s is per-row block work over 957k rows, ~4.5s is merge/assembly. For pace, `_pace_runs` (in-race relative primitives over full history) = 0.54s and CANNOT be reduced; the expensive `_rolling_asof` = 6.58s over all targets. Restricting the source `fin` to the target race's 16 horses → **0.0049s**, and `assert_frame_equal(check_exact=True, check_dtype=True)` on the target rows passes. So the reducible cost is the per-key rolling/merge_asof/cumsum, not the race-level primitive.

**Alternatives considered**: (a) naive output-only filter at the end of the full build — measured to save only ~4.5s (the assembly), rejected. (b) stateful sufficient-statistics store to also remove `load_frames` + primitives — out of scope (much larger/riskier). (c) entity-scoped SQL loading — deferred (dependency closure widens to full past field / other-offspring / cell).

## R2 — Per-block source-filter taxonomy (the crux)

Each block is one of three kinds. The **source filter key** is what makes projection byte-safe; the "daily cumsum − current day" / merge_asof mechanisms are kept UNCHANGED.

| Kind | Blocks | Source filter key | Why byte-safe |
|---|---|---|---|
| **Per-horse** | pace(023), extra(020), lowcost(030), past_market(058), speed_figure(061), corner(041), history | `horse_id ∈ target horses` | Every aggregate is grouped by `horse_id`; a horse's value depends only on its own rows. Filtering the source to target horses cannot change any target horse's value. **Proven**: pace 6.58s→0.005s, byte-identical. |
| **Cross-entity** | human_form(020 jockey/trainer), pedigree(026 sire), owner_breeder(056), debut_pedigree(032) | `entity_key ∈ target entities` (jockey/trainer/sire/owner/breeder of the target horses) | Aggregates group by the entity; self-exclusion (other-offspring = sire cumsum − self) and same-day exclusion (daily cumsum − current day) both live INSIDE the entity's rows. Keep ALL of each target entity's rows → both mechanisms intact. **Proven**: human_form projected by entity == full on target rows, byte-identical (`check_exact`). |
| **Race-atomic** | pace_scenario(031), relative_ability(059) | the target race's ENTIRE started field | Within-race leave-one-out + pl_topk softmax group read other horses in the SAME race. Project the whole field (never a single horse); the computation is local to the target race once the field's as-of inputs (projected pace / assembled ability) are present. |

**Decision**: `target_race_ids` is the single restriction parameter. A race id pulls its whole started field, so race-atomic blocks are correct by construction, and per-horse / cross-entity blocks derive their finer key (horse / entity) from the field.

## R3 — Same-day multiple target races (the highest-risk point, FR-009)

**Decision**: NEVER replace "daily cumsum − current day" with a coarser `race_date < cutoff_date` rule. Keep the exact per-key daily mechanism on the key-filtered source.

**Rationale**: the full build excludes a key's ENTIRE current day (removing the target appearance AND every same-day appearance of that key). Backfill projects a whole day of races at once; a jockey/horse may appear in several of them. Because the source keeps ALL of each target key's rows (including the whole target day) and the subtraction is `cumsum − current-day-aggregate`, every same-day appearance is excluded exactly as in the full build — for every target race on that day simultaneously. A `race_date < cutoff` shortcut would instead leak earlier-same-day appearances into later ones and is explicitly forbidden.

**Evidence**: human_form's mechanism (`_win_rate_before`: `cumsum − d_wins/d_cnt` per `(key, race_date)`) already encodes whole-day exclusion; the real-DB parity check passes on a target race whose jockeys ride multiple races that day. A dedicated synthetic test (a jockey and a horse each appearing in two same-day races, both after prior finished appearances) is a required gate for Phase B/C.

**Scope note**: the interactive `run_serving(race_id)` path projects exactly one race, so same-day multi-race only arises in `run_serving_backfill` (per-day). Both are covered by the same key-filtered-source mechanism; no special-casing.

## R4 — Parameter shape & threading

**Decision**: `target_race_ids: frozenset[str] | None = None` on each converted `build_*_features`, on `build_asof_features`, and on `assemble_feature_matrix` / `build_feature_matrix`. `None` = full build (byte-parity oracle). Serving passes `frozenset({race_id})` (single) or the day's race ids (backfill).

**Rationale**: a set of race ids is JSON-free, race-atomic by construction, and lets each block derive its own key. It composes with the existing `wanted=` leaf-skip (a block can be both skipped AND, if kept, projected). `None` everywhere else keeps materialize / training / backfill byte-unchanged.

**Alternatives**: passing a `(race_id, horse_id)` targets DataFrame — rejected as heavier and redundant (the field is recoverable from `frames` + race ids); passing a cutoff date — rejected (loses race-atomicity, invites the R3 mistake).

## R5 — Determinism / load order

**Decision**: do not change any `sort_values` key or the loader SQL ordering. The source filter is a boolean mask that preserves row order; `merge_asof` and stable sorts behave identically on a masked frame.

**Rationale**: codex flagged that `race_horses` / `race_results` load without an explicit `ORDER BY` and several blocks stable-sort by `race_date` only; adding a `race_id` tie-break would be a VALUE change. The parity gate would catch any order-induced drift; keeping keys unchanged avoids it entirely. Any tie-break fix is a separate, non-perf change (out of scope).

## R6 — Parity gate (adoption bar per block)

**Decision**: a block is wired into the serving projection ONLY after a test proves, on the real DB, `projected(target_race_ids={R}) == full_build.loc[target_keys(R)]` with `check_exact=True, check_dtype=True`, identical row & column order, across the cohort: normal, debut (0 prior), low-history, cancelled entry in field, missing odds, and same-day multiple appearances. End-to-end active-model win/top2/top3 must be byte-identical (max|Δ| 0.0).

**Rationale**: the whole feature's safety rests on byte-identity, not on "better" values (III). A block that cannot meet the gate is simply not converted — the full path remains its fallback.

## R7 — Expected gain & stopping rule

**Decision**: land pace first (largest retained block, proven), re-profile, then convert per-horse blocks (extra/lowcost/past_market/speed_figure/corner/history), then cross-entity (pedigree is the only heavy one, 2.6s; human_form is already 0.37s so low priority), then race-atomic. Stop when the next block's full-pool cost is small relative to the `load_frames`+primitive floor.

**Rationale**: floor ≈ `load_frames` 6s + un-restrictable race-level primitives (pace `_pace_runs` 0.54s, pm complete-field q/s, speed-figure cell base). Realistic target ~10–15s. Converting sub-second blocks is not worth the parity-gate cost — surface that in the plan rather than silently converting everything.

## R8 — Quality gate on the highest-risk point (cross-entity same-day source-filter) [G1]

**Decision**: the cross-entity same-day source-filter (Phase C: human_form / pedigree / owner_breeder) is validated by (a) an empirical byte-parity measurement and (b) the mandatory per-block parity gate — NOT by an unverified argument alone.

**Evidence & reasoning**:
- **Empirical**: `human_form` (jockey/trainer keys) filtered to the target race's entities reproduces the full build on the target rows byte-for-byte on the real DB (`check_exact`); a real target race's jockeys ride multiple races that day, so same-day multi-race is already exercised.
- **Mechanism**: each entity aggregate is `groupby([key, race_date])` then `cumsum − current-day`. It depends only on that key's own rows, and the current-day subtraction includes the key's WHOLE target day (every same-day race). Filtering the source to the target entities keeps all of each entity's rows (incl. same-day), so both same-day exclusion and pedigree self-exclusion (`sire cumsum − self`) are byte-preserved. No counterexample found under this mechanism.
- **Safety net (decisive)**: every block is wired into serving ONLY after `projected == full.loc[keys]` passes `check_exact` across the same-day-multi-race cohort (T006b + T019 sire, non-R7-conditional). If any reasoning gap exists, the gate catches it at implement time and the block stays on the full path (FR-010). This makes the design safe *by construction of the gate*, independent of the argument's completeness.

**codex second opinion**: a focused clean `codex exec` pass on this exact question was attempted; it returned no verdict (the run tried to spawn a nested review agent and hung — the known tooling instability recorded in `codex-env-recovery`). Per CLAUDE.md's failure protocol (max one retry; already two flaky runs this session), the self-review checklist + the empirical measurement + the machine-checkable per-block parity gate stand as the quality gate. Re-attempt a clean codex pass opportunistically before wiring Phase C, but it is not a blocker: the parity gate is the enforcing check.

## Baseline & Phase A (pace) results [T002 / T002b / T011]

**Retained blocks (T002b, lgbm-063 / features-017)**: pace 11/11, extra 6/6 retained; F02 0/9 (correctly leaf-skipped by `wanted=`). → pace and extra are worth converting; F02 is not built for the active model.

**Baseline (pre-conversion), real DB race `202603020607`**: `build_asof_features` full ~46–58s; pace block 9.4s (of which `_pace_runs` primitive ~0.5s, `_rolling_asof` ~6.6s reducible), extra ~3.2s.

**Phase A outcome (pace converted, measured real DB)**:
- pace block: **9.38s → 0.60s**, byte-identical (`check_exact`). Reducible `_rolling_asof` <50ms; ~0.5s `_pace_runs` floor remains.
- `build_feature_matrix` (active model, `wanted=`): **41.75s → 30.90s** projected — target rows byte-identical.
- active-model win/top2/top3 predictions **byte-identical** (max|Δ| 0.0).
- Gate found + fixed one real bug: full `static` × target-only `asof` left-merge NaN-filled non-target rows and upcast int columns (`career_starts` int64→float64); fixed by row-restricting `static` to the target races before the merge (values/dtypes byte-identical). This is exactly the kind of drift the per-block parity gate exists to catch.

**Remaining**: 30.9s still includes the un-converted per-horse blocks (extra 3.2s, lowcost 3.7s, past_market 3.4s, …) computed full — US2 converts them per gate toward the ~10–15s floor.

## US2 progress (per-horse blocks converted) [T012/T014/T017]

Converted (per-horse, source-filter by horse_id, byte-parity gated): **pace, extra, history, past_market**.
- `build_feature_matrix` (active model): ~43s → **22.85s**; target rows + predictions byte-identical.
- Gate caught pool-dependent dtype drift in history: `days_since_last` and `prev_finish` are float64 in
  the full pool (debuts → NaN) but int64 in a projected set with no debut → pinned float64 (no-op for
  the full build; materialize parity `test_asof_real_db` stays green).
- past_market keeps its `field_size` race-level primitive over the FULL frame; only the per-horse
  rolling source + target rows are restricted.

Deferred (need per-block care, each behind its own gate): **lowcost** (mixes a cross-entity
human_form_plus sub-block — horse_id filter would break it), **speed_figure** (cell baseline
primitive), **corner** (field_size primitive), **human_form/pedigree/owner_breeder/debut_pedigree**
(cross-entity: entity-key filter), **031/059** (race-atomic). human_form is R7-deferred (~0.37s).

## US2 continued: speed_figure, corner, pedigree [T015/T016/T019]

Now projected (7 blocks total): **pace, extra, history, past_market, speed_figure, corner, pedigree**.
- `build_feature_matrix` (active model): ~43s → **19.29s**; target rows + predictions byte-identical.
- speed_figure keeps its (venue×track×dist×going) cell BASELINE full; corner keeps its `field_size`
  full; both filter only the per-horse source. Both already pin their output columns to float64.
- **pedigree (cross-entity)**: source filtered to rows whose sire OR damsire is a target entity —
  which includes every target horse's own rows (their sire is a target sire), so other-offspring
  self-exclusion + same-day exclusion stay byte-identical. `base` row set derived from the restricted
  `targets`. pedigree already pins all columns float64. **This empirically closes R8/G1** (cross-entity
  same-day source-filter is byte-identical, on both synthetic self-exclusion+same-day fixtures and the
  real-DB matrix).

Still un-converted (each behind its gate): lowcost (mixed cross-entity human_form_plus), human_form
(R7-deferred ~0.37s), owner_breeder + debut_pedigree (cross-entity), pace_scenario(031) +
relative_ability(059) (race-atomic). Floor ≈ load_frames ~6s + these + assembly.

## US2 complete + US3 [T013/T020/T021/T022/T023/T024/T025/T026/T027]

**Projected (10 blocks):** pace, extra, history, past_market, speed_figure, corner, pedigree,
owner_breeder, debut_pedigree, **lowcost** (mixed per-horse + cross-entity: source filtered to
horse_id | jockey_id | trainer_id ∈ target entities).

**US3 (race-atomic) — already projected by construction:** `relative_ability`(059) consumes the
assembled `out`, which is target-only because `history` (the merge base) is projected → 059 computes
only over the target races' fields (within-race LOO intact). `pace_scenario`(031) and
`condition_change`(033) are correct via the target-only `out` merge and are sub-second (R7-deferred
internal conversion). All confirmed byte-identical by the E2E.

**Final timing (active model, real DB):** `build_feature_matrix` ~43s → **15.02s** (SC-001 met:
~10–15s floor). Predictions win/top2/top3 byte-identical (max|Δ| 0.0). Combined with the earlier
N+1 + ops-precompute work, the on-demand single-race prediction feature build went from ~55s (post
N+1) / ~113s (original) to ~15s.

**Deferred (R7, sub-second, not worth the gate):** human_form (~0.37s), condition_change(033 ~0.78s),
pace_scenario(031 ~0.36s), race_level(056 ~0.85s). Remaining floor = load_frames ~6s + static +
these sub-second blocks + assembly.

**Suites:** features 305 / serving 48 / training 120 green; ruff clean; materialize parity
(`test_asof_real_db`) green (full build byte-unchanged).
