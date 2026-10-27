from decimal import Decimal

import pandas as pd
import pytest

from scripts.roi_explore.saved_timepoints_160.audit import (
    cached_mev_flags, decision_time, same_prices, source, stamp,
)


@pytest.mark.parametrize('elapsed,ok', [(0, True), (1800, True), (1800.000001, False)])
def test_freshness_boundary(elapsed, ok):
    quote = pd.Timestamp('2026-10-18T00:00:00Z')
    decision, _ = decision_time(quote, quote, quote + pd.Timedelta(seconds=elapsed), quote,
        quote + pd.Timedelta(hours=2))
    assert (decision is not None) == ok


def test_old_prediction_is_not_backdated_to_quote():
    quote = pd.Timestamp('2026-10-18T00:00:00Z')
    decision, reason = decision_time(quote, quote, quote, quote+pd.Timedelta(minutes=31),
        quote+pd.Timedelta(hours=2))
    assert decision is None
    assert reason == 'quote_older_than_30_minutes'


def test_later_matching_quote_cannot_backdate_saved_mev():
    quote = pd.Timestamp('2026-10-18T00:00:00Z')
    decision, reason = decision_time(quote, quote, quote+pd.Timedelta(hours=2), quote,
        quote+pd.Timedelta(hours=1))
    assert decision is None
    assert reason == 'not_before_post'


def test_quote_storage_availability_matters():
    quote = pd.Timestamp('2026-10-18T00:00:00Z')
    decision, _ = decision_time(quote, quote+pd.Timedelta(minutes=1), quote,
        quote, quote+pd.Timedelta(hours=1))
    assert decision == quote+pd.Timedelta(minutes=1)


@pytest.mark.parametrize('value', ['2026-10-18 00:00:00', None, pd.NaT])
def test_unknown_timezone_rejected(value):
    with pytest.raises(ValueError):
        stamp(value)


def test_exact_field_price_and_id_required():
    g = pd.DataFrame({'horse_id': ['a', 'nk:b'], 'horse_number': [1, 2],
        'odds_used': ['2.10', '20.2']})
    f = [{'horse_id': 'a', 'horse_number': 1, 'odds': Decimal('2.1')},
         {'horse_id': 'nk:b', 'horse_number': 2, 'odds': '20.2'}]
    assert same_prices(f, g)
    assert not same_prices(f[:1], g)
    assert not same_prices(f + [f[0]], g)
    f[1]['horse_id'] = 'b'
    assert not same_prices(f, g)


def test_changed_price_or_number_rejected():
    g = pd.DataFrame({'horse_id': ['a'], 'horse_number': [1], 'odds_used': ['2.1']})
    assert not same_prices([{'horse_id': 'a', 'horse_number': 1, 'odds': '2.2'}], g)
    assert not same_prices([{'horse_id': 'a', 'horse_number': 2, 'odds': '2.1'}], g)


def test_quote_bridge_cannot_remove_model_logic_requirement():
    flags = ['mev_quote_or_logic_provenance_unverified']
    legacy = ';'.join(sorted(source.MEV_TOKENS - {'oddsobs=quote'}))
    assert cached_mev_flags(pd.DataFrame({'logic_version': [legacy]}), flags) == []
    assert cached_mev_flags(pd.DataFrame({'logic_version': ['unknown']}), flags) == [
        'cached_mev_logic_unverified']
