"""Expanding-window walk-forward folds over race_date (research R1).

Valid year Y: train = races with race_date.year < Y, valid = races in year Y.
2007 is initial train-only; evaluation starts at FIRST_VALID_YEAR (default 2008).
"""

from __future__ import annotations

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
    eval_races: list[EvalRace], first_valid_year: int = FIRST_VALID_YEAR
) -> Iterator[Fold]:
    """Yield expanding-window folds in chronological order. Empty valid years are skipped."""
    years = sorted({er.context.race_date.year for er in eval_races})
    for year in years:
        if year < first_valid_year:
            continue
        train = tuple(er for er in eval_races if er.context.race_date.year < year)
        valid = tuple(er for er in eval_races if er.context.race_date.year == year)
        if not train or not valid:
            continue
        yield Fold(valid_year=year, train=train, valid=valid)
