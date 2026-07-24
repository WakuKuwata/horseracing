# 081 Phase 0 — Screening findings (append-only)

**Run**: 2026-07-24 | **Contract**: phase0-screening-v1 (hash `c696cec79c95`, `can_adopt=false`)
**Window**: 2019-01-01 … 2026-07-12, 8 expanding folds, 26,006 eligible races
**Base**: lgbm-065 recipe-faithful strict-past OOF (`pl_topk:isotonic:0.3`), prequential γ, seed 20260724, B=2000

Δwinner-NLL = residual-offset probe (negative = the factor carries signal the active OOF has NOT used).
This is a SCREEN, not a Phase-1 prediction. The probe is a 1–3 parameter tilt, so its CI is TIGHTER
than a full-feature paired-eval; a real LightGBM feature will be noisier and may interact differently.

| rank | candidate | family | k | ΔNLL | CI (race-day cluster) | cov | Holm | screen |
|---|---|---|---|---|---|---|---|---|
| 1 | current_gap_shape | PRE_ENTRY | 3 | **−0.00288** | [−0.00393, −0.00185] | 0.88 | 0.000 | **PASS** |
| 2 | prior_gap_log | PRE_ENTRY | 1 | **−0.00114** | [−0.00183, −0.00043] | 0.88 | 0.009 | **PASS** |
| 3 | seasonal_sex | PRE_ENTRY | 2 | −0.00090 | [−0.00155, −0.00025] | 0.74 | 0.042 | screen< (point > −0.001) |
| 4 | weight_gain | POST_WEIGHT | 1 | −0.00034 | [−0.00074, +0.00011] | 0.45 | 0.478 | screen< |
| 5 | tataki_2 | PRE_ENTRY | 1 | −0.00030 | [−0.00063, +0.00004] | 0.79 | 0.415 | screen< |
| 6 | prev_finish_reversion | PRE_ENTRY | 2 | −0.00024 | [−0.00075, +0.00028] | 0.88 | 0.797 | screen< |
| 7 | body_mass_going | POST_WEIGHT | 3 | −0.00019 | [−0.00053, +0.00015] | 0.69 | 0.797 | screen< |
| 8 | draw_venue | PRE_ENTRY | 20 | −0.00003 | [−0.00120, +0.00110] | 0.96 | 0.958 | screen< |

## Map (the point of Phase 0)

- **Rotation non-linearity is the one live model-residual axis.** `current_gap_shape`
  (log-gap + short-hinge@14d + long-hinge@70d) is a RE-EXPRESSION of `days_since_last` (which the
  model already has), yet it shows clean out-of-fold residual signal — the booster's use of the raw
  gap has not captured the hinge shape. Validates codex #4 (re-expression matters for a finite-depth
  GBM). `prior_gap_log` (the prior race's own gap) is a weaker echo of the same axis.
- **Market edge ≠ model residual.** `tataki_2` and `prev_finish_reversion` were among the strongest
  MARKET-lift folklore factors (0.925 / reversion) but are NULL here (CI crosses zero). lgbm-065's
  OOF p already captures them; the market misprices them but the model does not. Exactly codex #6.
- **Null on the model side:** `draw_venue` (k=20, overfits), `body_mass_going`, `weight_gain`.
- **`seasonal_sex` is CI-clean but below the frozen point bar** (−0.0009 vs −0.001). Per the frozen
  contract this is `screen<` — the threshold is NOT relaxed (constitution III). Recorded as a
  borderline live signal for Phase 1 consideration, not a PASS.

## Honest limit (carry into Phase 1)

The best effect (−0.00288) is **below the paired-eval 80%-power MDE (~0.004–0.006)** measured on
this window (073 SE ≈ 0.0022). The probe CI is tight only because the probe is low-variance; a real
`current_gap_shape` feature run through the 073 tri-value gate will be noisier and — on this effect
size — **NO_DECISION is the likely Phase-1 verdict** (070 precedent), unless the feature interacts to
produce a larger realized gain. Phase 0 says "rotation shape is the most promising residual axis," NOT
"it will be adopted."

## Phase 1 handoff

- One hypothesis = **rotation gap-shape** (current_gap_shape + prior_gap_log are one axis, not two).
- New pre-registration required (phase0 output must not relax phase1 thresholds).
- Adoption gate = 073 evaluation-contract v2 (tri-value). Effect-size realism above must be stated
  in that pre-registration.

---

## Append-only correction + de-risk (2026-07-24, post codex Phase-1 review)

### Provenance fixes (codex P0)
- The report's `window` field is the **training-LOAD range** (2008→); the **evaluation** window is
  `eval_first_valid_year=2019 … 2026-07-12` (OOF valid races). Both are now recorded in the report
  JSON (`training_load_window`, `eval_first_valid_year`, `eval_window_note`). No numbers changed.
- Per-fold prequential γ (`gammas_by_fold`) + `gamma_sign_stable` are now saved per candidate
  (`per_fold_gamma_sign_stability` was pre-registered as a diagnostic but had been dropped from the
  JSON). Regenerated deterministically from the cached OOF (`--reuse-cache`, identical ΔNLL/CI).

### De-risk: the rotation residual is MONOTONE, not the folklore hinge shape
Codex's cheap pre-training de-risk: recover per-fold γ and g(d)=γᵀh(d) for `current_gap_shape`.

- `gap_log` γ is **stable and positive across all 7 folds** (+0.074 … +0.125) — the −0.00288 signal
  is real, not fold-specific noise.
- `gap_hinge_short(14d)` and `gap_hinge_long(70d)` γ are ≈0 (±0.005, signs wobble) — the hinges
  contribute almost nothing.
- g(d) is **monotonically increasing in gap in every fold** (d=3→+0.04..0.19, d=365→+0.64..0.71).

**Interpretation**: the folklore's non-monotone shape (short-gap penalty + mid-gap sweet spot,
which held vs the MARKET) does NOT exist in the MODEL residual. The model's residual is a simple
**monotone under-rating of longer-rest horses**. What carried the probe gain was the monotone
`log(1+d)` term alone.

### Disposition: STOP (user decision 2026-07-24)
A stable-but-MONOTONE residual fails codex's "stable AND clearly non-monotone → residual head"
criterion. As a tree feature it is maximally washout-prone (a monotone transform of the existing
`days_since_last`); as a residual head it is realizable but is a small monotone calibration offset
— NO_DECISION-likely under 073 (effect −0.00288 < MDE ~0.004–0.006) and ROI-neutral (accuracy is a
dead ROI lever, [[lgbm-065-roi-ceiling-confirmed]]). Per codex ("if g_fold(d) is monotone/unstable,
stop without full training"), Phase 1 is **not entered**. No FEATURE_VERSION bump; no model trained.

The 081 probe (pure `residual_probe.py` + `folklore-probe` CLI + cached OOF) is retained as reusable
screening infrastructure. A future `monotone_constraint` on `days_since_last`, or a residual-head
calibration, would each be a NEW hypothesis requiring its own pre-registration.
