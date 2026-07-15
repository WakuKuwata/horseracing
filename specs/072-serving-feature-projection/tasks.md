# Tasks: Serving-time as-of feature projection (split-build)

**Input**: Design documents from `specs/072-serving-feature-projection/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/projection.md

**Tests**: REQUIRED here — the per-block byte-parity test IS the adoption gate (FR-003 / research R6). A block is wired into serving only after its parity test passes.

## Format: `[ID] [P?] [Story] Description`
- **[P]**: parallelizable (different files, no incomplete-task dependency)
- Paths are repo-relative; feature code lives in `features/src/horseracing_features/` and `serving/src/horseracing_serving/`.

## Conventions (apply to every block conversion)
- Signature: `build_<X>_features(frames, *, <existing>, target_race_ids: frozenset[str] | None = None)`. `None` ⇒ byte-identical to today (INV-P2).
- Compute full-field race-level primitives over ALL past races FIRST, then filter the per-key SOURCE (horse_id / entity key / race field per research R2), then emit only target rows.
- NEVER change a `sort_values` key or loader `ORDER BY`; NEVER replace `daily cumsum − current day` with `race_date < cutoff` (R3/R5).
- Parity gate per block: projected == full.loc[target_keys], `check_exact=True, check_dtype=True`, identical row/column order, across cohort {normal, debut, low-history, cancelled-in-field, missing-odds, same-day-multiple}.

---

## Phase 1: Setup

- [X] T001 [P] Add shared projection parity helper `features/tests/_projection.py` — `assert_projected_equals_full(build_fn, frames, race_id, keys=("race_id","horse_id"))` that runs `build_fn(frames)` and `build_fn(frames, target_race_ids=frozenset({race_id}))`, aligns on keys, and asserts `assert_frame_equal(full.loc[target], proj, check_exact=True, check_dtype=True)` incl. column/row order. Reused by every block gate.
- [X] T002 [P] Capture baseline timings for the target race (`202603020607`) — full `build_asof_features` + per-block wall-clock — into `specs/072-serving-feature-projection/research.md` (a "Baseline (pre-conversion)" note) so each phase's gain is measurable.
- [X] T002b [P] Enumerate the blocks `lgbm-063` (features-017) actually RETAINS (intersect `model.feature_cols` with each block's columns) and record each retained-but-not-in-scope block's full-pool wall-clock. Confirms the un-converted remainder (e.g. condition_change(033), `race_level` from feature 056, pace_scenario/relative_ability before US3) is genuinely sub-second — grounds the R7 stopping rule and the SC-001 ~10–15s floor. Record in research.md. (NB: feature **056** contributed TWO distinct blocks — `owner_breeder` is IN scope and converted at T020; `race_level` is the deferred sub-second sibling here. Always name the block, not "056".)

## Phase 2: Foundational (blocking — must complete before any block conversion)

- [X] T003 Thread `target_race_ids: frozenset[str] | None = None` through `build_asof_features` in `features/src/horseracing_features/materialize.py`: accept + validate (subset of `frames.races.race_id`), pass to each block that supports it, compose with existing `skip_blocks`. When set, the final column selection still emits only rows for the target races' started field.
- [X] T004 Thread `target_race_ids` through `assemble_feature_matrix` and `build_feature_matrix` in `features/src/horseracing_features/builder.py`: when set, restrict the emitted population (static features computed for the target field), pass to `build_asof_features`, compose with `wanted=`. `None` everywhere = today's behavior exactly.
- [X] T005 Wire serving in `serving/src/horseracing_serving/pipeline.py`: `run_serving(race_id=R)` → `target_race_ids=frozenset({R})`; `run_serving_backfill(day)` → `target_race_ids=frozenset(day's race ids)`. Materialize/training/backfill-materialize callers keep `None`.
- [X] T006 Foundational parity gate (no block converted yet): unit (`make_frames`) + integration (real DB) proving `build_feature_matrix(target_race_ids={R})` == `build_feature_matrix()` restricted to target rows, `check_exact`; and that `target_race_ids=None` leaves `test_asof_real_db` / `test_repair_parity` green (materialize/training byte-unchanged). Includes the **FR-007/INV-P5 composition assertion**: `target_race_ids` × `wanted=` are orthogonal — (skip a leaf AND project rows) == (full build, then leaf-drop + row-restrict), byte-identical. File: `features/tests/integration/test_projection_foundation.py`.
- [X] T006b **FR-009 multi-target-race build test** (research R3): build with `target_race_ids` = a frozenset of **≥2 same-day races** and assert byte-parity for BOTH races vs the full build (`check_exact`). Use a `make_frames` fixture where one horse AND one jockey/sire each appear in two of those same-day races after prior finished appearances. **Scope note (C1)**: at Foundational, no block is converted yet, so this proves the MATRIX-level restriction handles multiple same-day target races (necessary, not sufficient). The genuinely risky *projected cross-entity same-day source-filter* is proven when those blocks convert — anchored on **T019 (pedigree/sire, non-R7-conditional)** so coverage holds even if T018 (human_form) defers. Both T019's and T006b's fixtures must include the same-day-two-races entity case. File: `features/tests/unit/test_projection_same_day_multi_race.py`.

**Checkpoint**: serving passes `target_race_ids`; build is byte-identical for single AND multi-race target sets (still full-speed — no block converted). Safe to convert blocks one at a time.

## Phase 3: User Story 1 — Fast cold single-race prediction (Priority: P1) 🎯 MVP

**Goal**: pace (largest retained block) gains a target-projected path, wired into serving, byte-identical and materially faster.
**Independent test**: quickstart §1+§3 for pace — projected pace == full on target rows (`check_exact`); active-model win/top2/top3 byte-identical; pace block wall-clock collapses (~6.6s → <50ms, SC-005).

- [X] T007 [US1] Add `target_race_ids` to `build_pace_features` + `_rolling_asof` in `features/src/horseracing_features/pace_features.py`: compute `_pace_runs` (in-race relative primitives) over the FULL frame, then filter `fin` / `started` source to the target races' horses (horse_id) before the rolling + merge_asof; emit only target rows.
- [X] T008 [P] [US1] Unit parity test `features/tests/unit/test_pace_projection.py` (make_frames): projected == full-restricted (`check_exact`) for normal multi-history horse, debut, low-history, cancelled-in-field, and **same-day multiple appearances** (horse in two same-day races) — the R3 gate.
- [X] T009 [P] [US1] Integration parity + timing `features/tests/integration/test_pace_projection_realdb.py`: real race — projected == full.loc[keys] (`check_exact`); assert the pace **block** drops to well under the full-pool cost (<1s vs ~9.4s; the reducible `_rolling_asof` is <50ms, the ~0.5s `_pace_runs` primitive is the un-reducible floor per SC-005). Record the numbers.
- [X] T010 [US1] End-to-end parity: extend `test_projection_foundation.py` (or a serving test) — active model (`lgbm-063`) win/top2/top3 byte-identical between full and projected `build_feature_matrix` for a real race (max|Δ| 0.0).
- [X] T011 [US1] Re-profile: record full vs projected `build_feature_matrix` time for the active model post-pace into research.md; confirm the pace block dropped out of the hot path.

**Checkpoint**: P1 shippable — serving is byte-identical and faster by the pace delta. This is the MVP.

## Phase 4: User Story 2 — Staged extension to next-heaviest blocks (Priority: P2)

**Goal**: convert the remaining heavy per-horse and cross-entity blocks, each behind its own parity gate; re-profile between blocks and stop when marginal gain < risk.
**Independent test**: for each converted block, quickstart §1 parity passes across the cohort; E2E predictions stay byte-identical.

**Gate (U1/R7)**: T002b is a HARD prerequisite for this phase. Convert a block ONLY if (a) `lgbm-063` actually retains it (else it is already leaf-skipped by `wanted=` and conversion is dead work — DROP it), AND (b) its measured full-pool cost justifies the parity-gate effort. Blocks flagged "R7-conditional" below are converted only if re-profiling after the prior block still shows them as a material cost; otherwise they are deferred and recorded in T022.

Per-horse (source-filter by horse_id; primitives full first):
- [X] T012 [P] [US2] `extra_features.py` (recent_form / aptitude / class_transition) + `features/tests/unit/test_extra_projection.py`.
- [X] T013 [P] [US2] `lowcost_features.py` + `features/tests/unit/test_lowcost_projection.py`.
- [X] T014 [P] [US2] `past_market_features.py` (058 rank) + `features/tests/unit/test_past_market_projection.py`.
- [X] T015 [P] [US2] `speed_figure_features.py` (cell base primitive full; roll target horses) + `features/tests/unit/test_speed_figure_projection.py`.
- [X] T016 [P] [US2] **R7-conditional (corner ~1.5s)** `corner_trajectory_features.py` + `features/tests/unit/test_corner_projection.py`.
- [X] T017 [P] [US2] **R7-conditional (history ~1.2s)** `history.py` (cumulative-before) + `features/tests/unit/test_history_projection.py`.

Cross-entity (source-filter by entity key; keep self-exclusion + whole-day exclusion):
- [X] T018 [P] [US2] **R7-DEFERRED (human_form ~0.37s — not worth the gate; documented)** `human_form.py` (jockey_id / trainer_id key) + `features/tests/unit/test_human_form_projection.py` incl. same-day-multi-race (a jockey in two same-day races). Convert only if T002b/re-profiling shows it material.
- [X] T019 [P] [US2] `pedigree_features.py` (sire_name key; keep other-offspring self-exclusion) + `features/tests/unit/test_pedigree_projection.py` incl. self-exclusion + same-day.
- [X] T020 [P] [US2] `owner_breeder_features.py` (owner/breeder key) + `features/tests/unit/test_owner_breeder_projection.py`.
- [X] T021 [US2] `debut_pedigree_features.py` (consumes projected history + pedigree of the target field) + `features/tests/unit/test_debut_pedigree_projection.py`.
- [X] T022 [US2] Re-profile + E2E: active-model predictions byte-identical after all US2 blocks; record build time into research.md; note any block whose sub-second cost made conversion not worth it (stopping rule R7).

**Checkpoint**: bulk of the per-row cost removed; each block independently gated.

## Phase 5: User Story 3 — Race-atomic within-race blocks (Priority: P2)

**Goal**: project 031 / 059 by passing the target race's ENTIRE started field (never a single horse); leave-one-out + pl_topk group intact.
**Independent test**: projected == full on the whole field (`check_exact`). Race-atomicity is structural (`target_race_ids` always derives the whole started field); the internal-helper test truncates the field by direct call and shows DIFFERENT values, documenting why the field must be whole (FR-005 / INV-P4).

- [X] T023 [US3] `pace_scenario_features.py` (031): consume the projected pace of the target race's full field; project output to the field + `features/tests/unit/test_pace_scenario_projection.py` — byte-parity on the whole field, PLUS an internal-helper test that calls the within-race function with a truncated field and asserts the values differ (structural race-atomicity, not a rejection path).
- [X] T024 [US3] `relative_ability_features.py` (059): consume the projected assembled ability of the target race's full field + `features/tests/unit/test_relative_ability_projection.py` — byte-parity on the whole field, PLUS the same truncated-field internal-helper assertion.
- [X] T025 [US3] Re-profile + E2E: active-model predictions byte-identical after US3; final single-race cold build time recorded.

## Phase 6: Polish & Cross-Cutting

- [X] T026 [P] Run full suites from correct cwd: `features` (`uv run python -m pytest tests -q`), `serving`, `training` — all green; `ruff check` clean on changed files.
- [X] T027 [P] Final timing report: single-race cold `build_feature_matrix` before/after (target ~43s → ~10–15s, SC-001) into `quickstart.md` / `plan.md`; confirm SC-002..005.
- [X] T028 [P] Update memory `predict-latency-breakdown.md` (Track 2 landed, per-block gains) and confirm `CLAUDE.md` pointer; note any deferred blocks.

---

## Dependencies & Execution Order

- **Setup (T001–T002b)** → **Foundational (T003–T006b)** block everything. Foundational must be byte-identical (single AND multi-target-race, T006b) before any block conversion.
- **US1 (T007–T011)** depends only on Foundational. **This is the MVP** — ship after T011.
- **US2 (T012–T022)** depends on Foundational AND on **T002b as a hard gate** (only convert blocks `lgbm-063` retains AND that re-profiling shows material; drop/defer the rest — U1/R7). Block tasks T012–T020 are mutually `[P]` (different files); T021 depends on T017+T019 (debut_pedigree consumes history+pedigree); T022 after its block tasks records the stopping decision.
- **US3 (T023–T024)** depends on Foundational; for maximum speed run after US2 (059 consumes assembled ability, 031 consumes pace) but correctness needs only Foundational + pace (T007). `[P]` with each other.
- **Polish (T026–T028)** last.

## Parallel Opportunities

- Setup: T001, T002, T002b in parallel.
- US2: T012–T020 in parallel (nine independent block files, each with its own test); T018/T019 (cross-entity) can run alongside per-horse tasks.
- US3: T023, T024 in parallel.
- Polish: T026–T028 in parallel.

## Implementation Strategy

- **MVP = Foundational + US1 (pace)**: byte-identical serving, first real latency drop (~pace delta). Shippable on its own.
- **Incremental**: add US2 blocks one at a time behind the parity gate, re-profiling; stop per R7 when a block's cost no longer justifies the gate work.
- **US3** last (or after pace) for the within-race blocks.
- Each task's parity gate is non-negotiable: a block that can't be made byte-identical is left on the full path (FR-010).
