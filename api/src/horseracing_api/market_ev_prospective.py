"""B8 (2026-10-28): the 期待回収率 columns' prospective record — read-time, facts only.

What is counted (spec 158's extraction rules, moved from the research script to the product):

- the stored rows of the displayed market-aware version (``mev-ens15-v1``; the table keeps the
  LATEST computation of a race only, so a pre-race computation that a later recompute replaced is
  gone — the record is what remains, not every computation that ever existed);
- every row carries the exact ``oddsobs=quote`` token (140: the odds were the race's win-quote
  provenance, not the ``updated_at`` proxy);
- every row was computed while the race had no result (``result_pending_at_compute``);
- one whole-field snapshot: a single run / compute time / observation time / booster;
- observation ≤ compute < post time (post time known);
- the stored field equals the race's current started field (ids and 馬番);
- values in range (odds ≥ 1, 0 < p < 1);
- the race has a result, an official win payout, and the paid 馬番 equal the winners' 馬番
  (race-level consistency, 139 D11 / ``attention_rules.race_payout_consistent``).

A race that fails a rule is counted ONCE under the first failing rule (``ProspectiveClass`` order),
so the funnel sums to the candidate count. Flat races only (158: 平地). Candidates are the races
held on/after the earliest column start date (the query's window); earlier races are not read.

Policy (fixed, not tuned here): 100 yen on EVERY horse strictly above the column's threshold
(``expected_return`` > 1.2 / ``expected_return_mix`` > 1.0), no cap, no ranking; a bet returns the
official payout of its 馬番 (per 100 yen) when that 馬番 is among the paid winners, else 0. The
recovery is Σreturn/Σstake with the race-day block bootstrap of 138 (same impl, b and seed). No
pass/fail rule is registered for these columns: the page only says how many points exist and
shows the interval; ``min_bets`` (138's first checkpoint) is the pre-registered minimum before any
reading.

The mixed value is recomputed exactly as ``build_market_ev`` does (eval ``market_mix``); a race
whose mixed value cannot be computed (no coefficients for its year, booster of another year) stays
in the ``expected_return`` tally and is reported as ``n_races_mix_unavailable`` for the mixed one.
"""

from __future__ import annotations

import datetime
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_eval import attention_rules as ar
from horseracing_eval import market_mix
from horseracing_eval.bootstrap import (
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
)

from .market_ev import MARKET_EV_THRESHOLD, StoredMarketEv, exceeds_threshold
from .schemas import (
    MarketEvProspectiveResponse,
    ProspectiveBootstrap,
    ProspectiveColumn,
    ProspectiveDay,
    ProspectiveDayColumn,
    ProspectiveFunnel,
)

__all__ = [
    "COLUMN_START_DATES",
    "MIN_BETS",
    "QUOTE_TOKEN",
    "ProspectiveRace",
    "build_market_ev_prospective",
    "classify_race",
    "earliest_start_date",
]

#: the per-row token of 140 that marks a genuine win-quote observation (training's constant)
QUOTE_TOKEN = "oddsobs=quote"
#: the pre-registered minimum number of 100-yen points before any reading (138's first checkpoint)
MIN_BETS = ar.CHECKPOINTS[0]
#: Feature 138's bootstrap (same impl / b / seed / block) — reused, not re-declared
BOOTSTRAP = ar.BOOTSTRAP
STAKE_YEN = 100
JUMP_TRACK_TYPE = "障"

COLUMN_START_DATES: dict[str, datetime.date] = {
    # the column switched to the 15-seed mean on 2026-10-02 and the registry's prospective start
    # (policy v2) is the registry constant; before it nothing is prospective for this column
    "expected_return": ar.PROSPECTIVE_START_DATE,
    "expected_return_mix": market_mix.MIX_PROSPECTIVE_START_DATE,
}


def earliest_start_date() -> datetime.date:
    return min(COLUMN_START_DATES.values())


@dataclass
class ProspectiveRace:
    """What the classification reads about one race (the router builds it from bulk queries)."""

    race_id: str
    race_date: datetime.date | None
    post_time: datetime.datetime | None
    track_type: str | None
    rows: Sequence[StoredMarketEv]
    #: current STARTED field: horse_id -> horse_number (None when unknown)
    started: Mapping[str, int | None]
    has_results: bool
    #: horse_ids finished 1st (official order)
    winner_horse_ids: frozenset[str] = frozenset()
    #: official win payout per 100 yen by 馬番
    payouts: Mapping[int, int] = field(default_factory=dict)


def _has_quote_token(logic_version: str) -> bool:
    return QUOTE_TOKEN in [seg.strip() for seg in str(logic_version).split(";")]


def classify_race(race: ProspectiveRace, *, since: datetime.date) -> str:
    """The single exclusive class of a race (first failing rule wins; see the module docstring)."""
    rows = list(race.rows)
    if race.race_date is None or race.race_date < since:
        raise ValueError(f"race {race.race_id} is before the window ({since}); not a candidate")
    if race.track_type is not None and JUMP_TRACK_TYPE in race.track_type:
        return "jump_race"
    if not rows or not all(_has_quote_token(r.logic_version) for r in rows):
        return "not_quote"
    if not all(r.result_pending_at_compute for r in rows):
        return "result_known_at_compute"
    snapshot = {
        (str(getattr(r, "run_id", "")), r.computed_at, r.odds_observed_at, r.booster,
         getattr(r, "booster_sha256", ""))
        for r in rows
    }
    if len(snapshot) != 1:
        return "not_single_snapshot"
    if race.post_time is None:
        return "post_time_unknown"
    head = rows[0]
    if head.odds_observed_at > head.computed_at:
        return "observed_after_compute"
    if head.computed_at >= race.post_time:
        return "computed_after_post"
    stored = {r.horse_id: (int(r.horse_number) if r.horse_number is not None else None)
              for r in rows}
    if set(stored) != set(race.started) or any(n is None for n in stored.values()):
        return "field_mismatch"
    if any(stored[h] != race.started[h] for h in stored):
        return "field_mismatch"
    for r in rows:
        odds = float(r.odds_used)
        p = float(r.win_prob)
        if not (math.isfinite(odds) and odds >= 1.0 and math.isfinite(p) and 0.0 < p < 1.0):
            return "invalid_values"
    if not race.has_results:
        return "pending_result"
    if not race.payouts:
        return "payout_race_missing"
    winner_numbers = [race.started.get(h) for h in race.winner_horse_ids]
    if not ar.race_payout_consistent(winner_numbers, race.payouts.keys()):
        return "payout_inconsistent"
    return "eligible"


def _mixed_values(rows: Sequence[StoredMarketEv], race_date: datetime.date) -> list[float] | None:
    """EV_mix per row (rows order) exactly as the race page computes it, or None (unavailable)."""
    if not market_mix.MIX_DISPLAY_ENABLED:
        return None
    if {r.model_version for r in rows} != {market_mix.MIX_SOURCE_MODEL_VERSION}:
        return None
    coef = market_mix.coefficients_for(race_date.year)
    if coef is None or {r.booster for r in rows} != {f"ensemble_{coef.year}.json"}:
        return None
    odds = [float(r.odds_used) for r in rows]
    try:
        p_mix = market_mix.mix_probabilities([float(r.win_prob) for r in rows], odds, coef)
    except ValueError:
        return None
    return [float(p) * o for p, o in zip(p_mix, odds, strict=True)]


@dataclass
class _Tally:
    n_races: int = 0
    n_races_mix_unavailable: int = 0
    n_races_with_bets: int = 0
    n_bets: int = 0
    n_hits: int = 0
    return_yen: int = 0
    #: race-day key -> [Σreturn, Σstake] over the bets of that day (block universe = days with bets)
    by_day: dict[str, list[float]] = field(default_factory=lambda: defaultdict(lambda: [0.0, 0.0]))
    days: dict[datetime.date, ProspectiveDayColumn] = field(default_factory=dict)

    def add_race(self, race_date: datetime.date, bets: Iterable[tuple[int, int]]) -> None:
        """``bets`` = (馬番, payout_yen or 0) of the selected horses of one eligible race."""
        bets = list(bets)
        self.n_races += 1
        day = self.days.get(race_date) or ProspectiveDayColumn(
            n_races=0, n_bets=0, n_hits=0, stake_yen=0, return_yen=0
        )
        day = day.model_copy(update={"n_races": day.n_races + 1})
        if bets:
            self.n_races_with_bets += 1
            hits = sum(1 for _n, pay in bets if pay > 0)
            ret = sum(pay for _n, pay in bets)
            self.n_bets += len(bets)
            self.n_hits += hits
            self.return_yen += ret
            acc = self.by_day[race_date.isoformat()]
            acc[0] += float(ret)
            acc[1] += float(STAKE_YEN * len(bets))
            day = day.model_copy(update={
                "n_bets": day.n_bets + len(bets), "n_hits": day.n_hits + hits,
                "stake_yen": day.stake_yen + STAKE_YEN * len(bets),
                "return_yen": day.return_yen + ret,
            })
        self.days[race_date] = day


def _ratio_ci(
    by_day: Mapping[str, list[float]],
) -> tuple[float | None, tuple | None, float | None, int]:
    days = sorted(by_day)
    if not days:
        return None, None, None, 0
    num = np.array([by_day[d][0] for d in days])
    den = np.array([by_day[d][1] for d in days])
    res = race_block_ratio_bootstrap_ci_v1(
        num, den, days, block=BOOTSTRAP["block"], b=int(BOOTSTRAP["b"]), seed=int(BOOTSTRAP["seed"])
    )
    point = float(res.point[0])
    undecidable = res.no_decision or not math.isfinite(float(res.ci_low[0]))
    if undecidable:
        return point, None, None, len(days)
    return (
        point,
        (float(res.ci_low[0]), float(res.ci_high[0])),
        centered_one_sided_p_from_replicates(res.replicates[0], point),
        len(days),
    )


def _column(column: str, threshold: float, tally: _Tally) -> ProspectiveColumn:
    point, ci, p, n_days = _ratio_ci(tally.by_day)
    return ProspectiveColumn(
        column=column,  # type: ignore[arg-type]
        threshold=threshold,
        start_date=COLUMN_START_DATES[column],
        n_races=tally.n_races,
        n_races_mix_unavailable=tally.n_races_mix_unavailable,
        n_races_with_bets=tally.n_races_with_bets,
        n_bets=tally.n_bets,
        n_hits=tally.n_hits,
        stake_yen=STAKE_YEN * tally.n_bets,
        return_yen=tally.return_yen,
        recovery_rate=point if tally.n_bets > 0 else None,
        ci=ci,
        p_one_sided=p,
        n_days=n_days,
        min_bets=int(MIN_BETS),
        remaining_to_min=max(0, int(MIN_BETS) - tally.n_bets),
        valuation_basis="official_win_payout",
        is_pseudo_selection=True,
        bootstrap=ProspectiveBootstrap(
            impl=str(BOOTSTRAP["impl"]), b=int(BOOTSTRAP["b"]), seed=int(BOOTSTRAP["seed"]),
            block=str(BOOTSTRAP["block"]), block_universe="race-days with >=1 bet of the column",
        ),
    )


def build_market_ev_prospective(
    races: Sequence[ProspectiveRace], *, model_version: str
) -> MarketEvProspectiveResponse:
    """Classify every candidate race, tally both columns and shape the response."""
    since = earliest_start_date()
    counts: dict[str, int] = defaultdict(int)
    tallies = {"expected_return": _Tally(), "expected_return_mix": _Tally()}
    days: dict[datetime.date, dict] = defaultdict(lambda: {"n_eligible": 0, "n_pending_result": 0})
    last_eligible: datetime.date | None = None

    for race in races:
        cls = classify_race(race, since=since)
        counts[cls] += 1
        if race.race_date is not None:
            if cls == "pending_result":
                days[race.race_date]["n_pending_result"] += 1
            elif cls == "eligible":
                days[race.race_date]["n_eligible"] += 1
        if cls != "eligible":
            continue
        assert race.race_date is not None
        race_date = race.race_date
        last_eligible = race_date if last_eligible is None else max(last_eligible, race_date)
        rows = list(race.rows)
        payouts = race.payouts

        if race_date >= COLUMN_START_DATES["expected_return"]:
            tallies["expected_return"].add_race(
                race_date,
                [(int(r.horse_number), int(payouts.get(int(r.horse_number), 0))) for r in rows
                 if exceeds_threshold(float(r.expected_return))],
            )
        if race_date >= COLUMN_START_DATES["expected_return_mix"]:
            mixed = _mixed_values(rows, race_date)
            if mixed is None:
                # counted as an eligible race of the column, with no bet (the value is absent)
                tallies["expected_return_mix"].n_races_mix_unavailable += 1
                tallies["expected_return_mix"].add_race(race_date, [])
            else:
                tallies["expected_return_mix"].add_race(
                    race_date,
                    [(int(r.horse_number), int(payouts.get(int(r.horse_number), 0)))
                     for r, ev in zip(rows, mixed, strict=True)
                     if market_mix.exceeds_mix_threshold(ev)],
                )

    empty_day = ProspectiveDayColumn(n_races=0, n_bets=0, n_hits=0, stake_yen=0, return_yen=0)
    by_day = [
        ProspectiveDay(
            race_date=d,
            n_eligible=days[d]["n_eligible"],
            n_pending_result=days[d]["n_pending_result"],
            expected_return=tallies["expected_return"].days.get(d, empty_day),
            expected_return_mix=tallies["expected_return_mix"].days.get(d, empty_day),
        )
        for d in sorted(days)
    ]
    funnel = ProspectiveFunnel(
        since=since,
        n_candidate_races=len(races),
        jump_race=counts["jump_race"],
        not_quote=counts["not_quote"],
        result_known_at_compute=counts["result_known_at_compute"],
        not_single_snapshot=counts["not_single_snapshot"],
        post_time_unknown=counts["post_time_unknown"],
        observed_after_compute=counts["observed_after_compute"],
        computed_after_post=counts["computed_after_post"],
        field_mismatch=counts["field_mismatch"],
        invalid_values=counts["invalid_values"],
        pending_result=counts["pending_result"],
        payout_race_missing=counts["payout_race_missing"],
        payout_inconsistent=counts["payout_inconsistent"],
        eligible=counts["eligible"],
    )
    return MarketEvProspectiveResponse(
        model_version=model_version,
        quote_token=QUOTE_TOKEN,
        stake_yen_per_bet=100,
        settlement="official_win_payout",
        funnel=funnel,
        columns=[
            _column("expected_return", MARKET_EV_THRESHOLD, tallies["expected_return"]),
            _column("expected_return_mix", market_mix.MIX_EV_THRESHOLD,
                    tallies["expected_return_mix"]),
        ],
        by_day=by_day,
        last_eligible_race_date=last_eligible,
    )


def races_from_queries(
    rows_with_race: Sequence[tuple],
    entries: Iterable,
    results: Iterable,
    payouts: Iterable,
) -> list[ProspectiveRace]:
    """Assemble ``ProspectiveRace`` objects from the bulk query rows (router helper)."""
    rows_by_race: dict[str, list] = defaultdict(list)
    race_by_id: dict[str, object] = {}
    for row, race in rows_with_race:
        rows_by_race[row.race_id].append(row)
        race_by_id[row.race_id] = race
    started: dict[str, dict[str, int | None]] = defaultdict(dict)
    for e in entries:
        if e.entry_status == EntryStatus.STARTED:
            started[e.race_id][e.horse_id] = (
                int(e.horse_number) if e.horse_number is not None else None
            )
    # (plain dict/list accumulation: the api's static read-only guard forbids ``.add()`` calls)
    has_results: dict[str, bool] = {}
    winners: dict[str, list[str]] = defaultdict(list)
    for race_id, horse_id, finish_order, status in results:
        has_results[race_id] = True
        if finish_order == 1 and status == ResultStatus.FINISHED:
            winners[race_id].append(horse_id)
    pays: dict[str, dict[int, int]] = defaultdict(dict)
    for race_id, number, yen in payouts:
        pays[race_id][int(number)] = int(yen)
    out = []
    for race_id in sorted(rows_by_race, key=lambda rid: (race_by_id[rid].race_date, rid)):
        race = race_by_id[race_id]
        out.append(ProspectiveRace(
            race_id=race_id, race_date=race.race_date, post_time=race.post_time,
            track_type=race.track_type, rows=rows_by_race[race_id],
            started=started.get(race_id, {}), has_results=race_id in has_results,
            winner_horse_ids=frozenset(winners.get(race_id, ())),
            payouts=pays.get(race_id, {}),
        ))
    return out
