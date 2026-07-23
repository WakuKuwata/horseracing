# 079 — EV-weighted training: retrospective kill-test (PRE-REGISTRATION)

**Status:** PRE-REGISTERED (locked before any run). Everything below is fixed *before* seeing
results. Any change after the first run must be recorded as an amendment with a reason, and the
run re-labelled.

**Type:** Negative-result-tolerant *evidence* experiment (same class as
[062](../062-rating-features/) and [070](../070-past-market-bundles/)). **Rejection is a
successful outcome.** This is a bounded kill-test, not a shipping feature.

**Second opinion:** [docs/plan/codex-079-review.md](../../docs/plan/codex-079-review.md)
(codex, NO-GO as originally proposed → conditional-GO for exactly this scoped form; internal
independent review agreed). Origin: dijzpeb「予測精度を上げたのに回収率が下がる…」(#52).

## Amendment A1 — pre-run refinements from the codex implementation review (before any run)

Recorded for transparency; made **before the first run** (no results seen), after a codex review
of the implementation surfaced under-specified / fail-open guards. These tighten the locked design;
none relaxes a gate.
- **Tail guard metric (§4.1):** the tail non-degradation guard is evaluated as **calibration-in-
  the-large** `(ΣE − ΣO)/N` on each mask (signed; >0 = over-prediction), guarded as
  `cand ≤ base + 0.02`. This replaces the ambiguous "O/E ratio" wording, which fails open when
  observed winners = 0. The `E/O` ratio is still reported as a diagnostic. Masks use odds/q only
  (identical for both arms → baseline-defined). A mask with N=0 in both arms is not evaluable
  (no tail exposure); an evaluable-but-undefined value fails closed.
- **Verdict ordering:** a MUST-guard failure returns **REJECT even when underpowered** (a
  catastrophic tail/NLL failure is decisive regardless of sample size).
- **Power check:** the 40-day minimum is checked **per arm** (bet race-days), and bootstrap
  replicates with zero stake in either arm are dropped (`b_used` recorded).
- **Empty cap-eligible set:** a complete-field race whose whole field is odds≥21 is **neutral
  (α=1)**, excluded from mean-1 normalisation (matches §2.2).
- **Diagnostics deferred (non-MUST, "reported" only):** top2/top3 non-inferiority, effective
  sample size, leave-one-winner-out recovery, threshold-crossing sensitivity, tail day-cluster CI
  are NOT computed in this artifact-only run (listed in the report's `deferred_diagnostics`). The
  MUST guards (tail calibration-in-the-large, winner-NLL non-inferiority, α≡1 byte-parity) are
  fully implemented.

---

## 1. Hypothesis & honest prior

**Hypothesis (H1).** PL top-k optimises win accuracy uniformly across all horses, but bets are
only placed on the ~few % of horses with EV > 1 under an `odds < 21` cap. Concentrating training
capacity on the *decision-relevant races* (via a per-race loss weight) may improve realised win
recovery of the existing EV+odds-cap policy, relative to the unweighted baseline, on identical
walk-forward folds.

**Honest prior (recorded now, before running).** Null or *worse* recovery, likely with
tail-calibration degradation. Reweighting adds no information; it reallocates capacity using a
noisy signal (OOF p) already known to be most wrong exactly in the longshot tail
(≈3.5× overestimation for q<0.05, [047 spec:46](../047-segment-diagnostics/spec.md)). The app's
own true-OOS finding is that the market is unbeatable (recovery < 1.0 all bands,
[[betting-roi-landscape-2026-07]], [[true-oos-validation-2026-04]]). We expect to reject.

**Hard ceiling (recorded now).** Even a historical PASS **cannot ship**. Historical odds are
bulk-loaded closing/final values; no pre-race snapshots exist ([065 spec](../065-prospective-shadow-log/spec.md)).
Closing-odds EV weighting is *retrospective market-distillation research only* — it cannot prove
improvement at operational (pre-race) decision time. Shipping would require prospective
decision-time odds (a 065-class data pipeline) + fresh untouched confirmation evidence.

---

## 2. Weight design (FIXED)

### 2.1 Estimand of the weight
Per-**race** scalar `α_r` applied to the whole race's PL listwise loss: `L_r' = α_r · L_r`.

- **Rationale (codex #1):** per-*horse* grad/hess multiplication is **NOT** a valid PL objective —
  `Σ_i w_i(p_i−y_i) ≠ 0`, breaking the zero-sum listwise gradient. A per-race *constant* weight
  (same value for every horse in the race) scales the entire listwise loss by a constant and **is**
  a valid weighted PL likelihood. This is the only tutorial-inspired form that stays a coherent PL
  loss. The tutorial's per-horse binary weighting does not transfer to PL.
- **Implementation consequence:** the weight vector passed to `lgb.Dataset(weight=)` must be
  **constant within each race**. This is enforced by construction (weight computed per race, then
  broadcast to that race's rows) and asserted by a fail-closed test.

### 2.2 Weight formula (FIXED — no tuning on results)
For race `r`, over horses with valid OOF p **and** valid odds **and** `odds_i < 21` (cap-eligible
set `C_r`):

```
EV_i   = p_oof_i × odds_i                      # OOF p, closing odds
ev_r   = max_{i ∈ C_r} EV_i   (if C_r empty → ev_r = 0 → neutral weight)
raw_r  = 1 + sigmoid((ev_r − center) / tau)
α_r    = raw_r / mean_{r ∈ window}(raw_r)       # normalise to mean 1 within each training window
```

- **center = 1.0, tau = 0.10** — FIXED before running (single pair, no selection).
  - Note (codex #5): the tutorial's `center=0.9, tau=0.02` gives w≈1.5 already at EV=0.9 and is a
    near-step at EV=1; we deliberately use a **gentler, honestly-centred** curve at the true
    break-even EV=1.0. `raw_r ∈ (1,2)`, ≈1.5 at EV=1.0.
  - If we ever want data-driven `(center,tau)`, selection MUST happen inside each outer training
    window via inner prequential folds only — **out of scope for this spike** (fixed pair only).
- **Normalisation to mean 1** (codex #1): keeps total gradient scale unchanged so LightGBM
  regularisation strength is not altered merely by upweighting.
- **Cap alignment** (codex #6): `ev_r` uses only `odds < 21` horses, so we do **not** upweight
  races whose only high-EV horse is an un-bettable longshot (avoids objective↔policy mismatch).
- **Missing data:** any race missing OOF p or odds for its field → `α_r = 1` (neutral), applied
  **identically in both arms**. No partial-field maximum.

### 2.3 Whole-race vs stage-1-only
This spike uses the **whole-race scalar** (all 3 PL stages scaled by `α_r`) because it is the
minimal provably-valid form injectable through the existing `Dataset(weight=)` seam. codex's
stage-1-only refinement (align to WIN policy) is recorded as a **follow-up** if the spike shows
any signal; it requires modifying the objective to weight stage 1 separately and is NOT done here.

### 2.4 OOF provenance (FIXED — codex #5)
- OOF p comes from a **frozen, content-addressed, unweighted, market-independent** bundle via
  `generate_oof_bundle` ([oof_generate.py:42](../../training/src/horseracing_training/oof_generate.py))
  / `predict_over_folds` ([foldfit.py:41](../../eval/src/horseracing_eval/foldfit.py)): fresh
  fold-wise refit on outer-train only, strict-past + same-day exclusion.
- Base recipe = current active model recipe (resolve at implementation time; do NOT guess).
- **Never** iterate weights from the weighted candidate (no bootstrapping the treatment onto itself).
- Rows in the earliest fold(s) without OOF coverage → neutral weight `α_r = 1`, both arms.
- Final probability calibration is fit on an **unweighted** chronological calibration set.

---

## 3. The gate (FIXED — codex #2) — NEW paired model↔policy gate

The 064 `policy_gate` is **not** reusable here (single-model uncapped-vs-capped, no bootstrap CI,
no active-vs-candidate paired comparison). A new paired gate is defined:

- **Two arms**, both freshly refit on **identical** expanding outer folds:
  - `baseline` = current recipe, **unweighted**.
  - `candidate` = same recipe + race-weight from §2.
- **Fixed policy for both:** bet WIN where `EV = p_calibrated × odds ≥ 1.0` and `odds < 21`;
  flat unit stake; same odds snapshot (closing) and same started+priced population.
- **Primary estimand:** paired **recovery difference** `Δ = recovery(candidate) − recovery(baseline)`,
  where `recovery = Σ(odds·won over bets) / n_bets`. (Net-profit-per-opportunity reported alongside.)
- **CI:** cluster bootstrap by **race-day** (arms place different bet counts → recompute each arm's
  payout/stake ratio inside each replicate; do **NOT** feed per-race mean differences into an
  ordinary mean-difference bootstrap). Seed FIXED, replicate count FIXED (10,000), block = race-day.
- **Verdict (single rule, FIXED):**
  - **ADOPT** — `Δ > 0` and 95% CI lower bound `> 0` and majority of year-folds improve and
    worst-fold `Δ ≥ −tol` and **all MUST guards (§4) pass**.
  - **REJECT** — CI upper bound `≤ 0`, or any MUST guard fails.
  - **NO_DECISION** — CI straddles 0 (underpowered), or settled bets/days below minimum
    (min 200 settled win bets AND 40 race-days per arm; else NO_DECISION).
- **Absolute recovery > 1 is explicitly NOT the bar** (closing-oracle bias). Only the *relative*
  paired difference is interpreted. Even ADOPT ⇒ prospective evaluation, not shipping (§1 ceiling).

---

## 4. MUST guards (FIXED — codex #6)

Measured on **baseline-defined** masks (never on the candidate's own bets — arm-dependent
populations are forbidden, [codex-073-review.md:103](../../docs/plan/codex-073-review.md)):

1. **Tail calibration non-degradation (MUST):** O/E ratio and tail ECE on `odds ≥ 21` and
   `q < 0.05` masks must not worsen beyond a pre-registered tolerance vs baseline.
2. **Winner-NLL non-inferiority (MUST, not improvement):** candidate winner NLL ≤ baseline + 0.005
   (catastrophic bound). Improvement is NOT required.
3. **top2/top3 non-inferiority (MUST if the model is ever exposed as a general prob model):**
   ≤ baseline within tol. (For an artifact-only spike it is a reported diagnostic.)
4. **Uniform-weight byte parity (MUST):** `α_r ≡ 1` reproduces current PL predictions byte-for-byte.

Diagnostics (reported, not gating): odds/q/EV-band O/E with day-cluster CI, bet-count & coverage
change, selection Jaccard vs baseline, effective sample size, fraction of races upweighted,
leave-one-winner-out recovery, threshold-crossing sensitivity.

---

## 5. Isolation & leak guardrails (FIXED — codex #3, #4)

- **Constitution II:** permitted only as an *explicitly market-aware* experiment (060 precedent).
  "Odds are not a feature" is insufficient — weights change the fitted estimand, so the model IS
  market-aware. Guardrails:
  - **Artifact-only evidence — do NOT register a model version** (not even CANDIDATE). Rationale:
    057 lets non-active models be explicitly selected, and the registry has no experimental/rejected
    state ([enums.py:71](../../db/src/horseracing_db/enums.py)); a registered row is not isolation.
    Results live as a content-addressed evidence artifact + this pre-registration + a status note.
  - **No feature/snapshot changes:** odds and weights never enter `feature_cols` / `feature_hash` /
    feature snapshots. `FEATURE_VERSION` unchanged.
  - **Do NOT combine** the weight with the 060 market **offset** (double market use destroys
    attribution; offset also needs target-race odds at inference and fails closed without them,
    [win_model.py:137](../../training/src/horseracing_training/win_model.py)). Offset OFF for both arms.
  - **Distinct marker** if any prediction is ever emitted: `mkttrain=evw-v1;weights=<digest>`
    (distinct from 060's `mkt=logq`). Active/default serving prediction byte parity preserved.
  - **Metadata (evidence artifact):** base OOF digest/attestation, odds-snapshot digest + source +
    temporal class (closing), weight formula, center/tau, normalisation, coverage rule, probability
    stage used for EV, code SHA.
- **Selection-leak controls:** validation outcomes/odds cannot affect weight generation or the
  fixed `(center,tau)`; OOF strict-past + same-day exclusion; base recipe & OOF artifact frozen first.

---

## 6. Implementation seams (from recon — NOT yet changed)

- **Weight injection (minimal):** `mkt_odds` already in the training frame
  ([dataset.py:54](../../training/src/horseracing_training/dataset.py)); PL/cond_logit already apply
  `dataset.get_weight()` to grad/hess ([cond_logit.py:91-97,242-246](../../training/src/horseracing_training/cond_logit.py))
  but no weight vector is ever set. Thread a `weights` arg through
  `WinModel.fit`→`_fit_softmax`→`lgb.Dataset(..., weight=w_sorted)`
  ([win_model.py:59,99,126](../../training/src/horseracing_training/win_model.py)), sorted with the
  same `order` as X/y/offsets. Compute the per-race weight at
  [predictor.py:250](../../training/src/horseracing_training/predictor.py) alongside `model_offsets`.
- **Recipe knob:** add `ev_weight` scheme to `ModelRecipe`
  ([recipe.py:34](../../training/src/horseracing_training/recipe.py)) + `RecipeFactory.fit` +
  `train-evaluate` CLI ([cli.py:496](../../training/src/horseracing_training/cli.py)). Default OFF
  → recipe_hash / behaviour byte-unchanged when off.
- **New gate:** new `eval/` module (e.g. `ev_weight_gate.py`) — do NOT extend `policy_gate.py`.
  Reuse day-cluster bootstrap machinery from `eval/.../paired.py` and the ROI-redesign paired-profit
  spec ([model-accuracy-roi-redesign-proposal.md:418](../../docs/plan/model-accuracy-roi-redesign-proposal.md)).

---

## 7. Required tests (FIXED — codex)

1. Uniform-weight (`α_r≡1`) **byte parity** with current PL (predictions + recipe_hash when off).
2. Fail-closed rejection if a weight vector is **not constant within a race**.
3. Loop vs vectorized PL objective parity under race weights.
4. Per-stage zero-sum gradient / constant-logit-shift invariance preserved under a valid race scalar.
5. Weight is constant within race; normalised to mean 1 within window.
6. OOF provenance: strict-past + same-day exclusion; own-result mutation does not change weights.
7. Validation outcomes/odds cannot affect weight generation or `(center,tau)`.
8. Missing-odds / earliest-fold / empty cap-set → neutral weight in **both** arms.
9. Paired gate: identical race-set & policy across arms; ratio-bootstrap determinism (seeded);
   underpowered → NO_DECISION; leave-one-winner-out; payout-concentration guard.
10. Σwin=1, top2/top3 ordering & marginal consistency unchanged.
11. No model_version row is created (artifact-only isolation); active serving byte parity.

---

## 8. Disposition (record after the single run)

`ADOPT` / `REJECT` / `NO_DECISION` per §3, with the recovery Δ + CI, MUST-guard results, and the
tail-calibration table. On REJECT/NO_DECISION: freeze favourable point estimates as rejected (070
precedent), preserve code unwired + evidence artifact (062 precedent). On ADOPT: the only next step
is a prospective decision-time-odds evaluation — **not** promotion.

### DISPOSITION — 2026-07-23 (the single pre-registered run)

**Verdict: `NO_DECISION`** — the EV-weighted candidate is statistically indistinguishable from the
unweighted baseline; the honest prior (null / no improvement) is confirmed. **Kill-test complete;
this is a successful null result.** Evidence artifact:
[artifacts/oof-079/… → specs/079-ev-weighted-training/evidence.json](evidence.json)
(code SHA `1e4aa67`, base model lgbm-063, run wall-clock ~45 min on the full DB).

- **Population:** 61,745 valid OOS races, ~107k win bets/arm, 2,004 race-days, 19 year-folds
  (2008–2026). Well-powered (above the 200-bet / 40-day minimums), so NO_DECISION reflects a
  genuine null effect, not insufficient data.
- **Primary estimand:** Δrecovery = candidate − baseline = **−0.0054**, 95% day-cluster bootstrap
  CI **[−0.0138, +0.0031]** (10,000 replicates, all used). CI straddles 0 ⇒ NO_DECISION.
  Baseline recovery 0.8388 vs candidate 0.8334 (both < 1.0, as expected under closing-oracle bias).
- **Per-fold:** 10/19 folds improved; effect mixed and small (early folds 2009–2011 negative,
  later years mixed ±0.02). worst-fold Δ −0.045.
- **MUST guards — both PASS (no degradation):** winner-NLL non-inferior (base 2.1116 → cand 2.1123,
  within +0.005); tail calibration-in-the-large NOT worsened and marginally BETTER on both masks
  (odds≥21: base 0.01055 → cand 0.01047; q<0.05: base 0.01075 → cand 0.01066). **The 047 tail-
  overconfidence-amplification risk did not materialise.**
- **Selection:** Jaccard 0.777 — the candidate bets on largely the same horses as the baseline
  (the reweighting barely moves the decision set), which explains the near-zero recovery effect.
- **Smoke (2007–2011, discarded for the official verdict):** REJECT (Δ −0.0275, CI below 0). The
  early-only window showed the candidate clearly worse; the effect washes out over the full window
  as the base model has more data and the OOF-EV reweighting matters less.

**Action taken (062/070 precedent):** freeze this as a null result; **do NOT register any model
version** (artifact-only isolation held — `save_model_version` refuses an ev_weight predictor); the
code stays committed but unwired (`ev_weight` default off = byte-identical). No promotion, no
prospective follow-up is warranted (a null historical effect does not justify the 065-class
pre-race-odds pipeline investment). Levers axis unchanged: the market remains unbeaten and
decision-region reweighting adds no detectable value on top of the current PL top-k model.
