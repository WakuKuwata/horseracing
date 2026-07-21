"""Feature 078 US1 (T002): load_topk_samples_from_oof — OOF-faithful top-k stage samples.

p comes from the OOF BUNDLE (not the persisted run); placings from DB results (labels only). The
dead-heat matrix and leak boundary mirror the runtime load_topk_samples.
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_probability.model_calibration import load_topk_samples_from_oof
from tests._synth import seed_predicted_race

pytestmark = pytest.mark.integration

_D = datetime.date(2008, 6, 1)


def _bundle(*race_ids):
    """A fixture OOF bundle whose win probs DIFFER from anything persisted (proves the source)."""
    preds = {}
    for rid in race_ids:
        preds[rid] = {
            "H1": {"win": 0.70, "top2": 0.80, "top3": 0.90},
            "H2": {"win": 0.20, "top2": 0.50, "top3": 0.70},
            "H3": {"win": 0.10, "top2": 0.30, "top3": 0.50},
        }
    return {"predictions": preds}


def test_uses_bundle_predictions_not_persisted(session):
    # persisted win probs are 0.5/0.3/0.2 — the OOF sample must use the BUNDLE's 0.7/0.2/0.1
    seed_predicted_race(session, race_id="200806010101",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3})
    out = load_topk_samples_from_oof(session, _bundle("200806010101"))
    assert len(out) == 1
    _rid, rdate, p, placed = out[0]
    assert rdate == _D
    assert p == {"H1": 0.70, "H2": 0.20, "H3": 0.10}  # bundle p, NOT persisted
    assert placed == ("H1", "H2", "H3")


def test_dead_heat_first_yields_none_for_all_placings(session):
    seed_predicted_race(session, race_id="200806010102",
                        win_probs={"H1": 0.4, "H2": 0.4, "H3": 0.2},
                        finish={"H1": 1, "H2": 1, "H3": 3})  # 1st dead heat
    _rid, _d, _p, placed = load_topk_samples_from_oof(session, _bundle("200806010102"))[0]
    assert placed[0] is None  # 1st non-unique → λ2/λ3 both drop this race downstream


def test_dead_heat_second_yields_none_at_position_two(session):
    seed_predicted_race(session, race_id="200806010103",
                        win_probs={"H1": 0.4, "H2": 0.3, "H3": 0.3},
                        finish={"H1": 1, "H2": 2, "H3": 2})  # 2nd dead heat
    _rid, _d, _p, placed = load_topk_samples_from_oof(session, _bundle("200806010103"))[0]
    assert placed[0] == "H1" and placed[1] is None  # 2nd non-unique → λ3 also drops (D4)


def test_deterministic_sort_and_started_only(session):
    for rid in ("200806010105", "200806010104"):
        seed_predicted_race(session, race_id=rid,
                            win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                            finish={"H1": 1, "H2": 2, "H3": 3})
    out = load_topk_samples_from_oof(session, _bundle("200806010105", "200806010104"))
    assert [s[0] for s in out] == ["200806010104", "200806010105"]  # (race_date, race_id) sort


def test_result_change_leaves_other_race_samples_invariant(session):
    seed_predicted_race(session, race_id="200806010106",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3})
    seed_predicted_race(session, race_id="200806010107",
                        win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                        finish={"H1": 1, "H2": 2, "H3": 3})
    bundle = _bundle("200806010106", "200806010107")
    before = load_topk_samples_from_oof(session, bundle)
    # mutate race ...107's result — race ...106's sample (p + placings) must be unchanged (leak bound)
    from horseracing_db.models import RaceResult
    from sqlalchemy import select
    rr = session.scalar(
        select(RaceResult).where(RaceResult.race_id == "200806010107")
        .where(RaceResult.horse_id == "H1"))
    rr.finish_order = 3
    session.flush()
    after = load_topk_samples_from_oof(session, bundle)
    b106 = next(s for s in before if s[0] == "200806010106")
    a106 = next(s for s in after if s[0] == "200806010106")
    assert b106 == a106  # unrelated race untouched by another race's result change
