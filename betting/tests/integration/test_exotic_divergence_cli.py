"""T025 (US4): exotic-divergence CLI reports coverage + log-ratio with baseline label."""

from __future__ import annotations

from decimal import Decimal

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import ExoticOdds

from horseracing_betting.cli import main
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"


def test_exotic_divergence_cli(session, tmp_path, capsys, database_url):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    make_active_model(session, tmp_path)
    session.add(ExoticOdds(race_id=_RACE, bet_type=BetType.TRIO, selection=[1, 2, 3],
                           odds=Decimal("40.0"), coverage_scope="partial", source="netkeiba"))
    session.commit()

    rc = main(["exotic-divergence", "--from", "2008-01-01", "--to", "2008-12-31",
               "--database-url", database_url])
    assert rc == 0
    out = capsys.readouterr().out
    assert "exotic-divergence" in out
    assert "baseline=推定" in out and "coverage" in out
    assert "trio" in out
