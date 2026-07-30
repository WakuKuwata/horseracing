"""Load REAL exotic odds for the recommendation/backtest wiring (Feature 012).

Returns ``{(bet_type, tuple(selection)) -> odds}`` keyed by the SAME canonical selection array as
011's ``to_selection`` (and db ``canonical_selection``), so a candidate bet joins its real odds by
exact equality. Reading odds is a market lookup — never a model feature (leak boundary). The stored
value is the latest (pre-race morning odds, or final dividend after results); callers decide which
is appropriate (selection uses pre-race / estimated; scoring uses the final dividend).
"""

from __future__ import annotations

from horseracing_db.models import ExoticOdds, RaceResult
from sqlalchemy import exists, select
from sqlalchemy.orm import Session


def load_selectable_exotic_odds(
    session: Session, race_id: str
) -> dict[tuple[str, tuple[int, ...]], float]:
    """Real exotic odds usable for BET SELECTION — empty once the race has a result.

    `exotic_odds` is single-latest per (race_id, bet_type, selection) and, post-race, holds the
    FINAL DIVIDEND of the winning combination only (netkeiba's payout table lists winners, never
    the full grid). Feeding that back into EV selection would pick bets by reading the outcome:
    the only priced selection is the one that won. That is a results leak, and it stays silent
    because the rows look like ordinary "real odds".

    So selection paths use this loader, which refuses to serve a settled race, while SCORING paths
    (backtest / divergence / gate) keep calling `load_real_exotic_odds` — reading the dividend is
    exactly what settlement is for.

    Pre-race quotes for exotic pools are not collected yet (that needs the odds API's type=2..8),
    so today this returns {} for settled races and {} for pending ones alike; it exists to make the
    boundary explicit and enforced rather than incidental.
    """
    if session.scalar(select(exists().where(RaceResult.race_id == race_id))):
        return {}
    return load_real_exotic_odds(session, race_id)


def load_real_exotic_odds(
    session: Session, race_id: str
) -> dict[tuple[str, tuple[int, ...]], float]:
    """{(bet_type, tuple(selection)) -> odds} for every real exotic_odds row of the race.

    SCORING/SETTLEMENT use only. For bet selection call `load_selectable_exotic_odds`, which
    refuses settled races — post-race these rows are final dividends of the WINNING combination.
    """
    out: dict[tuple[str, tuple[int, ...]], float] = {}
    for bet_type, selection, odds in session.execute(
        select(ExoticOdds.bet_type, ExoticOdds.selection, ExoticOdds.odds).where(
            ExoticOdds.race_id == race_id
        )
    ):
        out[(bet_type, tuple(int(x) for x in selection))] = float(odds)
    return out
