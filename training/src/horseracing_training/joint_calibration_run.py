"""DB loader and orchestration for the pre-registered joint-calibration diagnostic.

The loader is deliberately stricter than the older market-q loaders.  Its population is the
final ``started`` field, and one bad or missing win quote invalidates the whole race; it never
constructs q from the covered subset.  Exotic grids are read only from the local
``exotic_quotes`` table.  No fetcher or scraping fallback exists in this module.

Pre-registration: ``docs/plan/prereg-joint-calibration.md`` (rev2)
Frozen interface: ``docs/plan/contract-joint-calibration.md`` (section B)
"""

from __future__ import annotations

import datetime
import itertools
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from horseracing_db.enums import BetType, EntryStatus, ResultStatus
from horseracing_db.models import ExoticQuote, Race, RaceHorse, RaceResult
from horseracing_eval.hashing import race_set_hash, stable_hash
from horseracing_eval.joint_calibration import (
    CONTRACT_VERSION,
    MARKET_LAMBDA2,
    MARKET_LAMBDA3,
    WIDE_MIN_FIELD,
    JointCalibRace,
    evaluate,
)
from horseracing_probability.engine import joint_probabilities
from sqlalchemy import select
from sqlalchemy.orm import Session

FROZEN_FROM = datetime.date(2019, 1, 1)
FROZEN_TO = datetime.date(2026, 7, 12)

# Feature 069 froze the valid final WIN-odds range before this diagnostic.  1.0 is a legitimate
# JRA return-of-stake quote; 999.9 is the capped/missing sentinel and is excluded.
WIN_ODDS_MIN = 1.0
WIN_ODDS_SENTINEL = 999.9

GRID_BET_TYPES: tuple[str, ...] = (BetType.QUINELLA, BetType.WIDE, BetType.TRIO)
GRID_ARITY = {BetType.QUINELLA: 2, BetType.WIDE: 2, BetType.TRIO: 3}

# This order is also the tie-break order.  A source race contributes to at most one counter.
# The final two reasons remove only the US2 price-grid endpoint; the race remains in the primary
# stage-loss/NLL/reliability population (JointCalibRace.grid is optional by contract).
EXCLUSION_REASONS: tuple[str, ...] = (
    "partial_or_invalid_odds",
    "top3_dead_heat",
    "missing_or_duplicate_rank",
    "result_horse_not_started",
    "duplicate_horse_number",
    "incomplete_grid",
    "unsupported_wide_field",
)

ORIGINAL_US2_COHORT_SIZE = 1_001
US2_HELDOUT_SCOPE = "heldout_mod3_334"
US2_DEVELOPMENTAL_SCOPE = "developmental_all_1001"


class JointCalibrationRunError(RuntimeError):
    """Refuse a misleading readout when source key spaces do not agree."""


@dataclass(frozen=True)
class _Entry:
    horse_id: str
    number: int | None
    odds: Any
    status: str


@dataclass(frozen=True)
class _Result:
    horse_id: str
    rank: int | None
    status: str


@dataclass(frozen=True)
class _Quote:
    quotes: Any
    n_combinations: int
    official_at: datetime.datetime | None
    observed_at: datetime.datetime
    created_at: datetime.datetime


@dataclass(frozen=True)
class _Us2Membership:
    scope: str
    cohort_boundary_proven: bool
    cohort_race_ids: tuple[str, ...]
    fit_race_ids: tuple[str, ...]
    validation_race_ids: tuple[str, ...]
    selected_race_ids: tuple[str, ...]
    reconstruction: str


@dataclass(frozen=True)
class _BuildAudit:
    n_source_races: int
    grid_rows_dropped_scratched: int
    membership: _Us2Membership
    scoreable_us2_race_ids: tuple[str, ...]
    quote_content_hash: str
    expected_key_set_hash: str
    expected_key_counts: dict[str, dict[str, int]]
    quote_observations: tuple[dict[str, Any], ...]


def _valid_win_odds(value: Any) -> bool:
    if value is None:
        return False
    try:
        odds = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(odds) and WIN_ODDS_MIN <= odds < WIN_ODDS_SENTINEL


def _valid_grid_odds(value: Any) -> float | None:
    """Return a usable exotic low quote; exotic prices legitimately exceed 999.9."""
    if value is None:
        return None
    try:
        odds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return odds if math.isfinite(odds) and odds > 0.0 else None


def _selection(raw_key: Any, *, race_id: str, bet_type: str) -> tuple[int, ...]:
    if not isinstance(raw_key, str):
        raise JointCalibrationRunError(
            f"{race_id} {bet_type}: non-string quote key {raw_key!r}"
        )
    try:
        numbers = tuple(int(part) for part in raw_key.split("-"))
    except (TypeError, ValueError) as exc:
        raise JointCalibrationRunError(
            f"{race_id} {bet_type}: malformed quote key {raw_key!r}"
        ) from exc
    if len(numbers) != GRID_ARITY[bet_type] or len(set(numbers)) != len(numbers):
        raise JointCalibrationRunError(
            f"{race_id} {bet_type}: wrong-arity/repeated-horse quote key {raw_key!r}"
        )
    # All three US2 types are unordered.  Canonical ascending tuples are the frozen cross-package
    # key contract and prevent the historical frozenset-iteration mismatch from recurring.
    return tuple(sorted(numbers))


def _low_quote(value: Any) -> float | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    return _valid_grid_odds(value[0])


def _expected_keys(numbers: tuple[int, ...], bet_type: str) -> set[tuple[int, ...]]:
    return set(itertools.combinations(numbers, GRID_ARITY[bet_type]))


def _build_grid(
    race_id: str,
    records: dict[str, _Quote],
    *,
    started_numbers: tuple[int, ...],
    all_entry_numbers: set[int],
) -> tuple[dict[str, dict[tuple[int, ...], float]] | None, int, dict[str, set[tuple[int, ...]]]]:
    """Build a complete canonical three-pool grid, dropping only known non-starters.

    A missing expected key or invalid price is ordinary incompleteness and excludes this race from
    US2.  An unexpected key, unknown horse number, duplicate canonical key, or inconsistent
    ``n_combinations`` is an integrity mismatch and aborts the readout, matching the established
    exotic-price-edge fail-closed discipline.
    """
    if set(records) != set(GRID_BET_TYPES):
        return None, 0, {}

    started = set(started_numbers)
    result: dict[str, dict[tuple[int, ...], float]] = {}
    expected_by_type: dict[str, set[tuple[int, ...]]] = {}
    dropped_scratched = 0

    for bet_type in GRID_BET_TYPES:
        record = records[bet_type]
        if not isinstance(record.quotes, dict):
            return None, dropped_scratched, {}
        if record.n_combinations != len(record.quotes):
            raise JointCalibrationRunError(
                f"{race_id} {bet_type}: n_combinations={record.n_combinations} but "
                f"JSON grid has {len(record.quotes)} keys"
            )

        canonical: dict[tuple[int, ...], float] = {}
        for raw_key, raw_value in record.quotes.items():
            key = _selection(raw_key, race_id=race_id, bet_type=bet_type)
            if not started.issuperset(key):
                if not all_entry_numbers.issuperset(key):
                    unknown = sorted(set(key) - all_entry_numbers)
                    raise JointCalibrationRunError(
                        f"{race_id} {bet_type}: quote key {key} contains unknown horse "
                        f"number(s) {unknown}"
                    )
                dropped_scratched += 1
                continue
            if key in canonical:
                raise JointCalibrationRunError(
                    f"{race_id} {bet_type}: duplicate canonical quote key {key}"
                )
            odds = _low_quote(raw_value)
            if odds is None:
                return None, dropped_scratched, {}
            canonical[key] = odds

        expected = _expected_keys(started_numbers, bet_type)
        unexpected = set(canonical) - expected
        if unexpected:
            sample = sorted(unexpected)[0]
            raise JointCalibrationRunError(
                f"{race_id} {bet_type}: quote combination {sample} is absent from the "
                f"{len(expected)} expected final-starter combinations — key spaces disagree"
            )
        if set(canonical) != expected:
            # A proper subset is an incomplete capture, not permission to devig/score a partial
            # field.  The whole race leaves US2 through its single incomplete_grid counter.
            return None, dropped_scratched, {}
        result[bet_type] = canonical
        expected_by_type[bet_type] = expected

    return result, dropped_scratched, expected_by_type


def _iso(value: datetime.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _resolve_us2_membership(
    quotes: dict[str, dict[str, _Quote]],
    *,
    legacy_joinable_race_ids: set[str],
) -> _Us2Membership:
    """Recover the original 1,001-race acquisition cohort and its 667/334 split.

    The historical fit sorted the original race IDs lexicographically and assigned indices
    divisible by three to validation.  ``created_at`` survives quote UPSERTs, unlike mutable quote
    content/``observed_at``.  We only call the split held out when the first 1,001 distinct quote
    races form a strict acquisition-time block and each carries all three US2 grids.
    """
    acquired = sorted(
        (min(record.created_at for record in records.values()), race_id)
        for race_id, records in quotes.items()
        if records
    )
    cohort_pairs = acquired[:ORIGINAL_US2_COHORT_SIZE]
    cohort = tuple(sorted(race_id for _, race_id in cohort_pairs))
    cohort_set = set(cohort)
    complete = len(cohort) == ORIGINAL_US2_COHORT_SIZE and all(
        set(quotes[race_id]) == set(GRID_BET_TYPES) for race_id in cohort
    ) and set(cohort).issubset(legacy_joinable_race_ids)

    if len(acquired) == ORIGINAL_US2_COHORT_SIZE:
        strict_boundary = True
    elif len(acquired) > ORIGINAL_US2_COHORT_SIZE:
        last_cohort_write = max(
            record.created_at
            for race_id in cohort
            for record in quotes[race_id].values()
        )
        first_later_write = min(
            record.created_at
            for race_id, records in quotes.items()
            if race_id not in cohort_set
            for record in records.values()
        )
        strict_boundary = last_cohort_write < first_later_write
    else:
        strict_boundary = False

    proven = complete and strict_boundary
    fit = tuple(race_id for index, race_id in enumerate(cohort) if index % 3 != 0)
    validation = tuple(race_id for index, race_id in enumerate(cohort) if index % 3 == 0)
    if proven and len(fit) == 667 and len(validation) == 334:
        return _Us2Membership(
            scope=US2_HELDOUT_SCOPE,
            cohort_boundary_proven=True,
            cohort_race_ids=cohort,
            fit_race_ids=fit,
            validation_race_ids=validation,
            selected_race_ids=validation,
            reconstruction="first 1001 distinct quote races by immutable created_at; strict "
                           "boundary proven; lexicographic race_id index modulo 3 "
                           "(0=validation, 1/2=fit)",
        )

    if len(cohort) != ORIGINAL_US2_COHORT_SIZE or not complete:
        raise JointCalibrationRunError(
            "cannot isolate the complete original 1,001-race US2 quote cohort; refusing to "
            "mislabel a different population as developmental_all_1001"
        )

    # Never modulo-split a later-expanded/ambiguous quote population.  Rev2 explicitly permits
    # scoring the complete 1,001-race cohort as developmental when exact split membership cannot
    # be proved.  The cohort itself must still be complete, checked above.
    return _Us2Membership(
        scope=US2_DEVELOPMENTAL_SCOPE,
        cohort_boundary_proven=False,
        cohort_race_ids=cohort,
        fit_race_ids=(),
        validation_race_ids=(),
        selected_race_ids=cohort,
        reconstruction="exact acquisition boundary or complete 1001-race cohort not proven; "
                       "no modulo split applied; earliest acquisition cohort used developmentally",
    )


def _build(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
) -> tuple[list[JointCalibRace], dict[str, int], _BuildAudit]:
    if date_from > date_to:
        raise ValueError(f"date_from {date_from} is after date_to {date_to}")

    race_rows = session.execute(
        select(Race.race_id, Race.race_date)
        .where(Race.race_date >= date_from, Race.race_date <= date_to)
        .order_by(Race.race_id)
    ).all()
    race_days = {race_id: day for race_id, day in race_rows}

    entries: dict[str, list[_Entry]] = defaultdict(list)
    for race_id, horse_id, number, odds, status in session.execute(
        select(
            RaceHorse.race_id,
            RaceHorse.horse_id,
            RaceHorse.horse_number,
            RaceHorse.odds,
            RaceHorse.entry_status,
        )
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(Race.race_date >= date_from, Race.race_date <= date_to)
        .order_by(RaceHorse.race_id, RaceHorse.horse_id)
    ):
        entries[race_id].append(_Entry(horse_id, number, odds, status))

    results: dict[str, list[_Result]] = defaultdict(list)
    for race_id, horse_id, rank, status in session.execute(
        select(
            RaceResult.race_id,
            RaceResult.horse_id,
            RaceResult.finish_order,
            RaceResult.result_status,
        )
        .join(Race, Race.race_id == RaceResult.race_id)
        .where(Race.race_date >= date_from, Race.race_date <= date_to)
        .order_by(RaceResult.race_id, RaceResult.horse_id)
    ):
        results[race_id].append(_Result(horse_id, rank, status))

    quotes: dict[str, dict[str, _Quote]] = defaultdict(dict)
    for (
        race_id,
        bet_type,
        grid,
        n_combinations,
        official_at,
        observed_at,
        created_at,
    ) in session.execute(
        select(
            ExoticQuote.race_id,
            ExoticQuote.bet_type,
            ExoticQuote.quotes,
            ExoticQuote.n_combinations,
            ExoticQuote.official_at,
            ExoticQuote.observed_at,
            ExoticQuote.created_at,
        )
        .where(ExoticQuote.bet_type.in_(GRID_BET_TYPES))
        .order_by(ExoticQuote.race_id, ExoticQuote.bet_type)
    ):
        if bet_type in quotes[race_id]:
            raise JointCalibrationRunError(f"{race_id} {bet_type}: duplicate exotic quote row")
        quotes[race_id][bet_type] = _Quote(
            quotes=grid,
            n_combinations=int(n_combinations),
            official_at=official_at,
            observed_at=observed_at,
            created_at=created_at,
        )

    legacy_joinable = {
        race_id
        for race_id, race_entries in entries.items()
        if any(
            entry.status == EntryStatus.STARTED and entry.odds is not None
            for entry in race_entries
        )
    }
    membership = _resolve_us2_membership(
        quotes,
        legacy_joinable_race_ids=legacy_joinable,
    )
    selected_us2 = set(membership.selected_race_ids)
    exclusions = dict.fromkeys(EXCLUSION_REASONS, 0)
    races: list[JointCalibRace] = []
    dropped_scratched = 0
    scoreable_us2_ids: list[str] = []
    quote_hashes: dict[str, str] = {}
    expected_hashes: dict[str, str] = {}
    expected_counts: dict[str, dict[str, int]] = {}
    observations: list[dict[str, Any]] = []

    for race_id in membership.selected_race_ids:
        records = quotes.get(race_id, {})
        quote_hashes[race_id] = stable_hash(
            {
                bet_type: {
                    "quotes": record.quotes,
                    "n_combinations": record.n_combinations,
                }
                for bet_type, record in records.items()
            }
        )
        for bet_type, quote in sorted(records.items()):
            observations.append(
                {
                    "race_id": race_id,
                    "bet_type": bet_type,
                    "created_at": _iso(quote.created_at),
                    "official_at": _iso(quote.official_at),
                    "observed_at": _iso(quote.observed_at),
                }
            )

    for race_id, day in race_rows:
        all_entries = entries.get(race_id, [])
        started_entries = [e for e in all_entries if e.status == EntryStatus.STARTED]

        # Mutually-exclusive priority follows EXCLUSION_REASONS exactly.
        if any(not _valid_win_odds(e.odds) for e in started_entries):
            exclusions["partial_or_invalid_odds"] += 1
            continue

        race_results = results.get(race_id, [])
        rank_horses: dict[int, list[str]] = defaultdict(list)
        for result in race_results:
            if result.status == ResultStatus.FINISHED and result.rank in (1, 2, 3):
                rank_horses[int(result.rank)].append(result.horse_id)
        if any(len(rank_horses[rank]) > 1 for rank in (1, 2, 3)):
            exclusions["top3_dead_heat"] += 1
            continue
        if any(len(rank_horses[rank]) != 1 for rank in (1, 2, 3)):
            exclusions["missing_or_duplicate_rank"] += 1
            continue

        started_horse_ids = {entry.horse_id for entry in started_entries}
        if any(result.horse_id not in started_horse_ids for result in race_results):
            exclusions["result_horse_not_started"] += 1
            continue

        raw_numbers = [entry.number for entry in started_entries]
        if (
            any(number is None for number in raw_numbers)
            or len(set(raw_numbers)) != len(raw_numbers)
        ):
            exclusions["duplicate_horse_number"] += 1
            continue
        numbers = tuple(sorted(int(number) for number in raw_numbers if number is not None))
        number_by_horse = {entry.horse_id: int(entry.number) for entry in started_entries}

        inv = [1.0 / float(next(e.odds for e in started_entries if e.number == number))
               for number in numbers]
        total = math.fsum(inv)
        q = tuple(value / total for value in inv)
        top3 = tuple(number_by_horse[rank_horses[rank][0]] for rank in (1, 2, 3))

        all_entry_numbers = {int(e.number) for e in all_entries if e.number is not None}
        eval_grid: dict[str, dict[tuple[int, ...], float]] | None = None
        if race_id in selected_us2:
            source_grid, dropped, expected = _build_grid(
                race_id,
                quotes.get(race_id, {}),
                started_numbers=numbers,
                all_entry_numbers=all_entry_numbers,
            )
            dropped_scratched += dropped
            if source_grid is None:
                exclusions["incomplete_grid"] += 1
            else:
                scoreable_us2_ids.append(race_id)
                expected_hashes[race_id] = stable_hash(
                    {bet_type: sorted(keys) for bet_type, keys in expected.items()}
                )
                expected_counts[race_id] = {
                    bet_type: len(keys) for bet_type, keys in expected.items()
                }
                eval_grid = dict(source_grid)
                if len(numbers) < WIDE_MIN_FIELD:
                    exclusions["unsupported_wide_field"] += 1
                    # Quinella/trio remain valid US2 comparisons.  Wide is absent rather than
                    # silently mixing the N=5-7 settlement/engine mismatch into the N>=8 estimand.
                    eval_grid.pop(BetType.WIDE)

        races.append(
            JointCalibRace(
                race_id=race_id,
                day=day.isoformat(),
                numbers=numbers,
                q=q,
                top3=top3,
                grid=eval_grid,
            )
        )

    audit = _BuildAudit(
        n_source_races=len(race_days),
        grid_rows_dropped_scratched=dropped_scratched,
        membership=membership,
        scoreable_us2_race_ids=tuple(sorted(scoreable_us2_ids)),
        quote_content_hash=stable_hash(quote_hashes),
        expected_key_set_hash=stable_hash(expected_hashes),
        expected_key_counts=expected_counts,
        quote_observations=tuple(observations),
    )
    return races, exclusions, audit


def build(
    session: Session,
    *,
    date_from: datetime.date = FROZEN_FROM,
    date_to: datetime.date = FROZEN_TO,
) -> tuple[list[JointCalibRace], dict[str, int]]:
    """Load the primary population plus mutually-exclusive exclusion counters."""
    races, exclusions, _ = _build(session, date_from=date_from, date_to=date_to)
    return races, exclusions


def run(
    session: Session,
    *,
    seed: int,
    bootstrap_b: int,
    date_from: datetime.date = FROZEN_FROM,
    date_to: datetime.date = FROZEN_TO,
) -> dict[str, Any]:
    if date_from != FROZEN_FROM or date_to != FROZEN_TO:
        raise JointCalibrationRunError(
            "joint-calibration is pre-registered for 2019-01-01..2026-07-12; refusing a "
            f"different window ({date_from}..{date_to})"
        )
    races, exclusions, audit = _build(session, date_from=date_from, date_to=date_to)
    if not races:
        raise JointCalibrationRunError("no eligible races — refusing to emit a readout")

    # eval must not import horseracing_probability (the arrow runs probability -> eval), so the
    # 009 engine is injected here — training is the layer that owns both workspaces.
    payload = evaluate(races, b=bootstrap_b, seed=seed, joint_fn=joint_probabilities)
    if not isinstance(payload, dict):
        raise JointCalibrationRunError("evaluate() did not return the frozen dict payload")

    membership = audit.membership
    selected_hash = race_set_hash(membership.selected_race_ids)
    scoreable_hash = race_set_hash(audit.scoreable_us2_race_ids)
    main_exclusions = sum(exclusions[name] for name in EXCLUSION_REASONS[:5])

    instrument_contract = dict(payload.get("instrument_contract", {}))
    instrument_contract.update(
        {
            "kind": "joint_calibration",
            "secondary": True,
            "can_adopt": False,
            "estimand": "calibration and predictive fit of the frozen closing WIN-q to "
                        "PL/Harville joint mapping, conditional on information available to it",
            "primary_contrast": "arm difference in L2+L3",
            "exclusion_priority": list(EXCLUSION_REASONS),
            "exclusion_note": "the first five remove a race from all endpoints; the final two "
                              "remove only an unavailable/unsupported US2 grid endpoint",
        }
    )
    provenance = dict(payload.get("provenance", {}))
    provenance.update(
        {
            "contract_version": CONTRACT_VERSION,
            "frozen_window": {"from": FROZEN_FROM.isoformat(), "to": FROZEN_TO.isoformat()},
            "requested_window": {"from": date_from.isoformat(), "to": date_to.isoformat()},
            "matches_frozen_window": date_from == FROZEN_FROM and date_to == FROZEN_TO,
            "n_source_races": audit.n_source_races,
            "n_races": len(races),
            "n_days": len({race.day for race in races}),
            "n_excluded_from_primary": main_exclusions,
            "scored_race_set_hash": race_set_hash(race.race_id for race in races),
            "seed": seed,
            "bootstrap_b": bootstrap_b,
            "market_stage_discount": {"lambda2": MARKET_LAMBDA2, "lambda3": MARKET_LAMBDA3},
            "win_odds_valid_range": {
                "ge": WIN_ODDS_MIN,
                "lt": WIN_ODDS_SENTINEL,
                "sentinel": WIN_ODDS_SENTINEL,
                "requires_every_final_starter": True,
            },
            "us2_scope": membership.scope,
            "us2_cohort_boundary_proven": membership.cohort_boundary_proven,
            "us2_split_reconstruction": membership.reconstruction,
            "us2_original_cohort_race_ids": list(membership.cohort_race_ids),
            "us2_original_cohort_race_set_hash": race_set_hash(membership.cohort_race_ids),
            "us2_selected_race_ids": list(membership.selected_race_ids),
            "us2_selected_race_set_hash": selected_hash,
            "n_us2_selected_races": len(membership.selected_race_ids),
            "us2_scoreable_race_ids": list(audit.scoreable_us2_race_ids),
            "us2_scoreable_race_set_hash": scoreable_hash,
            "n_us2_scoreable_races": len(audit.scoreable_us2_race_ids),
            "us2_split_membership": {
                "fit": list(membership.fit_race_ids),
                "validation": list(membership.validation_race_ids),
                "developmental": (
                    list(membership.selected_race_ids)
                    if membership.scope == US2_DEVELOPMENTAL_SCOPE
                    else []
                ),
            },
            "us2_quote_content_hash": audit.quote_content_hash,
            "us2_expected_key_set_hash": audit.expected_key_set_hash,
            "us2_expected_key_counts": audit.expected_key_counts,
            "us2_quote_observations": list(audit.quote_observations),
            "grid_rows_dropped_scratched": audit.grid_rows_dropped_scratched,
        }
    )
    payload["instrument_contract"] = instrument_contract
    payload["provenance"] = provenance
    payload["exclusions"] = {
        **exclusions,
        # Row-level audit, not an eighth mutually-exclusive race exclusion.
        "price_rows_dropped_scratched": audit.grid_rows_dropped_scratched,
    }
    return payload
