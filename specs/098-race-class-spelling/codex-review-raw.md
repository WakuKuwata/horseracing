1. Primary/secondary

- **Verdict:** Keep the real post-cutover retrained comparison primary. Pseudo-cutovers may support interpretation but must not cause ADOPT.
- **Reasoning:** Real A/B measures the deployment policy’s total effect—including removal of the learned regime flag. That is not confounding for prediction performance, only for explaining the mechanism. Historical pseudo-cutovers are better powered but not exchangeable with the netkeiba switch and its coverage failures.
- **Missed risk:** Arm C alone is not a clean separation. Use a 2×2: split/normalised × source indicator absent/present. `source_regime` is not leakage if immutable and known at prediction time, but it is a brittle provenance/calendar shortcut; direct coverage indicators are preferable.
- **Settling test:** Pre-register contrasts `B−A` (total deployment effect), `normalised+source − split+source` (spelling effect holding source constant), and their interaction. Material interaction or real-window harm means NO_DECISION/REJECT regardless of pseudo results.

2. Repair/versioning

- **Verdict:** “Data repair, no version bump” is not defensible. The +0.0293 replay harm proves this is a model-visible semantic change.
- **Reasoning:** Compatibility is determined by model behaviour, not whether humans call the strings aliases. A column-name hash cannot protect this boundary.
- **Missed risk:** “Retrain → promote → in-place backfill” creates mismatch windows and makes rollback to the split model unsafe.
- **Settling test/guard:** Build an immutable canonical feature view alongside the old one. Bind every model to a semantic-version ID plus transform hash and ordered categorical-vocabulary hash; serving must reject mismatches and mixed-version rows. Atomically switch model+view, retaining the old matched pair for rollback. Test 0%, 50%, and 100% migration states and assert only exact model/view pairs can serve. This is effectively a version bump even if named differently.

3. Canonical representation

- **Verdict:** Use explicit JRA-VAN canonical tokens, with NFKC only as a lookup key—not as emitted feature values.
- **Reasoning:** This minimizes historical churn and avoids rewriting `Ｇ１` and 2,365 `ｵｰﾌﾟﾝ` rows.
- **Missed risks:** Older artifacts still require their original transform. LightGBM categorical codes are model-local; each artifact must carry its exact ordered `pandas_categorical` mapping. `重賞` is a coarse legacy value, not an alias for a specific grade. Netkeiba `オープン` is semantically a mixture of JRA-VAN `ｵｰﾌﾟﾝ` and `OP(L)`, so normalizing it to `ｵｰﾌﾟﾝ` is not spelling-only.
- **Settling test:** Run an exhaustive observed-token crosswalk and collision test, then serialize/reload every supported artifact and verify category mappings and predictions. Preserve/quarantine `重賞`; never infer `OP(L)` from `オープン`. Unless Listed status is independently recovered, exclude open races from this repair or map them to an explicit `OPEN_UNSPECIFIED`.

4. Most likely wrong verdict

- **Verdict:** The largest risk is transporting an average effect across non-exchangeable data-quality regimes.
- **Reasoning:** Pseudo-cutoffs and pre-repair post-cutover races can make the spelling useful or harmful as a regime proxy, even though that relationship may not persist after the 2026-08-22 repairs.
- **Missed risk:** Pooling can hide a sign reversal—especially if one cutoff, the broken-coverage era, or open/listed races dominate.
- **Settling check:** Pre-register a transportability gate: effects must retain direction across each pseudo-cutoff, source/coverage strata, and leave-one-cutoff-out pooling. Any sign reversal or normalization×regime interaction exceeding δ forces NO_DECISION.
