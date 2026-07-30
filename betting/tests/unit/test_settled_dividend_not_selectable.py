"""Settled exotic dividends must never reach BET SELECTION (results leak).

`exotic_odds` holds the FINAL DIVIDEND of the winning combination once a race is settled —
netkeiba's payout table lists winners only, never the full grid. If selection reads those rows,
the only priced selection is the one that won, so EV picks the winner by construction. The leak is
silent because the rows are indistinguishable from ordinary "real odds".

This stayed dormant while `exotic_odds` was empty. Backfilling 2,082 races of real dividends armed
it, so the boundary is now enforced: selection uses `load_selectable_exotic_odds` (empty for a
settled race), scoring keeps `load_real_exotic_odds`.
"""

from __future__ import annotations

import ast
import pathlib

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "horseracing_betting"

#: modules that CHOOSE bets — reading a settled dividend here is a results leak.
SELECTION_MODULES = ("exotic_recommend.py", "kelly_recommend.py")
#: modules that SETTLE bets — reading the dividend is exactly the point.
SCORING_MODULES = ("exotic_backtest.py", "kelly_backtest.py", "exotic_divergence.py",
                   "exotic_gate.py")


def _called_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_selection_paths_never_call_the_unguarded_dividend_loader():
    offenders = [m for m in SELECTION_MODULES
                 if "load_real_exotic_odds" in _called_names(_SRC / m)]
    assert not offenders, (
        f"settled dividends reachable from bet selection: {offenders}. "
        "Use load_selectable_exotic_odds, which refuses a race that already has a result."
    )


def test_selection_paths_use_the_guarded_loader():
    for m in SELECTION_MODULES:
        assert "load_selectable_exotic_odds" in _called_names(_SRC / m), m


def test_scoring_paths_keep_reading_real_dividends():
    """The guard must not be applied everywhere — settlement legitimately needs the dividend."""
    for m in SCORING_MODULES:
        assert "load_real_exotic_odds" in _called_names(_SRC / m), m


def test_guard_is_implemented_as_a_result_existence_check():
    src = (_SRC / "exotic_market.py").read_text(encoding="utf-8")
    fn = src.split("def load_selectable_exotic_odds", 1)[1].split("\ndef ", 1)[0]
    assert "RaceResult" in fn and "return {}" in fn, (
        "load_selectable_exotic_odds must return {} once the race has a result row"
    )
