"""US3 (FR-011): estimate-odds / validate-odds CLI print estimated odds + pseudo markers."""

from __future__ import annotations

import pytest

from horseracing_probability.cli import main
from tests._synth import seed_odds_race

pytestmark = pytest.mark.integration


def test_estimate_odds_cli_marks_estimated(session, capsys):
    seed_odds_race(session, race_id="200806010101",
                   win_odds={"H1": 1.6, "H2": 3.2, "H3": 3.2}, finish={"H1": 1, "H2": 2, "H3": 3})
    rc = main(["estimate-odds", "--race-id", "200806010101", "--top", "3"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "推定" in out and "estimated" in out      # explicitly flagged as estimated
    assert "馬単/exacta" in out


def test_validate_odds_cli_marks_pseudo(session, capsys):
    seed_odds_race(session, race_id="200806010101",
                   win_odds={"H1": 1.6, "H2": 3.2, "H3": 3.2}, finish={"H1": 1, "H2": 2, "H3": 3})
    rc = main(["validate-odds", "--from", "2008-06-01", "--to", "2008-06-01"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PSEUDO" in out and "疑似" in out          # pseudo evaluation marker
    assert "recovery" in out and "calibration" in out
