# Prospective Holdout — Pre-Registration Format (DORMANT)

**Feature**: 073 US4 (FR-018) | **Created**: 2026-07-15 | **State**: `DORMANT`

This is the **器 (empty template)** for a future prospective confirmatory holdout. It is
deliberately **not started**: the clock begins only when all `start_preconditions` are met. Until
then the state is `DORMANT` (equivalently `AWAITING_CAPTURE`), and no rows are collected.

> Why DORMANT and not STARTED: pre-race odds capture is not running (redesign proposal §2.9). A
> holdout marked STARTED with zero data would be a false "in progress" indicator (research D6).

## State

```yaml
state: DORMANT            # DORMANT | AWAITING_CAPTURE | ACTIVE  (this feature never sets ACTIVE)
started_at: null
```

## Pre-registration record (to be filled BEFORE the clock starts)

```yaml
hypothesis: null          # e.g. "candidate policy X has non-negative net-profit vs no-bet"
feature_formula: null     # exact feature/model/policy definition under test
thresholds: null          # primary metric threshold + CI rule (pre-committed)
primary_metric: null      # e.g. race-level winner NLL paired diff; or fixed-100-yen net profit
stopping_rule: null       # pre-registered end date OR sequentially-valid rule (alpha spending)
time_to_signal_estimate:  # from redesign proposal §4.2 — computed BEFORE starting
  required_settled_bets: null
  required_calendar_months: null
```

## Start preconditions (ALL required before the clock starts)

```yaml
start_preconditions:
  pre_race_odds_capture_running: false   # continuous capture of pre-race odds (currently false)
  immutable_recipe_frozen: false         # candidate recipe/calibrator pinned (see Feature 074)
  gate_and_stopping_rule_registered: false
  contamination_rules_defined: false     # what invalidates a captured decision
  first_target_race_selected: null
```

## Scope note

The ROI ledger (`market_snapshot` / `decision_attempt` / `decision_bet` / `settlement`),
multi-arm shadow, and any real prospective collection are **out of scope for Feature 073** and,
per the redesign proposal, require a live pre-race odds feed and a **constitution V amendment**
(odds are currently overwrite-only, no snapshot history). This template only reserves the shape.
