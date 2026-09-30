"""Feature 137: the market-ev response schemas are strict (075 splat-null countermeasure).

Unknown or misnamed keys raise instead of being dropped, and core numeric values have no silent
default, so a renamed producer key can never turn into a quiet null / 0 on screen.
"""

from __future__ import annotations

import datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from horseracing_api.schemas import (
    HorseMarketEv,
    MarketEvAvailable,
    MarketEvResponse,
    MarketEvUnavailable,
)

_ADAPTER = TypeAdapter(MarketEvResponse)
_TS = datetime.datetime(2026, 9, 27, 3, 0, tzinfo=datetime.UTC)


def _horse(**over) -> dict:
    base = {
        "horse_id": "H1", "horse_number": 1, "expected_return": 1.25, "odds_used": 5.0,
        "exceeds_threshold": True,
    }
    base.update(over)
    return base


def _available(**over) -> dict:
    base = {
        "status": "available", "race_id": "202609270511", "model_version": "mev-binary-v2",
        "logic_version": "mev-v1", "computed_at": _TS, "odds_observed_at": _TS,
        "odds_changed_after_compute": False, "result_pending_at_compute": True,
        "threshold": 1.2, "is_pseudo": True, "horses": [_horse()],
    }
    base.update(over)
    return base


def test_available_and_unavailable_round_trip_through_the_union():
    available = _ADAPTER.validate_python(_available())
    assert isinstance(available, MarketEvAvailable)
    assert available.horses[0].expected_return == 1.25
    unavailable = _ADAPTER.validate_python(
        {"status": "unavailable", "race_id": "202609270511", "reason": "not_computed",
         "threshold": 1.2}
    )
    assert isinstance(unavailable, MarketEvUnavailable)


def test_unknown_key_on_the_response_raises():
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(_available(win_prob=0.3))


@pytest.mark.parametrize("misnamed", ["expected_roi", "ev", "expectedReturn"])
def test_misnamed_expected_return_key_raises(misnamed):
    horse = _horse()
    horse[misnamed] = horse.pop("expected_return")
    with pytest.raises(ValidationError):
        HorseMarketEv.model_validate(horse)
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(_available(horses=[horse]))


def test_missing_expected_return_raises():
    horse = _horse()
    del horse["expected_return"]
    with pytest.raises(ValidationError) as excinfo:
        HorseMarketEv.model_validate(horse)
    assert any(err["loc"] == ("expected_return",) for err in excinfo.value.errors())


@pytest.mark.parametrize(
    "field", ["odds_used", "exceeds_threshold", "horse_number", "horse_id"]
)
def test_every_horse_field_is_required(field):
    horse = _horse()
    del horse[field]
    with pytest.raises(ValidationError):
        HorseMarketEv.model_validate(horse)


@pytest.mark.parametrize(
    "field",
    ["threshold", "is_pseudo", "computed_at", "odds_observed_at", "odds_changed_after_compute",
     "result_pending_at_compute", "model_version", "logic_version", "horses"],
)
def test_available_core_fields_have_no_default(field):
    body = _available()
    del body[field]
    with pytest.raises(ValidationError):
        MarketEvAvailable.model_validate(body)


def test_unavailable_rejects_unknown_reason_and_extra_keys():
    with pytest.raises(ValidationError):
        MarketEvUnavailable.model_validate(
            {"status": "unavailable", "race_id": "r", "reason": "no_odds", "threshold": 1.2}
        )
    with pytest.raises(ValidationError):
        MarketEvUnavailable.model_validate(
            {"status": "unavailable", "race_id": "r", "reason": "not_computed", "threshold": 1.2,
             "horses": []}
        )


def test_is_pseudo_cannot_be_false():
    with pytest.raises(ValidationError):
        MarketEvAvailable.model_validate(_available(is_pseudo=False))


def test_openapi_never_exposes_win_prob():
    from horseracing_api.app import app

    schemas = app.openapi()["components"]["schemas"]
    for name in ("HorseMarketEv", "MarketEvAvailable", "MarketEvUnavailable"):
        assert "win_prob" not in schemas[name]["properties"]
        assert schemas[name]["additionalProperties"] is False
