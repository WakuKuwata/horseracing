# 129 Data Model

## Build freeze and member receipt

Buildfreeze pins cutoff, originalresearch hashes/runtime, sixorderedjobs, actualfeaturecolumns and recipes, outcome-completeness andtrainpopulation. Eachreceipt pins its model/calibrator/preprocessor/metadata SHA, job/source/runtime, fitcost, OOFsufficiency androundtripresult. Only verifiedcomplete receipts canresume; partialdirectories are retained asfailed evidence.

## Candidate bundle

One profile125_new_joint_mixed6_v1, sixorderedmembers joint42/43/44,anchor42/43/44, weight1/6 each, shadow-only, adoptionfalse. Manifest binds model files, correctionterms/coefficients/source-year, featuremeaning raw, full/prunedcolumn contracts, training/coeffcutoffs, inferenceoperations andstudy125 evidence. Relative paths resolvewithinbundle root; missing/outside/tamperedfiles fail. BundlefileSHA is externalidentity; no cyclicselfhash.

## Shadow record

Append-only unique record containing captured_at/predicted_at, race_date/starttime if available, started IDs, inputregime+feature/historysnapshotSHA, exact candidateandanchor artifacts, anchor stage-discountartifact, both3heads andconsistency/readiness state. Rehearsal cannot claimprospective status. A matching timestamp alone doesnot substitute for pre-result andinputavailability checks.

## Confirmation

Manifest fixes candidatebundlehash/anchorbundlehash, seedmemberpairs andindependentreplicatecount, noisetransferassumption, powerplan, futurewindow/mindays, outcomeprotocol andcommonbudgetslot. States DRAFT→READY→RESERVED→COLLECTING→COMPLETED/NO_DECISION. Futureobservations unavailable meansCOLLECTING/NOT_READY, neverADOPT. Identity changes invalidate completion evidence; no automaticextension or refund.
