"""Feature 080 US3: real-dividend exotic gate driver and CLI."""

from __future__ import annotations

import datetime

import pytest

from horseracing_betting.cli import main
from horseracing_betting.exotic_gate import (
    PREREGISTERED_N_MIN,
    run_exotic_gate,
)
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_FROM = datetime.date(2008, 1, 1)
_TO = datetime.date(2008, 12, 31)


def _seed_model_without_exotic_odds(session, tmp_path) -> str:
    seed_learnable(
        session,
        years=(2007, 2008),
        races_per_year=10,
        field_size=8,
    )
    return make_active_model(session, tmp_path, model_version="exotic-gate-test")


def test_empty_real_dividends_are_all_no_decision(session, tmp_path):
    model_version = _seed_model_without_exotic_odds(session, tmp_path)

    verdicts = run_exotic_gate(
        session,
        date_from=_FROM,
        date_to=_TO,
        model_version=model_version,
        b=20,
    )

    assert set(verdicts) == set(PREREGISTERED_N_MIN)
    assert all(verdict.verdict == "NO_DECISION" for verdict in verdicts.values())
    assert all(verdict.n_bets == 0 for verdict in verdicts.values())


def test_exotic_gate_cli_prints_logic_version_header(
    session, tmp_path, capsys, database_url
):
    _seed_model_without_exotic_odds(session, tmp_path)

    rc = main(
        [
            "exotic-gate",
            "--from",
            _FROM.isoformat(),
            "--to",
            _TO.isoformat(),
            "--b",
            "20",
            "--database-url",
            database_url,
        ]
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert f"exotic-gate {_FROM}..{_TO} [lv:" in out
    assert f"window={_FROM}..{_TO}" in out
    assert "baseline=lowest_oest" in out
    assert "n_min=place:500,quinella:500,wide:500,exacta:700" in out
    assert "takeout=place:0.20,quinella:0.225,wide:0.225" in out
    assert "series=prospective-primary" in out
    for bet_type in PREREGISTERED_N_MIN:
        assert f"\n{bet_type} NO_DECISION " in out
