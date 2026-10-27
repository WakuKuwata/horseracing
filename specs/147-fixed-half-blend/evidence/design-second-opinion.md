# 147 fixed half blend: independent design review

Reviewer: `blend147_review`. Scope reviewed before candidate scoring.

- Candidate is exactly `0.5 × mean15(raw baseline77 probabilities) + 0.5 × mean15(raw unweighted pace91 probabilities)`. The input pace arm is original143 `pace`, not145 `weighted_pace`. No new fitting, weight changes or blend-ratio search.
- Mean seed probabilities first, then blend, then the established EV>1.2/integer-stake policy. Do not normalize candidate probability sums before EV selection: that is the deferred proposal2. Normalized winner NLL remains an evaluation metric only.
- Compare with the matched142 baseline and original143 weighted baseline③, with pace-only as a mechanism control. The original saved current ensemble is a separately identified reference.
- Bind original input/source protocols plus all360 source receipts and prediction files before scoring. Validate each receipt's three body/prediction/feature hashes and reconstruct all seed means rather than trusting a comparison parquet alone.
- Keep the common24,855 races,343,883 horses and807 eligible dates, including no-bet dates. No extra2026 results or changed settlement. Equal1,000-yen per-race budget,100-yen units, max10 horses, floor(10/n) units and retained cash.
- Three fixed candidate-minus-baseline/③/pace comparisons,4,000 paired day bootstrap repetitions, seed20261025, family3 Bonferroni correction separately per metric. Saved-seed/bootstrap intervals do not address repeated historical model selection.
- Predeclare known-race202608030111 removal, maximum-positive-difference removal, matched January1–June21 years and pre2026/2026 split as retrospective diagnostics only.
- Fixed raw-probability blend is faithful to the requested proposal1. No design blocker. Historical closing-odds proxy results remain research evidence with `can_adopt=false`, `eligible_for_verdict=false`, `untouched_holdout=false`.

An independent audit script reconstructs360 existing source fits/1,080 artifact hashes, exact candidate predictions and all five policies/quality/year/risk/bets, and recomputes the three contrast bootstrap families and sensitivities. Nine preselected saved-body replays (years2019/2024/2026, seeds1/8/15, each source arm) verify stored probabilities without retraining.
