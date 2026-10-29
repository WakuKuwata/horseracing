"""175 (plan D3): B8's classify_race and the decision-quote classify agree on the stages they
share (jump / field / values / result / payout), fed the same synthetic facts."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest
from horseracing_eval import decision_quote_scoring as dqs

from horseracing_api import market_ev_prospective as b8
from horseracing_api.market_ev import MARKET_EV_THRESHOLD

UTC = datetime.UTC
DAY = datetime.date(2026, 10, 31)
POST = datetime.datetime(2026, 10, 31, 6, 0, tzinfo=UTC)
SHARED_STAGES = {"jump_race", "field_mismatch", "invalid_values", "pending_result",
                 "payout_race_missing", "payout_inconsistent", "eligible"}


def facts(*, track_type="芝", started=None, odds=(2.0, 5.0, 20.0), p=(0.6, 0.3, 0.1),
          has_results=True, winners=("H01",), payouts=None):
    started = started if started is not None else {"H01": 1, "H02": 2, "H03": 3}
    payouts = payouts if payouts is not None else {1: 200}
    return dict(track_type=track_type, started=started, odds=odds, p=p, has_results=has_results,
                winners=frozenset(winners), payouts=payouts)


def b8_race(f):
    t0 = POST - datetime.timedelta(hours=1)
    rows = [SimpleNamespace(
        race_id="R", horse_id=f"H{i+1:02d}", model_version="mev-ens15-v1", horse_number=i + 1,
        win_prob=pp, expected_return=pp * o, odds_used=o, booster="ensemble_2026.json",
        booster_sha256="e" * 64, odds_observed_at=t0, result_pending_at_compute=True,
        logic_version=f"x;{b8.QUOTE_TOKEN}", computed_at=t0, run_id="run",
    ) for i, (pp, o) in enumerate(zip(f["p"], f["odds"], strict=True))]
    return b8.ProspectiveRace(
        race_id="R", race_date=DAY, post_time=POST, track_type=f["track_type"], rows=rows,
        started=f["started"], has_results=f["has_results"], winner_horse_ids=f["winners"],
        payouts=f["payouts"],
    )


def quote_record(f):
    horses = [dqs.QuoteHorse(f"H{i+1:02d}", i + 1, o, i + 1, pp, pp * o, pp, pp * o, None, None)
              for i, (pp, o) in enumerate(zip(f["p"], f["odds"], strict=True))]
    stored = {h.horse_id: h.horse_number for h in horses}
    captured = POST - datetime.timedelta(seconds=180)
    return dqs.QuoteRecord(
        race_id="R", race_date=DAY, post_time=POST, track_type=f["track_type"], captured_at=captured,
        official_at=captured, seconds_to_post=180, capture_target="t_minus_3", horses=horses,
        mix_status="no_coefficients", field_digest=dqs.digest_of_started(stored),
        started=f["started"], has_results=f["has_results"], winner_horse_ids=f["winners"],
        payouts=f["payouts"],
    )


@pytest.mark.parametrize("f", [
    facts(),
    facts(track_type="障"),
    facts(started={"H01": 1, "H02": 2}),                 # scratched after capture / compute
    facts(started={"H01": 1, "H02": 2, "H03": None}),    # 馬番 unknown
    facts(odds=(0.5, 5.0, 20.0)),                        # odds below 1
    facts(p=(1.0, 0.3, 0.1)),                            # p not in (0,1)
    facts(has_results=False),
    facts(payouts={}),
    facts(payouts={2: 900}),                             # paid 馬番 ≠ winner
    facts(winners=("H01", "H02"), payouts={1: 150, 2: 300}),
])
def test_shared_stages_agree(f):
    a = b8.classify_race(b8_race(f), since=DAY)
    b = dqs.classify(quote_record(f))
    assert a in SHARED_STAGES and b in SHARED_STAGES
    assert a == b


def test_thresholds_are_the_same_constant():
    assert dqs.EXPECTED_RETURN_THRESHOLD == MARKET_EV_THRESHOLD == 1.2
    assert b8.MIN_BETS == dqs.MIN_POINTS == 300 and b8.BOOTSTRAP == dqs.BOOTSTRAP
