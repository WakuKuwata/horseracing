# 153 independent final review

Reviewer: `market153_review`. Verdict: pass; blockers: none.

The independent audit imported no153 implementation and performed no fitting. It used the independent143 scoring helper plus separate candidate receipt reconstruction, all-field normalization loops, paired-bootstrap and sensitivity implementations. Synthetic cap/tie/cash/label-independence/pairing self-checks passed before the final audit.

## Evidence checked

- All543 frozen input/source bindings, inherited142/143/148 protocols and their actual dependencies were verified. Protocol SHA256: `6f3574d814a4a26adb47958cd9c2750cd469682824fe6578ed8014d1eb6abe1d`.
- All360fits (candidate120, baseline120, original③120) and1,080 model/prediction/feature-spec hashes were checked. Candidate completion marker360 entries, full identities, model recipes, runtime, annual chronology, target/training sizes,300 rounds and saved-model roundtrip were verified.
- Candidate ordered74 features equal original77 minus exactly odds/popularity/odds_rank. Category maps equal the same-year/same-seed original③ maps, categorical feature order is unchanged, q and remaining market information remain. Candidate feature hashes and all120fit quality metrics were independently checked.
- Original OOF weights were independently rebuilt for all8 training years, including neutral older rows and strictly past OOF provenance. Their byte SHA and candidate metadata match the original③ for every seed/year. No74-feature OOF teacher was refitted.
- All three raw parents were independently mean15 reconstructed. Both normalized arms were rebuilt with separate complete-field loops, max absolute field-sum error4.44e-16. Normalization preserved candidate parent winner NLL. Candidate raw probability quality is diagnostic only; no raw candidate purchase policy was scored.
- All5arms' complete predictions, purchase keys, integer units, settlement, aggregate/year/EV-band quality, profit/risk, all3 paired-bootstrap contrasts and sensitivity diagnostics match saved results. Existing4controls' summaries match148 exactly. Same24,855races/343,883horses/807dates are used, including no-bet dates.
- Nine predeclared saved bodies (2019seed1,2024seed8,2026seed15 per raw parent) were replayed on64rows each:576 probabilities, bitwise exact, maximum difference0.
- Summary SHA256: `dfb4c7d51fcb1fd0f093d3dd8b93ddff603bbd1beffd269cafe1567f6e56bb9f`. Detailed independent evidence is `independent_final.json`.

## Interpretation checked

The74-feature candidate returns109.50% with profit322,600yen. It trails matched77 by236,200yen/4.95pt, original③ by341,130yen/7.33pt and original③ full normalization by643,590yen/17.84pt. It also trails saved current-reference by238,930yen/5.03pt. Lower capital deployment (3,394,500yen vs3,533,200yen for③full normalization) does not explain away the lower ROI.

Compared with③full normalization,7of8 annual profits are lower; before2026 difference is−515,610yen and2026 difference−127,980yen. The known105.7odds race contributes zero to that contrast; removing it leaves−643,590yen. Removing each contrast's maximum positive-difference race also leaves losses. Candidate maximum drawdown177,880yen exceeds③full normalization154,910yen. Setting its five largest payouts to zero gives−117,900yen, compared with+479,290yen for③full normalization. These are retrospective sensitivity diagnoses, not proposed trading exclusions.

Winner NLL1.927140 is slightly higher than77/③, so this candidate does not even improve the broad race-level probability metric. Three-column removal adds no information; remaining q/other market columns and original price-based weights prevent any claim of market independence. The experiment does not isolate a general causal value of those three columns beyond this fixed training recipe.

The family3 corrected95% interval for **common-budget profit difference vs③full normalization** is entirely negative: [−5.5641,−0.0310]percentage points. The corrected deployed-ROI difference interval is [−38.6898,+0.2830]points and includes zero. Profit/ROI corrected intervals for the other two contrasts also include zero. Therefore do not say all corrected intervals include zero, and do not claim the ROI disadvantage is established after multiplicity correction. The negative profit interval describes this repeatedly inspected historical development comparison; it is not untouched out-of-sample confirmation or a forecast.

No basis to replace the existing③full-normalization candidate or change production. Preserve `can_adopt=false`, `untouched_holdout=false`, `eligible_for_verdict=false`. Results remain single-win closing-odds-proxy hypothetical settlement. Production/UI/DB and the held historic time-specific odds collection are unchanged.
