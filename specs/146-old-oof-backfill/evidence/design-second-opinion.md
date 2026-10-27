# Independent design review: old OOF backfill

Reviewer: `oof146_review`. Reviewed 2026-10-25 after reading AGENTS.md,
the current 135 plan, the 2026-10-24 attribution report, and the frozen
142/143 fitting and scoring implementations. No model fits or production
changes were performed by this reviewer.

The proposed experiment is appropriate for the requested proposal C: fit
77-feature unweighted mentors for 2013–2018 using only 2007 through the
preceding year and all 15 fixed seeds, average their **raw** win probabilities,
append these to the unchanged 2019–2025 mentor material, and refit the fixed
143 weighted recipe for 2019–2026. A two-stage freeze is needed because the
new mentor outputs do not exist at the first freeze. Bind the intended teacher
and candidate procedures before fitting any mentors, then bind every completed
mentor artifact and the exact combined teacher material before fitting candidates.

Required checks:

- Preserve the same complete fields and keys, 77 feature order, training-only
  category maps, 300 rounds, seeds 1–15, and upper date 2026-06-26. The actual
  final race date is 2026-06-21. Teacher targets must be strictly later than
  their own training cutoff, with no early stopping on their targets.
- Reconstruct every teacher seed mean independently and compare all old
  2019–2025 teacher rows and values bitwise against the frozen 143 material.
  Do not substitute race-normalized probabilities for raw probabilities.
- Verify the full annual candidate weight vector independently using 143's
  fixed EV center 1.2, width 0.2, amplitude 1, complete-race availability,
  informative-row global mean normalization, and exact-one neutral weights.
  No race normalization from 144 or new pace features from 145 belong here.
- Inspect all 90 mentor, 120 new candidate, and 240 reused control receipts
  and their model/prediction/feature-spec hashes. Replay saved model bodies
  on independently encoded feature matrices and independently score the same
  four arms, two fixed contrasts, paired daily bootstrap, and sensitivities.
- Freeze the candidate versus matched current baseline and candidate versus
  original 143 weighted model contrasts before scoring. Bonferroni family size
  is two separately for each reported metric. Keep the saved current ensemble
  as an explicitly separate reference.

Independent population inventory from the fixed source:

| Segment | Horse rows | Races | Role |
|---|---:|---:|---|
| 2007–2012 | 287,052 | 19,911 | Remains neutral |
| 2013–2018 | 287,252 | 19,935 | New mentor targets |
| 2019–2025 | 321,271 | 23,264 | Unchanged existing mentor material |

There are no design blockers. The following interpretation limits must remain:

1. The 2019 candidate now has informative old OOF, so it is **not** a neutral
   parity control and is allowed to differ from the 142 baseline and original
   143 weighted model.
2. Adding old teacher coverage also changes the informative normalization
   denominator, so weights on previously covered rows can change even when
   their raw teacher values are identical. A profit difference does not isolate
   coverage from normalization or nonlinear model fitting effects.
3. The fixed model shape was selected after inspecting later historical years
   in 142. Strict annual training and training-only category maps do not turn
   this backtest into an untouched historical hyperparameter selection process.
4. Closing odds are a settlement and feature proxy; resulting profits are
   hypothetical. Day intervals are conditional on the fixed seed ensemble and
   do not erase repeated historical research or estimate retraining uncertainty.
