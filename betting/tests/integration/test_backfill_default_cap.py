"""T029 regression: the CORE ``recommend_backfill`` must default to the capped policy.

The rollout hole this pins: live/orchestrate.refresh_range calls the core directly (not the
CLI), so a CLI-layer default silently left that path on the legacy uncapped policy — the first
post-flip production backfill generated 238 uncapped win groups before this was caught."""

from __future__ import annotations

import inspect

from horseracing_betting.cli import recommend_backfill
from horseracing_betting.recommend import DEFAULT_WIN_ODDS_CAP


def test_core_backfill_defaults_to_capped_policy():
    sig = inspect.signature(recommend_backfill)
    assert sig.parameters["win_odds_cap"].default == DEFAULT_WIN_ODDS_CAP == 21.0
