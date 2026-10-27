# 148 postprediction normalization: independent design review

Reviewer: `normalize148_review`. Design reviewed before candidate scoring.

- The two candidates are exactly `mean15(raw baseline77 p) / race_sum(mean15(raw baseline77 p))` and the same transformation for original143 weighted baseline③. Average the15 seeds first, then normalize each complete started field, then select from normalized EV and allocate integer stakes. No candidate mixing, feature changes, retraining, threshold search, ratio search, calibration fitting or extra arms.
- Retain raw matched baseline77 and original③ as the two controls. The original saved current ensemble is a separate reference because its small prediction difference from the matched baseline is already known.
- All five arms must share24,855 races,343,883 horses and807 eligible dates including no-bet dates. Use the unchanged single-win EV>1.2 policy,1,000-yen per-race budget,100-yen units,max10 horses and floor(10/n) units with retained cash. Use closing-odds proxy settlement consistently.
- Bind protocols, current implementation sources and all240 source-fit receipts/predictions before candidate scoring. Independently reconstruct the15-seed means from240 saved fits and verify720 model/feature/prediction artifact hashes, recipe identities and chronology.
- Predeclare four comparisons: each normalized candidate minus raw baseline77 and minus original③. Use4,000 paired day bootstrap draws,seed20261025 and family4 Bonferroni correction separately per metric. Saved-seed uncertainty does not cover repeated historical model selection.
- Predeclare removal of known race202608030111, removal of the maximum positive difference race, matched January1–June21 annual periods and pre2026/2026 splits as retrospective diagnostics only.
- Winner NLL should remain unchanged within each parent because that metric already normalizes raw probabilities for evaluation. Raw binary logloss and raw ECE may change. Test complete-field sums≈1, parent nonmutation, malformed fields, duplicate identifiers, zero sums, NaN and invalid probability rejection.
- No design blocker. This remains a historical exploratory study with `can_adopt=false`, `untouched_holdout=false` and `eligible_for_verdict=false`; no production adoption follows automatically.

The independent final audit uses the earlier independent143 scoring helpers without importing148 implementation code. It reconstructs both normalization candidates, all five policy/bet/quality/year/risk results, four paired bootstrap contrasts, sensitivities and six preselected saved-body replays (2019seed1,2024seed8,2026seed15 for each source arm). No model fitting is performed.
