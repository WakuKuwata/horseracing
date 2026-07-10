"""Feature 065: shadow_log_summary pure aggregation — prospective+settled+real-win ONLY, frozen
odds, voids/pending excluded from the ROI denominator, empty is typed-empty. No DB."""

from __future__ import annotations

from horseracing_db.enums import ResultStatus

from horseracing_api.backtest import ShadowLogSummary, is_prospective, shadow_log_summary

_FIN = ResultStatus.FINISHED


def _row(**kw):
    base = dict(bet_type="win", logic_version="ev=..;prospective=1;odds_asof=2026-08-01T09:00:00",
               selection={"horse_id": "H1", "horse_number": 1}, market_odds_used=4.0,
               is_estimated_odds=False, estimated_market_odds_used=None,
               computed_at="2026-08-01T09:00:00", finish_map={}, n_winners=0)
    base.update(kw)
    return base


def _hit_fm():
    return {"H1": (1, _FIN), "H2": (2, _FIN)}


def _miss_fm():
    return {"H1": (3, _FIN), "H2": (1, _FIN)}


def test_is_prospective_exact_token():
    assert is_prospective("a;prospective=1;b") is True
    assert is_prospective("a;oddscap=21;b") is False
    assert is_prospective("prospective=10") is False        # not the exact token
    assert is_prospective(None) is False


def test_only_prospective_settled_real_win_counted():
    rows = [
        _row(finish_map=_hit_fm(), n_winners=1),                       # prospective hit ✓
        _row(finish_map=_miss_fm(), n_winners=1),                      # prospective miss ✓
        _row(logic_version="ev=..;oddscap=21", finish_map=_hit_fm()),  # backfill (no marker) ✗
        _row(bet_type="exacta", finish_map=_hit_fm()),                 # exotic ✗
        _row(is_estimated_odds=True, estimated_market_odds_used=9.0, market_odds_used=None,
             finish_map=_hit_fm()),                                    # estimated ✗
    ]
    s = shadow_log_summary(rows)
    assert s.n_prospective == 2 and s.n_settled == 2 and s.n_hit == 1
    assert abs(s.hit_rate - 0.5) < 1e-9
    assert abs(s.recovery_rate - (4.0 + 0.0) / 2) < 1e-9              # frozen odds 4.0 on hit


def test_uses_frozen_market_odds_used_not_any_current():
    # the function only ever reads the row's market_odds_used (frozen at record time)
    s = shadow_log_summary([_row(market_odds_used=7.5, finish_map=_hit_fm(), n_winners=1)])
    assert abs(s.recovery_rate - 7.5) < 1e-9


def test_voids_and_pending_excluded_from_denominator():
    rows = [
        _row(finish_map=_hit_fm(), n_winners=1),                                   # valued hit
        _row(selection={"horse_id": "GONE", "horse_number": 9}, finish_map=_hit_fm(),
             n_winners=1),                                                          # void (no row)
        _row(finish_map={}),                                                        # pending
    ]
    s = shadow_log_summary(rows)
    assert s.n_prospective == 3 and s.n_settled == 1 and s.n_void == 1 and s.n_pending == 1
    assert s.recovery_rate == 4.0 and s.n_hit == 1                                  # denom = 1


def test_empty_is_typed_empty():
    s = shadow_log_summary([])
    assert s == ShadowLogSummary()
    assert s.recovery_rate is None and s.hit_rate is None and s.n_prospective == 0
