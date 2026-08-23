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


def assert_scored_window(valid_races, *, valid_from: datetime.date | None) -> None:
    """Fail closed if the SCORED races start before the frozen window.

    ``valid_from`` narrows the scored side, but only if every path actually threads it through.
    ``paired_eval`` did; ``regime_paired.evaluate_regimes`` did not, so a prospective holdout
    frozen at 2026-07-13 scored the whole of 2026 — including the races the development window
    had already used — while ``assert_confirmatory`` verified the CLI's --from/--to and reported
    the window as checked. A guard that validates the DECLARED window and not the SCORED one is
    worse than none, because it reads as assurance.

    This checks the races that were actually scored, so it holds no matter which path ran.
    """
    if valid_from is None or not valid_races:
        return
    first = min(er.context.race_date for er in valid_races)
    if first < valid_from:
        raise ValueError(
            f"scored window starts {first} but the pre-registered window starts {valid_from}: "
            f"{sum(1 for er in valid_races if er.context.race_date < valid_from)} of "
            f"{len(valid_races)} scored races predate it (valid_from was not threaded through)"
        )
