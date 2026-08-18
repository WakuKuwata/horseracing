# Adoption gate — evaluation contract v3

Single definition: `eval/src/horseracing_eval/gates.py`. Both the standard paired path
(`paired.paired_eval`) and the regime path (`regime_paired.evaluate_regimes`) call it.

## Why v3 exists

Reproducing the full pre-registered formula from the 088 record (26,338 races / 821 race-days,
SE 0.000863) gave these ADOPT probabilities, assuming the true effect is uniform across periods
and subgroups:

| true effect | v2 ADOPT prob |
|---:|---:|
| 0 (null) | 0.006 |
| −0.001 | 0.065 |
| −0.002 | 0.24 |
| −0.003 | 0.44 |
| −0.005 | 0.70 |

The gate advertised a 2.5% error rate and ran at 0.6%, rejecting roughly three of every four
genuine improvements. The dominant term was **not** the primary CI — it was the subgroup guard.
`2026_only` had a CI upper arm of ~0.0071 against a 0.005 margin, so `ci_high < margin` was
reachable only by proving **superiority** of 0.0021 on 66 race-days: a harder test than the whole
821-day primary. Across every recorded verdict the guard has **never** returned FAIL; its only
observed effect was turning an inconclusive run into REJECT (088).

## The conditions

`gate.adopted` is the conjunction of:

| sub-gate | condition | source |
|---|---|---|
| `effect_beats_delta` | `diff < -min_effect_delta` (point estimate) | v3 (was 091-only) |
| `ci_upper_below_zero` | `ci_high < 0` | 068 |
| `recent_no_evidence_of_harm` | no recent window has `ci_low > margin` | v3 (was a sign test) |
| `top2_noninferior` / `top3_noninferior` | diff ≤ 0.0005 | 068 |
| `calibration_noninferior` / `_not_emergency` | ΔECE ≤ 0.001, ECE < 0.05 | 068 |

Verdict: `gate.adopted AND subgroup_guard_status NOT IN {FAIL, MISSING}`.

`min_effect_delta` constrains the **point estimate**; the interval condition is the separate
`ci_high < 0`. It is not `ci_high < -delta`.

## Subgroup states

`three_way(ci_low, ci_high, margin, point=...)`:

| state | condition | blocks adoption |
|---|---|---|
| `PASS` | `ci_high < margin` — non-inferiority established, valid at any width | no |
| `FAIL` | `ci_low > margin` — confidently worse | **yes → REJECT** |
| `INCONCLUSIVE_LOW_PRECISION` | no conclusion and `ci_high − point ≥ margin` (or no CI) | no, disclosed |
| `NO_DECISION` | no conclusion from a test that could have concluded | no, disclosed |
| `MISSING` (guard level) | never computed — a wiring fault | **yes → NO_DECISION** |

Precision is judged on the **upper arm**, not the half-width: these are percentile bootstrap
intervals and are not symmetric, and it is the upper arm that decides PASS.

## What adoption claims, and what it does not

- `subgroup_assurance: "full"` (every critical subgroup PASS) — the intersection-union claim
  "non-inferiority holds in every critical subgroup" is established.
- `subgroup_assurance: "partial"` — **only** "no degradation beyond the margin was detected".
  The FAIL arm is itself weak at the recorded precisions: a true +0.010 degradation in
  `2026_only` is detected about 28% of the time. Read `critical_residual_risk` — the worst
  degradation each inconclusive interval still admits — alongside it.

The same asymmetry applies to `recent_no_evidence_of_harm`: passing it is not "the recent window
is fine".

## Freezing a config

Copy `gate-config-v3.template.json`, fill it, record the hash, then run with
`--confirmatory --gate-config-hash <hash>`. `--from` and `--to` must both be passed and must match
`eval_window`.

**Fill in `power` before freezing.** A gate frozen without knowing its own MDE produces a REJECT
that carries no information. At the current window size the whole conjunction needs roughly 0.006
for 80% power; the primary CI alone needs 0.0024.

A v2-stamped config fails closed under `--confirmatory`. That is deliberate: its numbers were
judged under different rules, and silently re-judging them would break the immutability of the
verdict it recorded. Re-freeze as v3 rather than editing the version string on an old file.

## Measured limit: v3 cannot see a recent-regime-only degradation

`scripts/gate_power_heterogeneity.py` (run 2026-08-17, same 088 parameters). Scenario: the
candidate improves everywhere **except** the current-regime year, where it degrades by `h`. The
current-regime year is 66 of 821 race-days, so the overall mean hides it almost completely — a
`h = +0.010` degradation moves the overall by only +0.0008.

| true effect elsewhere | h (2026 harm) | true overall | v2 adopts | **v3 adopts** | guard detects |
|---:|---:|---:|---:|---:|---:|
| −0.005 | 0 | −0.0046 | 0.128 | 0.999 | 0.001 |
| −0.005 | +0.005 | −0.0042 | 0.001 | **0.969** | 0.029 |
| −0.005 | +0.010 | −0.0038 | 0.000 | **0.679** | 0.318 |
| −0.005 | +0.020 | −0.0030 | 0.000 | 0.002 | 0.998 |

The FAIL arm — the only thing that vetoes under v3 — detects a `+0.010` degradation **32%** of the
time, `+0.008` 15%, `+0.005` 3%. It only becomes reliable at `+0.015` (87%).

Consequence, stated plainly: if one in ten candidates that improve overall degrade the current
regime by +0.010, then **5.1% of v3 adoptions are harmful** (v2: 0.0%). At one in two, 33%.

This is a real cost of the v3 relaxation and it is not mitigated by anything in this contract.
What v2 bought instead was *nothing adopted at all*: it took a uniform −0.003 improvement only
43.5% of the time (v3: 92.4%). Its zero harmful-adoption rate is a corollary of its rejection
rate, not a safety property.

**Neither setting solves the underlying problem: 66 race-days cannot support a decision at either
margin.** No gate rule fixes that. The two real fixes are:

1. **Measure in the deployment regime** rather than measuring historically and guarding — this is
   exactly what 091 did by making the serving regime PRIMARY (`regime_paired.py`). Where a feature
   targets the current regime, use that path, not the standard one plus a subgroup guard.
2. **Shorten or reweight the evaluation window toward the current source regime.** The standard
   window gives ~92% of its weight to pre-cutover years. That is a pre-registration decision, not a
   code default, so it is not made here.

Until one of those happens, treat `subgroup_assurance="partial"` as **"this run cannot speak about
the current regime"**, and read `critical_residual_risk` before adopting.

## 2026-08 hardening pass (multi-lens review)

Four independent review lenses were run over the harness. **No defect was found that had distorted
a recorded number** — every past verdict stands. What they found was fail-OPEN behaviour, fixed
here:

| fixed | was |
|---|---|
| the regime path now returns NO_DECISION | it collapsed everything non-ADOPT to REJECT and never checked `min_eval_days`, so an underpowered run — the case v3 exists to relabel — came out as "the candidate is worse" (found by two lenses independently) |
| `--confirmatory` requires the hash AND both window ends | they were checked only *if supplied*, so the flag alone verified nothing beyond "a v3 config exists" |
| a regime run without `--confirmatory` is stamped `exploratory` | it was stamped verdict-eligible, indistinguishable from a confirmed run |
| a candidate whose mask rate is `None` is `diagnostic` | only a *differing* rate was; a pre-091 recipe stayed verdict-eligible against a config whose frozen arm is m=0.5 |
| conflicting `--num-threads` is refused | an explicit value silently beat the frozen determinism contract |
| probabilities are validated before clipping | `p=1.2` or `inf` clipped to `1-1e-15`, scoring winner NLL ≈ 0 — a corrupted arm looked like a perfect predictor |
| equal-mass ECE never exceeds `bins` | the remainder became an extra final bin of as few as 1 row, entering the weighted sum at up to 1/n |
| empty / non-finite race-day clusters raise | a replicate drawing only empty days produced a NaN percentile reported as a number |
| partial-ingest races leave the started-all arrays | they contributed fabricated 0 labels to the top2/top3 and ECE gates (2 races of 26,411) |
| both paths share one recent-window reference date | the regime path used the last day carrying a paired diff, not the last valid race date |

Deliberately NOT changed: OOF target encoding uses chronological leave-one-block-out including
later blocks — that is not an evaluation leak (the outer-valid year enters no encoder) and it
matches serving, where the encoder is fit on all history. The TE prior including the held race's
own labels is ~2e-5 of a 700k-row mean and changing it would break recipe-hash compatibility.
`race_count_v1` splitting a race day across the model/calibration boundary is 073's documented
choice, with `race_day_v1` available.

## The promotion boundary

A verdict decides **adoption**; it does not by itself decide **activation**. `adoption_status=active`
additionally requires, at `save_model_version`:

1. the legacy 4-metric gate passes (unchanged), **and**
2. a v3 verdict that is verdict-eligible, says `ADOPT`, and has `subgroup_assurance="full"`.

Pass it with `train-evaluate --verdict <report.json>` (either report shape). Without it the row is
saved as a CANDIDATE and the reason lands in `metrics_summary["promotion"]`. Nothing raises — a
trained model is never lost to a contract error.

Until 2026-08 the legacy gate was the only thing between a fresh model and production, so a
candidate that v3 would REJECT could go live by beating four point estimates. `partial` assurance
staying a candidate is the same rule 085 and 091 followed by hand: a run that cannot speak about
the current regime waits for evidence that can.

## Measured limit: the CI does not contain retraining noise

`scripts/seed_variance_probe.py` (run 2026-08-18). The same two-arm comparison was re-run six
times changing ONLY the training seed — same data (pinned parquet), same recipe, `num_threads=1`,
a near-null contrast (`drop=pace_first3f`, 3 columns / 17 splits in the active model). The true
effect is fixed, so the spread is pure retraining noise.

| | |
|---|---:|
| measured diffs across six reruns | −0.003755 … +0.002075 |
| **seed noise SD** | **0.001816** |
| same-fold bootstrap SE | 0.002239 |
| **ratio** | **0.81** |

The day-cluster bootstrap resamples RACES; it cannot see the variation that comes from refitting
the model. Combining the two (independent) sources widens the interval by 29% at fold level.

Extrapolating to the 088 window (8 folds, treating fold-level noise as independent — NOT measured):
seed noise ≈ 0.00064 against a reported SE of 0.00086, a ratio of 0.74, giving a combined SE of
≈0.00108 and an all-gates MDE nearer **0.0030** than 0.0024.

What this means for reading past results:

| | effect | in units of seed noise (fold level) |
|---|---:|---:|
| 036 target encoding | −0.0134 | 7.4× |
| 085 arm E | −0.0128 | 7.0× |
| grade repair | −0.0129 | 7.1× |
| prize backfill | −0.0098 | 5.4× |
| 069 F02 | −0.0057 | 3.1× |
| 070 F04 | −0.0015 | **0.8×** |
| 088 | −0.0006 | **0.3×** |

The adopted results are 5–7× the retraining noise and are safe. **The 0.0005–0.002 band that the
shelved features occupied is at or below it** — those measurements were never distinguishable from
re-running the same experiment with a different seed. That is a stronger statement than
"underpowered": more eval races would not have fixed it, because the noise is in the fit, not the
sample.

Not fixed here. The options are to average each arm over k seeds (noise/√k, k× the compute), to
resample seeds inside the interval (needs the same refits), or to state the wider MDE and stop
claiming resolution below it. Whichever is chosen belongs in a pre-registration, not in a default.

## Other known limits

- Two nested recent windows each get a one-sided harm test, so the false-FAIL rate exceeds a
  single window's. Uncorrected: at SE ≈ 0.0014 against a 0.005 margin a false FAIL is a ~3.5σ
  event. A simultaneous (max-T) construction is the fix if that ratio narrows.
- Per-candidate error rates say nothing about the family-wise rate across many candidates
  evaluated on the same window.
