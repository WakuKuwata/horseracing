"""T022 (US3): period divergence of estimated vs real exotic odds, coverage explicit (SC-006)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import ExoticOdds

from horseracing_betting.exotic_divergence import exotic_divergence
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"
_FROM = datetime.date(2008, 1, 1)
_TO = datetime.date(2008, 12, 31)


def test_divergence_reports_coverage_and_logratio(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    # one real trio dividend to match against the estimated O_est
    session.add(ExoticOdds(race_id=_RACE, bet_type=BetType.TRIO, selection=[1, 2, 3],
                           odds=Decimal("40.0"), coverage_scope="partial", source="netkeiba"))
    session.commit()

    reports = exotic_divergence(session, date_from=_FROM, date_to=_TO, model_version=mv)
    assert BetType.TRIO in reports
    trio = reports[BetType.TRIO]
    assert trio.n_pairs >= 1                       # the inserted real trio matched
    assert trio.coverage_rate > 0.0
    assert trio.baseline == "estimated(010/011)" and trio.pseudo_baseline is True
    # a bet type with no real odds has explicit zero coverage (not dropped)
    if BetType.EXACTA in reports:
        assert reports[BetType.EXACTA].coverage_rate == 0.0


def test_divergence_is_deterministic(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    session.add(ExoticOdds(race_id=_RACE, bet_type=BetType.TRIO, selection=[1, 2, 3],
                           odds=Decimal("40.0"), coverage_scope="partial", source="netkeiba"))
    session.commit()
    a = exotic_divergence(session, date_from=_FROM, date_to=_TO, model_version=mv)
    b = exotic_divergence(session, date_from=_FROM, date_to=_TO, model_version=mv)
    assert {k: v.n_pairs for k, v in a.items()} == {k: v.n_pairs for k, v in b.items()}
    assert a[BetType.TRIO].log_ratio_median == b[BetType.TRIO].log_ratio_median
