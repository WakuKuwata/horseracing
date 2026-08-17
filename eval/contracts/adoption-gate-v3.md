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

## Known limits (not fixed here)

- The power table assumes a **uniform** effect. A candidate that improves overall while degrading
  one subgroup is not represented; that scenario needs its own simulation.
- Two nested recent windows each get a one-sided harm test, so the false-FAIL rate exceeds a
  single window's. Uncorrected: at SE ≈ 0.0014 against a 0.005 margin a false FAIL is a ~3.5σ
  event. A simultaneous (max-T) construction is the fix if that ratio narrows.
- Per-candidate error rates say nothing about the family-wise rate across many candidates
  evaluated on the same window.
