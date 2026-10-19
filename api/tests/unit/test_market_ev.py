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
from horseracing_eval import market_mix

from horseracing_api import market_ev
from horseracing_api.market_ev import MARKET_EV_THRESHOLD, exceeds_threshold
from horseracing_api.market_ev import build_market_ev as _build_market_ev
from horseracing_api.schemas import (
    MarketEvAvailable,
    MarketEvUnavailable,
    MarketMixAvailable,
    MarketMixUnavailable,
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "horseracing_api"
_T0 = datetime.datetime(2026, 9, 27, 3, 0, tzinfo=datetime.UTC)
_DAY = datetime.date(2026, 9, 27)
ENS = market_mix.MIX_SOURCE_MODEL_VERSION


def build_market_ev(*args, **kwargs):
    """The 137/138 tests below predate the race_date argument (Feature 141); they use 2026-09-27."""
    kwargs.setdefault("race_date", _DAY)
    return _build_market_ev(*args, **kwargs)


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
    win_prob: Decimal | None = None
    booster: str = "ensemble_2026.json"

    def __post_init__(self):
        if self.win_prob is None:  # stored expected_return is exactly win_prob × odds_used
            self.win_prob = self.expected_return / self.odds_used


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


# --------------------------------------------------------------------- Feature 141: market mix


def _ens_rows():
    return [
        _Row("H1", 1, Decimal("0.90"), Decimal("2.5"), model_version=ENS),
        _Row("H2", 2, Decimal("1.30"), Decimal("4.0"), model_version=ENS),
        _Row("H3", 3, Decimal("1.04"), Decimal("8.0"), model_version=ENS),
        _Row("H4", 4, Decimal("0.75"), Decimal("15.0"), model_version=ENS),
    ]


def _hand_mix(rows, coef):
    import math

    inv = [1 / float(r.odds_used) for r in rows]
    s = [coef.a * math.log(i / sum(inv)) + coef.b * math.log(float(r.win_prob))
         for i, r in zip(inv, rows, strict=True)]
    z = sum(math.exp(x) for x in s)
    return [math.exp(x) / z * float(r.odds_used) for x, r in zip(s, rows, strict=True)]


def test_mixed_expected_return_matches_a_hand_computation():
    rows = _ens_rows()
    out = build_market_ev("r", rows, _current(rows), race_date=datetime.date(2026, 10, 12))
    assert isinstance(out, MarketEvAvailable) and isinstance(out.mix, MarketMixAvailable)
    coef = market_mix.coefficients_for(2026)
    assert (out.mix.coef_year, out.mix.a, out.mix.b) == (2026, coef.a, coef.b)
    assert out.mix.version == market_mix.MIX_VERSION and out.mix.threshold == 1.0
    assert out.mix.source_model_version == ENS and out.mix.fit_through_year == 2025
    want = _hand_mix(rows, coef)
    got = [h.expected_return_mix for h in out.horses]
    assert got == pytest.approx(want, rel=1e-12)
    assert [h.exceeds_mix_threshold for h in out.horses] == [w > 1.0 for w in want]
    # the mixed probabilities sum to one over the field; the unmixed column is untouched
    assert sum(g / float(r.odds_used) for g, r in zip(got, rows, strict=True)) == pytest.approx(1.0)
    assert [h.expected_return for h in out.horses] == [float(r.expected_return) for r in rows]
    assert [h.exceeds_threshold for h in out.horses] == [False, True, False, False]


def test_mix_threshold_is_strict():
    assert market_mix.exceeds_mix_threshold(1.0) is False
    assert market_mix.exceeds_mix_threshold(1.0000001) is True


def test_mix_uses_the_coefficients_of_the_race_year():
    rows = _ens_rows()
    for r in rows:
        r.booster = "ensemble_2025.json"
    a25 = build_market_ev("r", rows, _current(rows), race_date=datetime.date(2025, 12, 28)).mix
    assert a25.coef_year == 2025 and a25.a == market_mix.coefficients_for(2025).a


def test_mix_refuses_rows_of_another_year_booster():
    """The loader falls back to an earlier year's manifest when the race year has none: such rows
    are a different p̄ than the coefficients were fitted for (review L2)."""
    rows = _ens_rows()  # ensemble_2026.json
    out = build_market_ev("r", rows, _current(rows), race_date=datetime.date(2025, 12, 28))
    assert out.mix.status == "unavailable" and out.mix.reason == "model_mismatch"
    rows[1].booster = "ensemble_2025.json"
    out = build_market_ev("r", rows, _current(rows), race_date=_DAY)
    assert out.mix.reason == "model_mismatch"
    assert all(h.expected_return_mix is None for h in out.horses)


def test_race_without_a_date_has_no_coefficients():
    rows = _ens_rows()
    out = build_market_ev("r", rows, _current(rows), race_date=None)
    assert isinstance(out, MarketEvAvailable)
    assert out.mix.reason == "no_coefficients"


@pytest.mark.parametrize(
    ("race_date", "rows_fn", "reason"),
    [
        (datetime.date(2027, 1, 5), _ens_rows, "no_coefficients"),   # year not frozen yet
        (datetime.date(2024, 12, 28), _ens_rows, "no_coefficients"),
        (_DAY, _rows, "model_mismatch"),                             # single-seed rows
    ],
)
def test_mix_unavailable_never_falls_back_to_the_unmixed_value(race_date, rows_fn, reason):
    rows = rows_fn()
    out = build_market_ev("r", rows, _current(rows), race_date=race_date)
    assert isinstance(out, MarketEvAvailable)
    assert isinstance(out.mix, MarketMixUnavailable) and out.mix.reason == reason
    assert all(h.expected_return_mix is None and h.exceeds_mix_threshold is None for h in out.horses)
    by_id = {r.horse_id: float(r.expected_return) for r in rows}
    assert [h.expected_return for h in out.horses] == [by_id[h.horse_id] for h in out.horses]


def test_mix_invalid_inputs():
    rows = _ens_rows()
    rows[0].win_prob = Decimal("0")
    out = build_market_ev("r", rows, _current(rows), race_date=_DAY)
    assert out.mix.status == "unavailable" and out.mix.reason == "invalid_inputs"
    assert out.horses[0].expected_return_mix is None


def test_mix_disabled_switch(monkeypatch):
    monkeypatch.setattr(market_mix, "MIX_DISPLAY_ENABLED", False)
    rows = _ens_rows()
    out = build_market_ev("r", rows, _current(rows), race_date=_DAY)
    assert out.mix.reason == "disabled"
    assert all(h.expected_return_mix is None for h in out.horses)


def test_race_date_is_required():
    with pytest.raises(TypeError):
        _build_market_ev("r", _rows(), _current())
