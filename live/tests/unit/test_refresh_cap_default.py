"""T029 regression (live side): refresh_range must inherit the CORE capped default.

The rollout hole this pins: refresh_range calls betting's ``recommend_backfill`` core directly,
so a CLI-layer default never reached it — the first post-flip backfill generated 238 uncapped
win groups. The default now lives on the core signature; this guard fails if refresh_range ever
starts passing ``win_odds_cap`` explicitly (which would silently detach it from the product
default again)."""

from __future__ import annotations

import inspect

from horseracing_betting.cli import recommend_backfill
from horseracing_betting.recommend import DEFAULT_WIN_ODDS_CAP


def test_core_default_is_capped():
    assert inspect.signature(recommend_backfill).parameters["win_odds_cap"].default \
        == DEFAULT_WIN_ODDS_CAP == 21.0


def test_refresh_range_does_not_override_the_core_default():
    import horseracing_live.orchestrate as orch
    src = inspect.getsource(orch.refresh_range)
    call = src.split("recommend_backfill(")[1].split(")")[0]
    assert "win_odds_cap" not in call
