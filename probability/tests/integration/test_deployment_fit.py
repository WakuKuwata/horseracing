"""Feature 078 US2 (T006): deployment final-fit — shipped params, gated by verdict (D2/D5/D6).

The prequential fit gives the verdict; the SHIPPED params come from a separate all-OOF fit. A non-
ADOPT stage ships explicit identity; an ADOPT stage ships fitted params + provenance (fit_through =
max contributing race_date, deterministic race-set hash). Fit is on RAW OOF win (D1).
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_probability.oof_calibration import (
    stage_deployment_fit,
    two_gamma_deployment_fit,
)
from tests._synth import seed_predicted_race

pytestmark = pytest.mark.integration


def _seed_and_bundle(session):
    """Seed a few races across dates + a matching OOF bundle (win differs from persisted)."""
    races = {
        "200806010101": datetime.date(2008, 6, 1),
        "200806020101": datetime.date(2008, 6, 2),
        "200806030101": datetime.date(2008, 6, 3),
    }
    preds = {}
    for rid, rdate in races.items():
        seed_predicted_race(session, race_id=rid, race_date=rdate,
                            win_probs={"H1": 0.5, "H2": 0.3, "H3": 0.2},
                            finish={"H1": 1, "H2": 2, "H3": 3})
        preds[rid] = {
            "H1": {"win": 0.60, "top2": 0.80, "top3": 0.92},
            "H2": {"win": 0.25, "top2": 0.55, "top3": 0.75},
            "H3": {"win": 0.15, "top2": 0.35, "top3": 0.55},
        }
    return {"predictions": preds, "bundle_digest": "beef"}, races


def test_reject_ships_explicit_identity(session):
    bundle, _ = _seed_and_bundle(session)
    sd = stage_deployment_fit(session, bundle, adopt=False)
    assert (sd["lambda2"], sd["lambda3"]) == (1.0, 1.0)
    assert sd["fit_through"] is None and sd["n_fit"] == 0 and sd["identity"] is True
    tg = two_gamma_deployment_fit(session, bundle, adopt=False)
    assert (tg["gamma_lo"], tg["gamma_hi"]) == (1.0, 1.0)
    assert tg["fit_through"] is None and tg["n_fit"] == 0 and tg["identity"] is True


def test_adopt_stage_records_provenance(session):
    bundle, races = _seed_and_bundle(session)
    sd = stage_deployment_fit(session, bundle, adopt=True, min_races=2)
    # all 3 races have a clean 1-2-3 → they all contribute; fit_through = the latest date (D5)
    assert sd["n_fit"] == 3
    assert sd["fit_through"] == max(races.values()).isoformat()
    assert sd["fit_race_set_hash"] is not None
    # λ is either a fitted interior value or an explicit fallback identity — both are well-formed
    assert 0.0 < sd["lambda2"] and 0.0 < sd["lambda3"]


def test_adopt_two_gamma_records_provenance(session):
    bundle, races = _seed_and_bundle(session)
    tg = two_gamma_deployment_fit(session, bundle, adopt=True)
    assert tg["n_fit"] == 3
    assert tg["fit_through"] == max(races.values()).isoformat()
    assert tg["fit_race_set_hash"] is not None  # 3 races < min_wins → identity, but provenance stands


def test_deterministic(session):
    bundle, _ = _seed_and_bundle(session)
    a = stage_deployment_fit(session, bundle, adopt=True, min_races=2)
    b = stage_deployment_fit(session, bundle, adopt=True, min_races=2)
    assert a == b
    assert two_gamma_deployment_fit(session, bundle, adopt=True) == \
        two_gamma_deployment_fit(session, bundle, adopt=True)
