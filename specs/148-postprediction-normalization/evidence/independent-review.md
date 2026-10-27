# 148 independent final review

Reviewer: `normalize148_review`. Verdict: **PASS**, no blocker. Evidence: `independent_final.json`; audit implementation: `independent_audit.py`. The audit imports only the earlier independent143 helpers, not148 implementation code, and performs no fitting.

- Confirmed frozen protocol SHA `bf199e0daa0816fe568895bb3fd3e7dc306a1695e183dbd1267430c269361261` and all513 input/source bindings. Reconstructed240 saved source fits and checked720 model/prediction/feature artifact hashes, receipts, parameters, feature order, chronology and provenance.
- Rebuilt both15-seed raw means, independently normalized every complete race using field loops and matched saved candidate predictions within floating sum-order tolerance. Both candidates sum to1 within4.45e-16. Within-parent per-race winner NLL differs by at most8.89e-16, confirming mathematical invariance rather than predictive improvement.
- Independently recomputed all five policy selections, integer stakes, payouts, quality/ECE, annual results, risk measures and probability-mass diagnostics. The24,855 races,343,883 horses and807 dates are common, including no-bet dates.
- Recomputed four fixed4,000-draw day-bootstrap contrasts, seed20261025, family4 corrections, and all predeclared sensitivity diagnostics. All profit and ROI corrected intervals contain zero. Tiny same-parent NLL intervals below zero reflect floating point arithmetic and are not evidence of improved accuracy.
- Six preselected saved-model replays (2019seed1,2024seed8,2026seed15 × both parents) reproduced64 saved prediction rows each with maximum absolute difference0. No fitting, threshold search or extra candidate was introduced.

The normalized baseline produced121.38% ROI and719,290-yen profit, versus114.45% and558,800 yen for its raw parent (+160,490 yen). Normalized③ produced127.35% and966,190 yen, versus116.84% and663,730 yen for original③ (+302,460 yen). Raw saved current is a separate114.54%/561,530-yen reference.

The financial mechanism is lower spending that exceeds the accompanying payout reduction. NLL is unchanged; raw ECE does not improve. This is a purchase-rule transformation, not new model learning. For③ the improvement occurs before2026 (+348,260 yen), while2026 worsens by45,800 yen. Removing known race202608030111 from both sides leaves +407,160 yen, so the gain is not inherited from that prior influential hit. Maximum drawdown and maximum consecutive loss-making races nevertheless increase for both parents.

Result-review interpretation agrees with the audited evidence. One terminology correction was requested: `max_consecutive_losing_races` counts consecutive races with negative profit, so label it maximum consecutive **loss-making** races rather than consecutive misses. Closing-odds proxy settlement, repeatedly examined historical data and saved-seed uncertainty remain limitations; `can_adopt=false`, `eligible_for_verdict=false`, `untouched_holdout=false` remain appropriate.
