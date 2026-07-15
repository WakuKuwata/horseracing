# Implementation Plan: Serving-time as-of feature projection (split-build)

**Branch**: `072-serving-feature-projection` | **Date**: 2026-07-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/072-serving-feature-projection/spec.md`

## Summary

Serving one race still computes every retained as-of block over the full ~957k-row pool, then keeps ~16 rows. This plan adds an optional **target restriction** (`target_race_ids`) to each hot as-of block: the block computes any race-level primitive that needs the full past field over ALL past races, then restricts the per-key rolling / merge_asof / cumsum to the target race's rows. `target_race_ids=None` keeps the current full build **byte-identical** (the parity oracle for materialize / training / backfill). Serving passes the race(s) it is predicting. Landed staged and per-block behind a byte-parity gate; composed under the existing `wanted=` leaf-skip path so serving gets both.

Proven: pace's `_rolling_asof` over all targets = 6.58s → **0.0049s** when the source is restricted to the target race's horses, **byte-identical** on the target rows (real DB, `check_exact`). Goal: active-model single-race cold build ~43s → ~10–15s, predictions byte-identical.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: pandas / numpy (feature build); SQLAlchemy 2.0 (loader); LightGBM (serving predictor, unchanged)

**Storage**: PostgreSQL 16 (read-only for feature build); no schema change; materialized parquet (025) untouched

**Testing**: pytest + real-DB integration (features/tests/integration), DB-free unit (`tests._frames.make_frames`)

**Target Platform**: Linux/macOS server (serving CLI via ops subprocess)

**Project Type**: Multi-package monorepo (`features/`, `serving/`, …); this feature touches `features/` (block functions + builder) and `serving/` (wiring only)

**Performance Goals**: single-race cold feature build ~43s → ~10–15s; pace block <50ms projected; predictions byte-identical

**Constraints**: FEATURE_VERSION / `source_fingerprint` / materialize parquet parity / compat pins / load order / sort keys ALL unchanged; per-block byte-parity gate (`check_exact=True, check_dtype=True`) is the adoption bar

**Scale/Scope**: ~957k history rows; ~16 horses/race; ~12 races/day (backfill). Blocks in scope — per-horse (by cost): pace, extra, lowcost, past_market, speed_figure, corner, history; cross-entity: pedigree, human_form, owner_breeder, debut_pedigree; race-atomic: pace_scenario(031), relative_ability(059). Sub-second blocks are R7-conditional (convert only if re-profiling justifies); only blocks `lgbm-063` retains are candidates.

## Constitution Check

Constitution v1.0.0 gate (PASS / N/A):

- [x] **I. データ契約**: N/A to changes — no ID/label/schema change; `raceId` 12-digit and `id_mappings` joins untouched. `target_race_ids` is a filter of existing race ids.
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS — projection restricts OUTPUT rows only; the historical source a block may read is unchanged (strictly-before `race_date`, same-day exclusion via unchanged "daily cumsum − current day", pedigree self-exclusion, cross-entity same-day). Source is restricted by KEY (horse / entity / race-field), never by a coarser `race_date < cutoff` rule, so same-day contributions are preserved (research R2/R3). Odds/results never become features. Enforced by per-block leak + same-day-multi-race tests.
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS — the adoption gate is a **byte-parity** test (projected == full-restricted, `check_exact`), run per block on the real DB across an edge-case cohort before that block is wired into serving. No model/feature-value change ⇒ no walk-forward needed (values are provably identical, not "better").
- [x] **IV. 確率整合性**: PASS — predictions are byte-identical (SC-003); Σ constraints and Unknown/0 handling inherited unchanged.
- [x] **V. 再現性・監査**: PASS — no persisted-value change; logic_version / model_version / feature_version unchanged. Projection is invisible to the audit envelope (same numbers, same versions).
- [x] **VI. feature 分割規律**: PASS — no schema/API change; serving-only. No new table. Staged rollout (pace first) matches "1 bundle at a time" discipline.
- [x] **品質ゲート**: codex second opinion taken (Track 2 minimal-safe design adopted; a focused third pass on the highest-risk cross-entity same-day source-filter hung without a verdict — known tooling instability, `codex-env-recovery`). Per CLAUDE.md failure protocol, the quality gate is the empirical byte-parity measurement (human_form real-DB `check_exact`) + the machine-checkable per-block parity gate (research R8). A clean codex pass may be re-attempted before wiring Phase C but is not a blocker.

**Result**: no violations; Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/072-serving-feature-projection/
├── plan.md              # This file
├── research.md          # Phase 0: per-block source-filter taxonomy + same-day resolution
├── data-model.md        # Phase 1: target_race_ids contract + block API + parity gate
├── contracts/
│   └── projection.md    # block interface, builder threading, serving wiring, parity oracle
├── quickstart.md        # Phase 1: how to validate (real-DB parity + timing + E2E)
└── checklists/requirements.md
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── builder.py                 # assemble_feature_matrix / build_feature_matrix: thread target_race_ids
├── materialize.py             # build_asof_features: thread target_race_ids to each block; compose with skip_blocks
├── pace_features.py           # P1: source-filter by horse_id before _rolling_asof
├── extra_features.py          # P2: per-horse (recent_form/aptitude/class_transition)
├── lowcost_features.py        # P2: per-horse
├── past_market_features.py    # P2: per-horse (058 rank)
├── speed_figure_features.py   # P2: per-horse (cell primitive full, roll target horses)
├── corner_trajectory_features.py  # P2: per-horse
├── history.py                 # P2: per-horse cumulative
├── human_form.py              # P2 (cross-entity): source-filter by jockey_id/trainer_id
├── pedigree_features.py       # P2 (cross-entity): source-filter by sire_name; keep self-exclusion
├── owner_breeder_features.py  # P2 (cross-entity): source-filter by owner_name/breeder_name (056)
├── debut_pedigree_features.py # P2 (cross-entity): consumes projected history+pedigree of target field (032)
├── pace_scenario_features.py  # P2 (race-atomic): consumes projected pace of the target field
└── relative_ability_features.py # P2 (race-atomic): consumes projected assembled ability of the field

features/tests/unit/           # per-block projection parity (make_frames): per-horse, cross-entity, race-atomic, same-day-multi-race, debut/low-history/cancel
features/tests/integration/    # real-DB: projected == full-restricted (check_exact) + timing
serving/src/horseracing_serving/pipeline.py  # pass target_race_ids from run_serving / run_serving_backfill
```

**Structure Decision**: extend the existing single as-of source (`build_asof_features`) with an optional row restriction; no new module, no second implementation (025 contract). Serving is the only caller that passes a restriction.

## Phasing / Rollout

- **Phase A (P1)** — pace only: add `target_race_ids` to `build_pace_features` (+ `_rolling_asof` source filter by horse_id), thread through builder, wire serving, per-block parity gate + timing. Ship + re-profile.
- **Phase B (P2, per-horse blocks)** — extra, lowcost, past_market, speed_figure, corner, history: same per-horse source filter, each behind its own parity gate. Re-profile after each; stop when marginal gain < risk.
- **Phase C (P2, cross-entity)** — human_form, pedigree (+ debut_pedigree, owner_breeder): source filter by ENTITY key (keeps other-offspring / other-mounts + same-day); parity gate incl. self-exclusion + same-day-multi-race.
- **Phase D (P2, race-atomic)** — pace_scenario(031), relative_ability(059): consume the projected pace / assembled-ability of the target race's full field; parity gate on the whole field.

Each phase is independently shippable and never leaves a block wired into serving without passing its parity gate (FR-010).

## Complexity Tracking

No constitution violations — table omitted.
