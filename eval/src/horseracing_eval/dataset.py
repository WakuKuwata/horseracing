"""Build evaluation data from the DB (data-model.md).

Population = started horses (entry_status excludes cancelled/excluded). Scoring
labels come from labels.derive_labels (finished only, dead-heat aware). Races with
no finished horses are dropped.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Race, RaceHorse, RaceResult
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from .predictor import HorseEntry, RaceContext, ResultMarket


@dataclass(frozen=True)
class ScoringLabel:
    horse_id: str
    win: int
    top2: int
    top3: int


@dataclass(frozen=True)
class EvalRace:
    context: RaceContext
    labels: tuple[ScoringLabel, ...]  # finished horses only


@dataclass(frozen=True)
class RacePopulation:
    """Feature 068 (FR-002/FR-001, codex population golden): started-all labels + winner
    eligibility for one race, derived purely from an already-loaded ``EvalRace`` (no DB).

    ``started_*`` map every STARTED horse to a 0/1 label; a started horse absent from the
    finished labels (DNF / 失格 / unplaced) gets 0 — the win教師 population that training uses.
    ``winner_horse_id`` is the single winner or ``None``; ``eligible`` is True only when the race
    has EXACTLY one winner (dead-heat n_winners>1, no-winner n_winners==0, and partial-ingest
    races are ineligible for winner NLL and surfaced, spec Edge Cases).
    """

    race_id: str
    started_horse_ids: tuple[str, ...]
    field_size: int
    started_win: dict[str, int]
    started_top2: dict[str, int]
    started_top3: dict[str, int]
    winner_horse_id: str | None
    n_winners: int
    eligible: bool


def population_masks(er: EvalRace) -> RacePopulation:
    """Classify one race's started population and winner eligibility (T004, pure)."""
    label_by_id = {sl.horse_id: sl for sl in er.labels}
    started_ids = tuple(h.horse_id for h in er.context.started_horses)
    started_win: dict[str, int] = {}
    started_top2: dict[str, int] = {}
    started_top3: dict[str, int] = {}
    for hid in started_ids:
        sl = label_by_id.get(hid)
        started_win[hid] = int(sl.win) if sl is not None else 0
        started_top2[hid] = int(sl.top2) if sl is not None else 0
        started_top3[hid] = int(sl.top3) if sl is not None else 0
    winners = [hid for hid, w in started_win.items() if w == 1]
    n_winners = len(winners)
    eligible = n_winners == 1
    return RacePopulation(
        race_id=er.context.race_id,
        started_horse_ids=started_ids,
        field_size=len(started_ids),
        started_win=started_win,
        started_top2=started_top2,
        started_top3=started_top3,
        winner_horse_id=winners[0] if eligible else None,
        n_winners=n_winners,
        eligible=eligible,
    )


def load_eval_races(
    session: Session,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> list[EvalRace]:
    """Load races (sorted by race_date, race_id) with started population + finished labels.

    Bulk-loaded in THREE queries (races / started horses / finished labels) instead of the
    former per-race N+1 (~135k round-trips over 67k races → 63s). Each query is ordered so the
    per-race grouping is byte-identical to the old per-race path: started horses keep
    ``ORDER BY horse_number, horse_id`` (Postgres NULLS LAST), labels keep the
    ``horseracing_db.labels`` semantics (finished-only, dead-heat-aware ``<=``,
    ``ORDER BY finish_order, horse_id``). Result is identical to the old loop (equivalence-tested).
    """
    race_stmt = select(Race.race_id, Race.race_date).order_by(Race.race_date, Race.race_id)
    if start_date is not None:
        race_stmt = race_stmt.where(Race.race_date >= start_date)
    if end_date is not None:
        race_stmt = race_stmt.where(Race.race_date <= end_date)
    race_rows = session.execute(race_stmt).all()

    # started horses (all races in range), grouped by race in (horse_number, horse_id) order
    rh_stmt = (
        select(
            RaceHorse.race_id, RaceHorse.horse_id, RaceHorse.frame,
            RaceHorse.horse_number, RaceHorse.odds, RaceHorse.popularity,
        )
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
        .order_by(Race.race_date, Race.race_id, RaceHorse.horse_number, RaceHorse.horse_id)
    )
    # finished labels (all races in range), same columns/derivation as horseracing_db.labels
    lbl_stmt = (
        select(
            RaceResult.race_id, RaceResult.horse_id,
            case((RaceResult.finish_order == 1, 1), else_=0).label("win"),
            case((RaceResult.finish_order <= 2, 1), else_=0).label("top2"),
            case((RaceResult.finish_order <= 3, 1), else_=0).label("top3"),
        )
        .join(Race, Race.race_id == RaceResult.race_id)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
        .order_by(Race.race_date, Race.race_id, RaceResult.finish_order, RaceResult.horse_id)
    )
    if start_date is not None:
        rh_stmt = rh_stmt.where(Race.race_date >= start_date)
        lbl_stmt = lbl_stmt.where(Race.race_date >= start_date)
    if end_date is not None:
        rh_stmt = rh_stmt.where(Race.race_date <= end_date)
        lbl_stmt = lbl_stmt.where(Race.race_date <= end_date)

    horses_by_race: dict[str, list] = defaultdict(list)
    for rh in session.execute(rh_stmt):
        horses_by_race[rh.race_id].append(
            HorseEntry(
                horse_id=rh.horse_id, frame=rh.frame, horse_number=rh.horse_number,
                result_market=ResultMarket(
                    odds=float(rh.odds) if rh.odds is not None else None,
                    popularity=rh.popularity,
                ),
            )
        )
    labels_by_race: dict[str, list] = defaultdict(list)
    for row in session.execute(lbl_stmt):
        labels_by_race[row.race_id].append(
            ScoringLabel(horse_id=row.horse_id, win=row.win, top2=row.top2, top3=row.top3)
        )

    eval_races: list[EvalRace] = []
    for race_id, race_date in race_rows:
        horses = horses_by_race.get(race_id)
        if not horses:  # no started horses
            continue
        labels = labels_by_race.get(race_id)
        if not labels:  # all non-finishers -> excluded from evaluation
            continue
        eval_races.append(
            EvalRace(
                context=RaceContext(
                    race_id=race_id, race_date=race_date, started_horses=tuple(horses)
                ),
                labels=tuple(labels),
            )
        )
    return eval_races
