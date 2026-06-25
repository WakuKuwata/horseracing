"""T022 (US3): exotic-recommend / exotic-backtest CLI emit results + DOUBLE-pseudo labels."""

from __future__ import annotations

import pytest

from horseracing_betting.cli import main
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"


def test_exotic_recommend_cli(session, tmp_path, capsys, database_url):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    make_prediction_run(session, race_id=_RACE, model_version=mv)

    rc = main(["exotic-recommend", "--race-id", _RACE, "--threshold", "0.0", "--top-k", "2",
               "--database-url", database_url])
    assert rc == 0
    out = capsys.readouterr().out
    assert "exotic recommendations=" in out
    # no exotic_odds in DB -> all estimated (double-pseudo) fallback
    assert "二重疑似" in out
    assert "0 real-odds" in out


def test_exotic_backtest_cli(session, tmp_path, capsys, database_url):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    make_active_model(session, tmp_path)

    rc = main(["exotic-backtest", "--from", "2008-01-01", "--to", "2008-12-31",
               "--threshold", "0.0", "--top-k", "2", "--database-url", database_url])
    assert rc == 0
    out = capsys.readouterr().out
    assert "exotic-backtest" in out
    assert "二重疑似" in out
    for strat in ("ev", "lowest_oest", "uniform"):
        assert strat in out
