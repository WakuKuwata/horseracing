"""T010 (066): boundary fit is outcome-leak-free (constitution II / FR-016).

fit_boundary reads market odds per race to compute entropy — the RESULTS (who won) must never move
the edges, and only races strictly inside the fit window contribute. Uses the testcontainer session.
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_eval.dispersion_bands import (
    DispersionBoundary,
    diagnose_bands,
    fit_boundary,
)
from tests._synth import insert_race

pytestmark = pytest.mark.integration

_FROM = datetime.date(2021, 1, 1)
_TO = datetime.date(2021, 12, 31)


def _seed_window(session, *, winner_offset: int) -> None:
    # 6 races inside the window with varied field concentration; winner varies by offset so results
    # differ between runs while odds stay fixed.
    for r in range(6):
        rid = f"20210{r + 1}010101"[:12]
        n = 6 + r  # 6..11 horses
        horses = []
        for i in range(n):
            order = (i + winner_offset) % n
            horses.append({
                "horse_id": f"R{r}H{i}", "horse_number": i + 1,
                "odds": 2.0 + 1.5 * i,  # fixed odds regardless of winner
                "finish_order": order + 1,
            })
        insert_race(session, race_id=rid, race_date=datetime.date(2021, (r % 12) + 1, 10),
                    horses=horses)


def test_boundary_invariant_to_race_results(session):
    _seed_window(session, winner_offset=0)
    b1 = fit_boundary(session, fit_from=_FROM, fit_to=_TO)
    # wipe + reseed the SAME odds but DIFFERENT winners
    from horseracing_db.models import Race, RaceHorse, RaceResult
    session.query(RaceResult).delete()
    session.query(RaceHorse).delete()
    session.query(Race).delete()
    session.commit()
    _seed_window(session, winner_offset=3)
    b2 = fit_boundary(session, fit_from=_FROM, fit_to=_TO)
    assert b1.quintile_edges == b2.quintile_edges  # results never touch the edges
    assert b1.n_races_fit == b2.n_races_fit


def test_boundary_only_uses_races_in_window(session):
    _seed_window(session, winner_offset=0)
    # a race OUTSIDE the window must not contribute
    insert_race(session, race_id="202001010101", race_date=datetime.date(2020, 1, 1),
                horses=[{"horse_id": "OUT", "horse_number": 1, "odds": 1.1, "finish_order": 1},
                        {"horse_id": "OUT2", "horse_number": 2, "odds": 20.0, "finish_order": 2}])
    b = fit_boundary(session, fit_from=_FROM, fit_to=_TO)
    assert b.n_races_fit == 6  # only the in-window races
    assert b.fit_from == "2021-01-01" and b.fit_to == "2021-12-31"


def _boundary(edges):
    return DispersionBoundary(
        metric="normalized_entropy", field_size_buckets="global",
        fit_from="2020-01-01", fit_to="2020-12-31", as_of="2020-12-31",
        version="t", quintile_edges=edges, n_races_fit=100,
    )


def test_diagnose_rejects_oos_window_not_after_fit():
    b = _boundary([0.5, 0.6, 0.7, 0.8])
    with pytest.raises(ValueError):
        diagnose_bands(session=None, boundary=b,  # window overlaps fit -> guard trips before DB use
                       diagnose_from=datetime.date(2020, 6, 1),
                       diagnose_to=datetime.date(2021, 1, 1))


def test_diagnose_counts_void_and_favorite_loss(session):
    # one OOS race where the FAVOURITE (lowest odds) LOSES, and one VOID race (no result).
    insert_race(session, race_id="202201010101", race_date=datetime.date(2022, 3, 1), horses=[
        {"horse_id": "F", "horse_number": 1, "odds": 1.5, "finish_order": 3},   # favourite loses
        {"horse_id": "M", "horse_number": 2, "odds": 4.0, "finish_order": 2},
        {"horse_id": "W", "horse_number": 3, "odds": 12.0, "finish_order": 1},  # winner @ 12.0 >=10
    ])
    # void race: entered with odds but no finished result row
    insert_race(session, race_id="202201010102", race_date=datetime.date(2022, 3, 1), horses=[
        {"horse_id": "A", "horse_number": 1, "odds": 2.0},
        {"horse_id": "B", "horse_number": 2, "odds": 3.0},
    ])
    b = _boundary([0.1, 0.2, 0.3, 0.4])  # low edges -> most races land in "open"
    rows = diagnose_bands(session, boundary=b,
                          diagnose_from=datetime.date(2022, 1, 1),
                          diagnose_to=datetime.date(2022, 12, 31))
    totals = {r.band: r for r in rows}
    scored = [r for r in rows if r.n > 0]
    assert len(scored) == 1 and scored[0].favorite_loss_rate == 1.0  # favourite lost
    assert scored[0].high_payout_rate == 1.0  # winner paid 12.0 >= 10.0
    assert sum(r.n_void for r in rows) == 1  # the result-less race counted as void, not scored
    assert all(0.0 <= (r.ci_low or 0.0) <= (r.ci_high or 1.0) <= 1.0 for r in totals.values())
