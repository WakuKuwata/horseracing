1. **Compat routing**

- **Verdict:** Sound only if compatibility is artifact-directed and explicitly allowlisted—not selected from the registry’s current default.
- **Missed risk:** A missing representation marker, wildcard compatibility, or future `023→024` fallback could silently choose the wrong representation.
- **Settling test:** Exhaustive artifact-version × serving-version contract test: only the pinned active `features-021` artifact may use raw compat; missing/unknown metadata and every undeclared version pair must fail closed. Golden-test byte equality and predictions against the old build.

2. **Simulation estimand**

- **Verdict:** Correct for the marginal causal effect of deploying the three-entry transform, not for the total effect of the source migration.
- **Missed risk:** The historical pseudo-split cannot reproduce interactions with `OP(L)` merging, other feature drift, vendor-routing changes, missingness, or changed race composition. Those interactions could make the spelling useful—or harmful—only in the real regime.
- **Settling test:** In the real-window guard, hold every feature row identical and change only `race_class`; additionally stratify paired loss by `OP(L)` history/source regime and major drift clusters. Large interaction or opposite-sign strata blocks adoption.

3. **Washout**

- **Verdict:** Keep the washout window, but add a separate transition-regime window starting at each cutoff. Do not pool the two estimands.
- **Missed risk:** The washout excludes the unseen-category shock—the period most analogous to the current active model—and may approve a representation that helps after retraining but hurts immediately.
- **Settling test:** Pre-register, per cutoff: (a) frozen/pre-cutoff-trained model scored during `[cutoff, cutoff+1y)`, and (b) the planned mature retrained window. Require the transition result not to confidently contradict adoption.

4. **Vocabulary hash**

- **Verdict:** No. It verifies artifact integrity, not whether live values belong to the artifact vocabulary.
- **Missed risk:** Known values can silently become unseen through normalization/order/type drift and follow LightGBM’s missing branch while the booster hash remains valid.
- **Settling test:** Before categorical coercion, emit a per-prediction `unknown_category_mask` plus representation/version and privacy-safe token fingerprint. Continue serving, but alert on known-token misses or abnormal unknown rates. Injection tests must show unseen categories still score while the audit fires.

5. **Largest practical risk**

- **Verdict:** Representation dispatch becoming dependent on mutable defaults is the likeliest incident; it can also contaminate the experiment’s “raw” arm.
- **Missed risk:** “Raw” is not a stable concept once `build_training_matrix` canonicalizes according to the current registry.
- **Settling test:** Require an explicit representation argument at every training, simulation, and serving boundary; pre-register golden fixtures for raw-021, canonical-023, and rejected combinations. Assert exact token values—not merely changed rows—and reject NaNs introduced by categorical assignment.
