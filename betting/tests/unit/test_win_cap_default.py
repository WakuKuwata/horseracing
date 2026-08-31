"""Feature 064 T029: default-ON cap resolution + exact-token policy filters (codex-reviewed)."""

from __future__ import annotations

import argparse

import pytest

from horseracing_betting.cli import _resolve_win_odds_cap
from horseracing_betting.recommend import DEFAULT_WIN_ODDS_CAP


def _ns(**kw):
    return argparse.Namespace(**kw)


def test_unspecified_resolves_to_default():
    assert _resolve_win_odds_cap(_ns()) == DEFAULT_WIN_ODDS_CAP == 21.0


def test_explicit_value_passes_through():
    assert _resolve_win_odds_cap(_ns(win_odds_cap=15.0)) == 15.0


def test_opt_out_returns_none():
    assert _resolve_win_odds_cap(_ns(no_win_odds_cap=True)) is None


def test_both_flags_is_usage_error_not_precedence():
    with pytest.raises(SystemExit):
        _resolve_win_odds_cap(_ns(win_odds_cap=21.0, no_win_odds_cap=True))


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_values_rejected(bad):
    with pytest.raises(SystemExit):
        _resolve_win_odds_cap(_ns(win_odds_cap=bad))
