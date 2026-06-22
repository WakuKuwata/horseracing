"""Predictor Protocol and value types (contracts/predictor.md).

Result-time market data (odds/popularity) is isolated in ``ResultMarket`` so the
market baseline can use it while future feature-based predictors are clearly warned
NOT to (leak prevention, FR-013).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ResultMarket:
    """結果確定時の odds / popularity。市場 baseline の参照線専用 (FR-013)。
    feature-based predictor はこれを参照してはならない (リーク)。"""

    odds: float | None
    popularity: int | None


@dataclass(frozen=True)
class HorseEntry:
    horse_id: str
    frame: int | None = None
    horse_number: int | None = None
    result_market: ResultMarket | None = None  # 参照線専用 (FR-013)


@dataclass(frozen=True)
class RaceContext:
    race_id: str
    race_date: datetime.date
    started_horses: tuple[HorseEntry, ...]  # entry_status='started' のみ


@dataclass(frozen=True)
class Prediction:
    win: float
    top2: float
    top3: float


@runtime_checkable
class Predictor(Protocol):
    #: 結果確定 odds/popularity を参照する baseline は True (参照線専用)。
    is_leaky_reference: bool

    def fit(self, train_races: list[RaceContext]) -> None: ...

    def predict_race(self, race: RaceContext) -> dict[str, Prediction]: ...
