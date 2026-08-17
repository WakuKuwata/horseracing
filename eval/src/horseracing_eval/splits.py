"""Expanding-window walk-forward folds over race_date (research R1).

Valid year Y: train = races with race_date.year < Y, valid = races in year Y.
2007 is initial train-only; evaluation starts at FIRST_VALID_YEAR (default 2008).

``valid_from`` narrows the SCORED side to races on or after a given day (2026-08 review). Without
it the folds are year-granular, so a caller asking for a window starting mid-year silently got the
whole year: a "prospective holdout from 2026-07-13" scored every 2026 race including the ones the
development window had already used. The TRAIN side stays year-granular on purpose — moving it to
day granularity would change every existing fold's training set and with it every recorded number.
The cost is that the models are fit on slightly less data than a day-exact split would allow, which
applies equally to both arms of a paired comparison.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from dataclasses import dataclass

from .dataset import EvalRace

FIRST_VALID_YEAR = 2008


@dataclass(frozen=True)
class Fold:
    valid_year: int
    train: tuple[EvalRace, ...]
    valid: tuple[EvalRace, ...]


def expanding_folds(
    eval_races: list[EvalRace],
    first_valid_year: int = FIRST_VALID_YEAR,
    valid_from: datetime.date | None = None,
) -> Iterator[Fold]:
    """Yield expanding-window folds in chronological order. Empty valid years are skipped.

    ``valid_from`` restricts only the scored (valid) races; the train side is unchanged, so
    ``valid_from=None`` reproduces the pre-2026-08 folds exactly.
    """
    years = sorted({er.context.race_date.year for er in eval_races})
    for year in years:
        if year < first_valid_year:
            continue
        train = tuple(er for er in eval_races if er.context.race_date.year < year)
        valid = tuple(er for er in eval_races if er.context.race_date.year == year)
        if valid_from is not None:
            valid = tuple(er for er in valid if er.context.race_date >= valid_from)
        if not train or not valid:
            continue
        yield Fold(valid_year=year, train=train, valid=valid)
