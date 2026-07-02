"""Feature 049 T018: top-k fitting sample loader — finishers, dead heats, walk-forward."""

from __future__ import annotations

import datetime

import pytest

from horseracing_probability.model_calibration import (
    load_topk_samples,
    split_before,
    to_topk_samples,
)
from tests._synth import seed_predicted_race

pytestmark = pytest.mark.integration


def test_reconstructs_unique_finishers(session):
    seed_predicted_race(session, race_id="200806010101",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3})
    raw = load_topk_samples(session, date_from=datetime.date(2008, 6, 1),
                            date_to=datetime.date(2008, 6, 1))
    assert len(raw) == 1
    _rid, _rdate, p, placed = raw[0]
    assert set(p) == {"H1", "H2", "H3"}
    assert placed == ("H1", "H2", "H3")

    samples = to_topk_samples(raw)
    assert len(samples) == 1
    s = samples[0]
    ids = sorted(p)  # H1,H2,H3
    assert s.i1 == ids.index("H1")
    assert s.i2 == ids.index("H2")
    assert s.i3 == ids.index("H3")
    assert abs(sum(s.win) - 1.0) < 1e-9


def test_dead_heat_second_yields_none_for_that_stage(session):
    # dead heat at 2nd (two horses finish_order=2) -> i2 None, i1 present, i3 absent (no order 3)
    seed_predicted_race(session, race_id="200806010102",
                        win_probs={"H1": 0.4, "H2": 0.3, "H3": 0.3},
                        finish={"H1": 1, "H2": 2, "H3": 2})
    raw = load_topk_samples(session, date_from=datetime.date(2008, 6, 1),
                            date_to=datetime.date(2008, 6, 1))
    _rid, _rdate, _p, placed = raw[0]
    assert placed[0] == "H1"     # unique winner
    assert placed[1] is None     # dead heat at 2nd
    assert placed[2] is None     # no unique 3rd

    samples = to_topk_samples(raw)
    assert samples[0].i1 is not None
    assert samples[0].i2 is None and samples[0].i3 is None


def test_no_unique_winner_is_dropped(session):
    seed_predicted_race(session, race_id="200806010103",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 1, "H3": 3})  # dead heat at 1st
    raw = load_topk_samples(session, date_from=datetime.date(2008, 6, 1),
                            date_to=datetime.date(2008, 6, 1))
    # loader keeps the row but to_topk_samples drops it (can't index stage 1)
    assert to_topk_samples(raw) == []


def test_split_before_is_strictly_before_with_id_tiebreak(session):
    seed_predicted_race(session, race_id="200806010101",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3},
                        race_date=datetime.date(2008, 6, 1))
    seed_predicted_race(session, race_id="200806020101",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3},
                        race_date=datetime.date(2008, 6, 2))
    raw = load_topk_samples(session, date_from=datetime.date(2008, 6, 1),
                            date_to=datetime.date(2008, 6, 2))
    # strictly before the 2nd race: only the 1st remains (same-day/after excluded)
    before = split_before(raw, datetime.date(2008, 6, 2), "200806020101")
    assert [r[0] for r in before] == ["200806010101"]
    # target itself is NOT included (strict)
    assert split_before(raw, datetime.date(2008, 6, 1), "200806010101") == []


def test_calibrator_applied_before_normalization(session):
    seed_predicted_race(session, race_id="200806010101",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3})
    raw = load_topk_samples(session, date_from=datetime.date(2008, 6, 1),
                            date_to=datetime.date(2008, 6, 1))
    # a sharpening calibrator (p^2 renormalized) must change the win vector vs identity
    def sharpen(pd):
        sq = {h: v ** 2 for h, v in pd.items()}
        s = sum(sq.values())
        return {h: v / s for h, v in sq.items()}

    plain = to_topk_samples(raw)[0].win
    cal = to_topk_samples(raw, calibrator=sharpen)[0].win
    assert plain != cal
    assert abs(sum(cal) - 1.0) < 1e-9
