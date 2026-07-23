# Codex design review — 079 EV-weighted training (retrospective kill-test)

> Source: codex-cli 0.144.1, `codex exec --sandbox read-only`, 2026-07-22. Internal independent second-opinion agreed (no material disagreements). Prompt: EV-weighted training design review.

## Recommendation

**NO-GO as proposed.** Specifically: reject per-horse PL gradient weighting, reuse of the current 064 verdict, and candidate registration based on closing-odds evidence.

**Conditional GO** for one preregistered, retrospective, negative-result-tolerant spike using a coherent race/stage-weighted PL loss and a new paired model-policy gate. A historical pass would justify prospective shadow evaluation—not shipping.

## Prioritized findings

### 1. Blocker: option (a) is not a valid PL objective

The current implementation accumulates listwise stage gradients and then multiplies each row by `dataset.get_weight()` ([cond_logit.py:227](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cond_logit.py:227), [cond_logit.py:242](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cond_logit.py:242)).

For unequal horse weights:

\[
g_i'=w_i(p_i-y_i)
\]

then generally:

\[
\sum_i g_i' \ne 0
\]

and the cross-partials are asymmetric unless all \(w_i\) in the choice set are equal. Therefore this is not the gradient of a scalar PL likelihood. Predictions would still numerically softmax to Σ=1, but the fitted objective would lose the shift-invariant listwise semantics.

The external tutorial’s construction is based on binary logloss, so its row weights do not transfer mechanically to PL ([tutorial](https://note.com/dijzpeb/n/n1afb70e3c981)).

| Option | Verdict | Reason |
|---|---|---|
| (a) Per-horse grad/hess multiplier | **Reject** | Not a coherent PL loss; breaks zero-sum stage gradients |
| (b) Race scalar | **Best option** | \(L_r'=\alpha_rL_r\) is a valid weighted PL likelihood |
| (c) Binary objective | Control only | Mathematically valid, but changes both objective and weighting; no longer native listwise probability training |

Recommended form: derive a race scalar from the cap-eligible region, such as `max baseline EV among odds<21`, and apply it to **stage 1 only** because the target policy is WIN. Keep stages 2/3 at their existing weights ([cond_logit.py:103](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cond_logit.py:103)). Normalize race weights to mean 1 inside each training window so LightGBM regularization strength is not changed merely by increasing total gradient scale.

This is coherent but blunt: it prioritizes whole races, not one horse. There is no simple horse-specific multiplier with the tutorial’s semantics that remains a valid PL likelihood.

### 2. Blocker: the current 064 gate cannot judge this experiment

Conceptually, betting-policy performance is the correct primary family of evidence. Literally, `eval/policy_gate.py` is not the needed gate:

- It accepts one model’s rows and compares uncapped EV against odds-capped EV ([policy_gate.py:124](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/policy_gate.py:124)).
- Adoption is based on point recovery, fold majority, and worst-fold tolerance ([policy_gate.py:166](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/policy_gate.py:166)).
- Its report has no bootstrap CI ([policy_gate.py:49](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/policy_gate.py:49)).
- The production PL version of 064 is still marked unexecuted, and default activation incomplete ([tasks.md:103](/Users/kuwatawaku/workspace/horseracing/specs/064-odds-cap-betting-policy/tasks.md:103)).

Required replacement: active versus weighted candidate, freshly refit on identical outer folds and scored under the **same fixed** `EV≥1, odds<21` policy and odds snapshot.

Primary should be paired net-profit per opportunity or a cluster-resampled recovery difference. Because the arms may place different numbers of bets, bootstrap whole race-days and recompute each arm’s payout/stake ratio inside every replicate; do not feed ordinary per-race differences into the existing mean-difference bootstrap.

Pre-register:

- Exact base recipe, weight formula, cap, threshold and fixed stake.
- Race/horse population and missing-odds handling.
- Primary estimand and CI rule.
- Bootstrap unit, seed, replicate count and block-width sensitivity.
- Minimum settled bets/days, worst-period guard, leave-one-winner-out and payout-concentration guard.
- A single `ADOPT/REJECT/NO_DECISION` rule.

The repo’s own ROI redesign already specifies same-race, same-snapshot, fixed-stake paired profit and day/meeting cluster CI ([model-accuracy-roi-redesign-proposal.md:418](/Users/kuwatawaku/workspace/horseracing/docs/plan/model-accuracy-roi-redesign-proposal.md:418)).

### 3. Closing odds: not direct label leakage, but not operationally valid evidence

Closing odds are predictive of the outcome; that correlation alone is not leakage. The problem is availability and provenance.

The repository states that historical odds were bulk-loaded closing/final values and that no historical pre-race snapshots exist ([065 spec:13](/Users/kuwatawaku/workspace/horseracing/specs/065-prospective-shadow-log/spec.md:13)). The market baseline is explicitly marked a leaky/result-time reference ([market_gate.py:66](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/market_gate.py:66)).

Therefore:

- Closing-odds weighting is acceptable only as **retrospective privileged-information/market-distillation research**.
- It is not acceptable as proof that the model improves at the operational decision time.
- A historical improvement may be preferentially closing-oracle-favoured because only the candidate was trained to that closing-defined region.

Using 060’s \(q\) does not repair this: it comes from the same closing odds. It is also not EV:

\[
p_i o_i = \frac{p_i}{q_i Z}, \qquad Z=\sum_j 1/o_j
\]

Use actual frozen bet-time decimal odds for an EV experiment. Reuse 060’s complete-field validation/devig machinery for consistency, and use \(q\) for market-disagreement diagnostics. If weighting on \(p/q\), call it a different “market disagreement” experiment.

Do not combine weighting and the 060 offset initially. That doubles market use and destroys attribution. Offset models also require target-race odds at inference and fail closed without them ([win_model.py:137](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/win_model.py:137)).

### 4. Constitution II permits this only as an explicit market-aware experiment

This is not an absolute constitutional ban: Constitution II allows a separately specified market experiment with explicit timing, leakage, and evaluation rules ([constitution.md:48](/Users/kuwatawaku/workspace/horseracing/.specify/memory/constitution.md:48)). Feature 060 is the precedent.

But “odds are not a feature” is insufficient. Training weights change the fitted estimand, so the model is market-aware.

Required guardrails:

- Separate spec and recipe identity: `market-aware-training`, `retrospective-closing-distilled`.
- Never active/default; no automatic promotion.
- Keep failed or underpowered runs as evidence artifacts, not registered models.
- Metadata must include base OOF digest/attestation, odds snapshot digest, source and temporal class, formula, cap, center/tau, normalization, coverage rule, probability stage and code SHA.
- Prediction marker distinct from 060, e.g. `mkttrain=evw-v1;weights=<digest>`.
- Active/default prediction byte parity.
- No odds or weights in feature columns/snapshots.
- Missing odds/OOF p: whole-race neutral weight or identical exclusion in both arms; no partial-field maximum.
- Recommendation paths must reject this model unless an explicit experiment/model version is selected.

“Candidate” alone is not isolation: feature 057 allows non-active models to be explicitly selected. The registry has only `candidate` and `active`, not an experimental/rejected state ([enums.py:72](/Users/kuwatawaku/workspace/horseracing/db/src/horseracing_db/enums.py:72)). Artifact-only before a passed gate is safer.

### 5. OOF p removes data leakage, but not selection leakage

A frozen, unweighted, market-independent OOF bundle is a sound one-shot nuisance estimate: `predict_over_folds` freshly refits on each outer training set before predicting its valid fold ([foldfit.py:41](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/foldfit.py:41)).

Requirements:

- Freeze the base recipe and content-addressed OOF artifact first.
- Each training row’s p must come from a model trained strictly before that row, including same-day exclusion.
- Never iterate weights from the weighted candidate.
- Define treatment of early rows without OOF coverage.
- Fit final probability calibration on an unweighted chronological calibration set.
- Pin the probability stage used for EV; 078 distinguishes raw serving p from betting post-calibration p ([078 plan:29](/Users/kuwatawaku/workspace/horseracing/specs/078-oof-manifest-generation/plan.md:29)).

For `(center,tau)`, the cleanest experiment fixes one pair before evaluation. Otherwise selection must happen inside each outer training window using inner prequential folds only.

Also, the proposed description is numerically misleading: with center `0.9`, tau `0.02`, weight is 1.5 at EV=.9, about 1.92 at .95, and 1.99 at 1.0. It does not remain approximately 1 throughout EV<1.

### 6. Tail-calibration non-degradation should be a MUST

Winner-NLL **improvement** should not be a MUST because ROI is the primary hypothesis. Nevertheless:

- Winner NLL should have a preregistered catastrophic/noninferiority bound.
- Top2/top3 noninferiority is required if the candidate is exposed as a general probability model.
- Overall ECE and, especially, decision-region/tail calibration should be MUST guards.

The danger is real: the OOF p used to identify high-EV horses is already inflated in the longshot tail—about 3.5× for `q<.05` ([047 spec:46](/Users/kuwatawaku/workspace/horseracing/specs/047-segment-diagnostics/spec.md:46)). The proposed rule also upweights high-EV horses above the odds cap even though they will never be bet. That is objective-policy misalignment.

Measure, on fixed baseline-defined masks:

- Odds bands, q bands, baseline-EV bands around 1.0, and `odds<21`.
- Predicted wins versus observed wins and O/E ratio with day-cluster CI.
- Tail ECE, NLL and Brier.
- Bet-count/coverage changes and selection Jaccard.
- Effective sample size and fraction of races receiving elevated weight.
- Leave-one-winner-out recovery and threshold-crossing sensitivity.

Do not define the confirmatory calibration subset from each candidate’s own bets; the repository already warns that this creates arm-dependent populations ([codex-073-review.md:103](/Users/kuwatawaku/workspace/horseracing/docs/plan/codex-073-review.md:103)).

## Required tests

- Finite-difference gradient against an explicit scalar-weighted PL loss.
- Per-stage zero-sum gradient and constant-logit-shift invariance.
- Fail-closed rejection of unequal row weights.
- Loop/vectorized objective parity with race/stage weights.
- Uniform-weight byte parity with current PL.
- Σwin=1, top2/top3 ordering and marginal consistency.
- Strict-past/same-day OOF provenance and own-result mutation invariance.
- Validation outcomes/odds cannot affect weight generation or hyperparameter selection.
- Missing-odds, earliest-fold and full-field behavior.
- Exact candidate/active race-set and policy parity.
- Ratio-bootstrap determinism, underpowered `NO_DECISION`, payout concentration and leave-one-winner-out.
- Metadata completeness, candidate isolation and active byte parity.

## Honest prior

The realistic prior is **null or worse recovery**, probably accompanied by tail-calibration degradation. Reweighting does not add information; it reallocates capacity using a noisy signal already known to be most wrong near the proposed decision region.

Scope this like 062 and 070: a bounded kill-test where rejection is a successful result. Feature 062 stopped after its PL spike and preserved the work unwired ([062 tasks:27](/Users/kuwatawaku/workspace/horseracing/specs/062-rating-features/tasks.md:27)); 070 froze favourable point estimates as rejected when their CI crossed zero ([070 status:9](/Users/kuwatawaku/workspace/horseracing/docs/plan/070-status-freeze.md:9)).

Final disposition:

- **No-go:** current per-horse PL design, closing-odds adoption claim, existing 064 verdict reuse.
- **Conditional go:** one retrospective race/stage-scalar spike, aligned to `odds<21`, fixed hyperparameters, frozen OOF p, new paired gate.
- **No shipping after historical pass:** advancement requires prospective decision-time odds and untouched confirmation evidence.

The independent second-opinion review reached the same conclusion; there were no material disagreements.
