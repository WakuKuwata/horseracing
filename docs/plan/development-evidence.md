# 2008–2026 is Development Evidence (not a confirmatory holdout)

**Feature**: 073 US4 (FR-017) | **Created**: 2026-07-15

## Statement

The JRA-VAN period **2008–2026** has been looked at repeatedly across dozens of features (020,
023, 026, 030–033, 041, 042, 047–049, 056, 058–062, 068–070, …) to select features, thresholds,
objectives, calibration methods, and bundle definitions. Even though every fold is a time-series
OOS split, the *period as a whole* is a **development set** for the research process: individual
per-fold or per-bundle bootstrap CIs do NOT correct for the multiple comparisons accumulated by
choosing among many specs while watching the same years (research D6, 070 research self-note).

Therefore, when interpreting any future adoption verdict:

- **2008–2026 results are development evidence.** They are legitimate for building intuition,
  ranking candidates in an inner walk-forward, and killing bad ideas — but a bp-level win over
  this period is **not** confirmatory of out-of-sample value.
- A genuine confirmatory decision requires **unused data** and a **pre-registered** hypothesis,
  metric, threshold, and stopping rule — see `prospective-holdout-preregistration.md` (currently
  DORMANT; it cannot start until pre-race odds capture is running).
- Large candidate searches belong in the inner walk-forward; only a single pre-committed winner
  should be judged on any confirmatory window.

## Practical consequence

Do not treat a favourable 2019–2026 (or any 2008–2026 subwindow) point estimate as proof. The
honest ledger for "does this actually help / make money" is the DORMANT prospective holdout, which
is gated on a live pre-race odds feed that does not yet exist (see the redesign proposal §2.9).
