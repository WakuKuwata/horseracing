"""Load REAL exotic odds for the recommendation/backtest wiring (Feature 012).

Returns ``{(bet_type, tuple(selection)) -> odds}`` keyed by the SAME canonical selection array as
011's ``to_selection`` (and db ``canonical_selection``), so a candidate bet joins its real odds by
exact equality. Reading odds is a market lookup — never a model feature (leak boundary). The stored
value is the latest (pre-race morning odds, or final dividend after results); callers decide which
is appropriate (selection uses pre-race / estimated; scoring uses the final dividend).
"""

from __future__ import annotations

from horseracing_db.models import ExoticOdds
from sqlalchemy import select
from sqlalchemy.orm import Session


def load_real_exotic_odds(
    session: Session, race_id: str
) -> dict[tuple[str, tuple[int, ...]], float]:
    """{(bet_type, tuple(selection)) -> odds} for every real exotic_odds row of the race."""
    out: dict[tuple[str, tuple[int, ...]], float] = {}
    for bet_type, selection, odds in session.execute(
        select(ExoticOdds.bet_type, ExoticOdds.selection, ExoticOdds.odds).where(
            ExoticOdds.race_id == race_id
        )
    ):
        out[(bet_type, tuple(int(x) for x in selection))] = float(odds)
    return out
