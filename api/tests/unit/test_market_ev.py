"""Feature 137: pure assembly of the market-ev response (no DB).

The threshold constant is the single source of truth and the comparison is strict; field changes
and odds changes are detected by set / value comparison; provenance aggregates are max / AND.
"""

from __future__ import annotations

import ast
import datetime
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from horseracing_api import market_ev
from horseracing_api.market_ev import MARKET_EV_THRESHOLD, build_market_ev, exceeds_threshold
from horseracing_api.schemas import MarketEvAvailable, MarketEvUnavailable

_SRC = Path(__file__).resolve().parents[2] / "src" / "horseracing_api"
_T0 = datetime.datetime(2026, 9, 27, 3, 0, tzinfo=datetime.UTC)


@dataclass
class _Row:
    horse_id: str
    horse_number: int | None
    expected_return: Decimal
    odds_used: Decimal
    race_id: str = "202609270511"
    model_version: str = "mev-binary-v2"
    odds_observed_at: datetime.datetime = _T0
    result_pending_at_compute: bool = True
    logic_version: str = "mev-v1"
    computed_at: datetime.datetime = _T0


def _rows():
    return [
        _Row("H2", 2, Decimal("1.2"), Decimal("4.0")),
        _Row("H1", 1, Decimal("0.9"), Decimal("2.5")),
        _Row("H3", 3, Decimal("1.2001"), Decimal("8.0")),
    ]


def _current(rows=None, **over):
    odds = {r.horse_id: r.odds_used for r in (rows or _rows())}
    odds.update(over)
    return odds


def test_threshold_constant_is_one_point_two_and_strict():
    assert MARKET_EV_THRESHOLD == 1.2
    assert exceeds_threshold(1.2) is False
    assert exceeds_threshold(1.2001) is True
    assert exceeds_threshold(float(Decimal("1.2"))) is False


def test_threshold_is_defined_only_in_market_ev_module():
    # the numeric 1.2 threshold must not be re-declared elsewhere in the API source
    for path in _SRC.rglob("*.py"):
        if path.name == "market_ev.py" and path.parent == _SRC:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    assert getattr(target, "id", "") != "MARKET_EV_THRESHOLD", path


def test_no_rows_is_not_computed():
    out = build_market_ev("202609270511", [], {"H1": Decimal("2.5")})
    assert isinstance(out, MarketEvUnavailable)
    assert (out.reason, out.threshold) == ("not_computed", 1.2)


def test_available_orders_by_horse_number_and_flags_strictly():
    out = build_market_ev("202609270511", _rows(), _current())
    assert isinstance(out, MarketEvAvailable)
    assert [h.horse_number for h in out.horses] == [1, 2, 3]
    assert [h.exceeds_threshold for h in out.horses] == [False, False, True]
    assert out.is_pseudo is True
    assert out.threshold == MARKET_EV_THRESHOLD
    assert out.odds_changed_after_compute is False


def test_missing_horse_number_sorts_last():
    rows = [*_rows(), _Row("H0", None, Decimal("1.0"), Decimal("3.0"))]
    out = build_market_ev("202609270511", rows, _current(rows))
    assert [h.horse_id for h in out.horses] == ["H1", "H2", "H3", "H0"]
    assert out.horses[-1].horse_number is None


@pytest.mark.parametrize(
    "current",
    [
        {"H1": Decimal("2.5"), "H2": Decimal("4.0")},                   # a stored horse left
        {**_current(), "H9": Decimal("12.0")},                         # a new started horse
    ],
)
def test_started_set_difference_is_field_changed(current):
    out = build_market_ev("202609270511", _rows(), current)
    assert isinstance(out, MarketEvUnavailable)
    assert out.reason == "field_changed"


def test_odds_change_is_a_value_comparison():
    assert build_market_ev("r", _rows(), _current(H1=Decimal("2.50"))).odds_changed_after_compute \
        is False
    assert build_market_ev("r", _rows(), _current(H1=2.5)).odds_changed_after_compute is False
    assert build_market_ev("r", _rows(), _current(H1=Decimal("2.6"))).odds_changed_after_compute \
        is True


@pytest.mark.parametrize("missing", [None, Decimal("0.0"), 0.9])
def test_started_horse_without_valid_odds_is_odds_unavailable(missing):
    """The training job never computes a race with a started horse lacking valid odds, so stored
    rows would be from an earlier price: they must not be shown (spec: オッズ欠損は表示しない)."""
    out = build_market_ev("r", _rows(), _current(H3=missing))
    assert isinstance(out, MarketEvUnavailable)
    assert out.reason == "odds_unavailable"
    assert out.threshold == MARKET_EV_THRESHOLD


def test_provenance_aggregates_max_and_and():
    rows = _rows()
    rows[0].odds_observed_at = _T0 + datetime.timedelta(minutes=5)
    rows[1].result_pending_at_compute = False
    rows[2].computed_at = _T0 + datetime.timedelta(seconds=1)
    rows[2].logic_version = "mev-v1;latest"
    out = build_market_ev("r", rows, _current(rows))
    assert out.odds_observed_at == _T0 + datetime.timedelta(minutes=5)
    assert out.result_pending_at_compute is False
    assert out.computed_at == _T0 + datetime.timedelta(seconds=1)
    assert out.logic_version == "mev-v1;latest"


def test_mixed_model_versions_are_rejected():
    rows = _rows()
    rows[0].model_version = "mev-binary-v1"
    with pytest.raises(ValueError):
        build_market_ev("r", rows, _current(rows))


def test_module_imports_no_ml_or_write_package():
    tree = ast.parse(Path(market_ev.__file__).read_text())
    modules = {
        (node.module or "") for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    for banned in ("lightgbm", "numpy", "pandas", "horseracing_training", "horseracing_betting"):
        assert not any(m.startswith(banned) for m in modules), banned
