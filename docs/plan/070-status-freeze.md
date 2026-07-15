# 070 Past-Market Bundles — Status Freeze (append-only supersession record)

**Feature**: 073 US4 (FR-016) | **Created**: 2026-07-15 | **Kind**: append-only supersession record

This is a **read-only, append-only** record of the frozen status of Feature 070. It does **not**
rewrite 070's own spec/plan/research or any past verdict — it references them by commit hash so
the historical record stays intact (constitution V, research D6).

## Status matrix (frozen)

| Bundle | Axis | OOS verdict (2019–2026, n≈22,597) | Wired? |
|---|---|---|---|
| F03 | pm_rank_robust (rank percentile) | **REJECTED** — diff −0.00234, CI upper +0.00039 (crosses 0) | No (unwired) |
| F04 | pm_expectation_residual | **REJECTED** — diff −0.00148, CI upper +0.00143 (crosses 0) | No (NOT_RUN → unwired) |
| F05 | pm_conditioned (support) | **REJECTED** — diff −0.00159, CI upper +0.00104 (crosses 0) | No (NOT_RUN → unwired) |

All three point estimates were favourable and all critical subgroups (2026/nk) PASSed, but the
block-bootstrap winner-NLL CI upper bound crossed zero every time = **not significant**. Under the
068/069 gate this is a **REJECT** (or NO_DECISION under the 073 tri-value contract), not an ADOPT.

## Supersession references (by hash, not rewritten)

- **FEATURE_VERSION**: bumped `features-018 → features-019` for 070, then **REVERTED** back to
  `features-018` after OOS rejection (027/062 precedent — a rejected bump would push
  `lgbm-064-f02acc` onto the serving compat path for no gain).
  - Revert commit: `81f5d9e` — `revert(070): FEATURE_VERSION 019->018 after OOS rejection of all bundles`
- **Registry**: `features/src/horseracing_features/registry.py` documents F03/F04/F05 as REJECTED /
  unwired; `FEATURE_VERSION = "features-018"`. The `pm_rank_robust` / `pm_expectation_residual` /
  `pm_conditioned` modules + unit tests are **preserved unwired** as the documented negative result.
- **Rationale (frozen)**: the past-market rank / residual / conditioned axes are ~redundant with
  F02 `log(q·N)` (069, adopted) + 058 raw rank. See `specs/070-past-market-bundles/` and the
  `accuracy-levers-exhausted-2026-07` memory.

## Contract note

Under the 073 evaluation contract, 070's verdicts are tagged `evaluation_contract_version=v1` and
are **immutable** — any v2 recomputation is reference-only and must not overwrite or re-classify
them (FR-015). This freeze record is the authoritative status; do not re-open 070 as an adoption
candidate without new, unused data and a fresh pre-registration (US4 / research D6).
