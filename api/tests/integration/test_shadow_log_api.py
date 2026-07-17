"""Feature 065: /shadow-log endpoint — typed-empty when the instrument is still filling, and it
picks up prospective win recs ACROSS runs (not active-run scoped)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import Recommendation

from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "200806010111"


def test_shadow_log_empty_is_typed_empty(client, session):
    body = client.get("/api/v1/shadow-log").json()
    assert body["n_prospective"] == 0 and body["n_settled"] == 0
    assert body["counterfactual_snapshot_recovery_rate"] is None and body["by_month"] == []


def test_shadow_log_picks_up_prospective_across_runs(client, session):
    seed_model(session)
    run_id = seed_race(session, race_id=_RACE, horses={1: {"win": 0.4, "odds": 2.0}})
    # a prospective win rec (marker) — no results yet ⇒ pending
    session.add(Recommendation(
        prediction_run_id=run_id, race_id=_RACE, bet_type=BetType.WIN,
        selection={"horse_id": "H1", "horse_number": 1},
        market_odds_used=Decimal("2.0"), estimated_market_odds_used=None, is_estimated_odds=False,
        pseudo_odds=Decimal("2.5"), pseudo_roi=Decimal("0.1"), stake_fraction=None,
        logic_version="ev=..;prospective=1;odds_asof=2026-08-01T09:00:00",
    ))
    session.commit()
    body = client.get("/api/v1/shadow-log").json()
    assert body["n_prospective"] == 1 and body["n_pending"] == 1     # counted, unsettled
    assert body["n_settled"] == 0


def test_shadow_log_by_month_recovery_is_populated_not_null(client, session):
    # Feature 075 (analyze I1): a settled prospective win must produce a by_month row whose
    # counterfactual_snapshot_recovery is the FROZEN-odds recovery, NOT null. Guards the splat trap
    # where renaming the response field but keeping the internal dict key "recovery" silently nulls
    # it (grep can't see it; only a nested-value assertion catches it).
    seed_model(session)
    run_id = seed_race(
        session, race_id=_RACE, race_date=None or __import__("datetime").date(2026, 8, 15),
        horses={1: {"win": 0.4, "odds": 2.0, "finish": 1}},   # hit at frozen odds 2.0
    )
    session.add(Recommendation(
        prediction_run_id=run_id, race_id=_RACE, bet_type=BetType.WIN,
        selection={"horse_id": "H1", "horse_number": 1},
        market_odds_used=Decimal("2.0"), estimated_market_odds_used=None, is_estimated_odds=False,
        pseudo_odds=Decimal("2.5"), pseudo_roi=Decimal("0.1"), stake_fraction=None,
        logic_version="ev=..;prospective=1;odds_asof=2026-08-14T09:00:00",
    ))
    session.commit()
    body = client.get("/api/v1/shadow-log").json()
    assert body["n_settled"] == 1 and body["n_hit"] == 1
    assert body["valuation_basis"] == "frozen_snapshot_odds"
    # top-level frozen-odds recovery = gross 2.0 over 1 settled
    assert body["counterfactual_snapshot_recovery_rate"] == pytest.approx(2.0)
    assert len(body["by_month"]) == 1
    m = body["by_month"][0]   # month bucket derives from computed_at (server default), not race_date
    assert m["n_settled"] == 1
    # THE I1 GUARD: nested value present and equal to the top-level (single month) — never null.
    assert m["counterfactual_snapshot_recovery"] == pytest.approx(2.0)
