"""US3 (FR-011): `show` prints consistency-passing top-K combination probabilities."""

from __future__ import annotations

import pytest

from horseracing_probability.cli import main
from tests._synth import seed_predicted_race

pytestmark = pytest.mark.integration


def test_show_outputs_consistent_probabilities(session, capsys):
    seed_predicted_race(session, race_id="200806010101",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3})
    rc = main(["show", "--race-id", "200806010101", "--top", "3"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "consistency OK" in out
    assert "馬単/exacta" in out and "三連単/trifecta" in out
