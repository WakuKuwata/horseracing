"""Read-only assembly of the Feature 138 注目条件 (attention conditions) S1-S5.

Everything that DECIDES something lives in the eval registry ``horseracing_eval.attention_rules``:
which rule a horse matches (``matches`` / ``chip_now``), how a judged horse (a pick) is classified
(``classify_pick``), how a stage follows from the recorded checkpoint decisions (``stage_from``),
the axis levels, the chip choice, the field digest and the race-day bootstrap (``ratio_ci``). This
module only joins stored rows to those functions and shapes the responses. It never re-judges a
pick (the first computation is final, plan D12/D16) and never decides a checkpoint (training
records decisions; the stage follows the records, D18). No DB access here (queries.py does it).

Display sources (plan D17, FR-016):

- ``judged`` = the values frozen on the live (non-voided) pick rows of the race's FIRST ensemble
  computation; the chip is decided from them and never follows later odds.
- ``current`` = the latest row of the DISPLAYED market-ev version (the 15-seed average). Its
  single-seed value is attached only when the single-seed latest row carries the SAME run_id, so two
  different moments never merge into one "current" value (CX-03). ``current`` is null — and
  ``chip_now`` is ``unknown`` — whenever the 137 market-ev state of the displayed version is not
  ``available`` (not computed / field changed / odds missing).

Prospective tally (plan 0.5, FR-005): computed at read time from every pick of a rule (no date
filter), memoised per rule in ``TALLY_MEMO`` under the key from ``queries.attention_memo_key``
(D22), so a race-detail or day read reuses the tally and its bootstrap instead of re-running them.
Win probabilities and p̂ are never returned (constitution IV).

Settlement (selection policy v2, 139): the counted picks are settled at the OFFICIAL win payout
(``official`` = the stage basis and the prospective level); the same picks at the judged odds
(``frozen`` = policy v1's settlement) and at the current stored odds (``stored``) are references.
"""

from __future__ import annotations

import datetime
import math
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import numpy as np
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_eval import attention_rules as ar

from .market_ev import build_market_ev
from .schemas import (
    AttentionAvailable,
    AttentionBacktest,
    AttentionBacktestBootstrap,
    AttentionBuyTimeExpectation,
    AttentionBuyTimeSource,
    AttentionCheckpointBootstrap,
    AttentionDayItem,
    AttentionDayResponse,
    AttentionExclusionCounts,
    AttentionFlags,
    AttentionFreshnessBand,
    AttentionFrozenBasis,
    AttentionFrozenStats,
    AttentionHorse,
    AttentionJudgedFreshness,
    AttentionLevels,
    AttentionOddsDrift,
    AttentionOfficialBasis,
    AttentionPriceNoise,
    AttentionProspective,
    AttentionProspectiveBootstrap,
    AttentionRuleLevels,
    AttentionRulesResponse,
    AttentionSelectedCalibration,
    AttentionSelectedWindows,
    AttentionStoredBasis,
    AttentionUnavailable,
    CheckpointDecision,
    EvSnapshot,
    MarketEvAvailable,
    RuleSummary,
    StageDetail,
)

DISCLAIMER = (
    "注目条件は過去データで最も有望だった条件で、検証済みの条件ではありません。"
    "S1・S2 は結果を見てから見つけた条件で、過去検証の p 値は多重探索を補正していません。"
    "過去検証の回収率は確定オッズからの近似、判断時点の見込みは過去データからの換算です。"
    "前向き検証の回収率は公式の単勝払戻で精算しています。的中や利益を保証するものではありません。"
)

#: 判断時鮮度帯 edges (seconds between the pick's odds observation and the post time)
_FRESHNESS_BANDS = (("<=10m", 600.0), ("<=60m", 3600.0), (">60m", math.inf))
_NO_TIME = datetime.datetime.min.replace(tzinfo=datetime.UTC)


# --- stored rows (attribute protocols; queries.py returns ORM objects / labelled rows) ---------


class StoredPick(Protocol):
    pick_id: uuid.UUID
    race_id: str
    horse_id: str
    horse_number: int | None
    rule_id: str
    kind: str
    voids_pick_id: uuid.UUID | None
    ens_expected_return: Decimal | None
    single_expected_return: Decimal | None
    odds_used: Decimal | None
    odds_observed_at: datetime.datetime | None
    days_since_last: Decimal | None
    field_digest: str
    run_id: uuid.UUID
    computed_at: datetime.datetime


class StoredScan(Protocol):
    race_id: str
    computed_at: datetime.datetime


class Entry(Protocol):
    horse_id: str
    horse_number: int | None
    entry_status: str
    odds: Decimal | None


class TallyRow(Protocol):
    """One ``kind='pick'`` row plus what is known about it now (queries.attention_tally_rows)."""

    pick_id: uuid.UUID
    race_id: str
    horse_number: int
    rule_id: str
    computed_at: datetime.datetime
    post_time: datetime.datetime | None
    odds_observed_at: datetime.datetime | None
    result_pending_at_compute: bool
    odds_used: Decimal
    field_digest: str
    voided: bool
    has_race_result: bool
    horse_has_result: bool
    finish_order: int | None
    result_status: str | None
    n_winners: int | None
    stored_odds: Decimal | None
    #: the official win payout (per 100 yen) of the pick's 馬番; None when the race has no row
    #: for it
    official_payout_yen: int | None
    #: the race has at least one official win payout row
    race_payout_known: bool
    #: the race's payout rows name exactly its winners (139 D11, race level)
    race_payout_consistent: bool


class CheckpointRecord(Protocol):
    rule_id: str
    checkpoint: int
    decision: str
    n_counted: int
    n_hits: int
    roi_frozen: Decimal
    ci_low: Decimal | None
    ci_high: Decimal | None
    bootstrap: dict
    counted_pick_ids_sha256: str
    settlement_cutoff: datetime.datetime
    prospective_start_date: datetime.date
    skipped_pending_before_last: int
    decided_at: datetime.datetime


def _f(value) -> float | None:
    return None if value is None else float(value)


# --- prospective tally (per rule) ---------------------------------------------------------------


@dataclass(frozen=True)
class RuleTally:
    """The read-time prospective state of one rule (memoised; never persisted)."""

    rule_id: str
    stage: StageDetail
    #: pooled ROI of the counted picks at the official win payout — the prospective level's input
    #: under policy v2 (None when nothing is counted)
    point_roi_official: float | None
    prospective: AttentionProspective


def pick_facts(row: TallyRow) -> ar.PickFacts:
    """``PickFacts`` of one stored pick. ``dead_heat`` is race level (winners != 1, zero winners
    included) — the frozen backtest's definition. The official payout and the race-level
    "payout known" / "payout consistent" flags are passed through as read; classifying a missing
    or inconsistent payout is the registry's job (``classify_pick``)."""
    won = row.finish_order == 1 and row.result_status == ResultStatus.FINISHED
    return ar.PickFacts(
        pick_id=str(row.pick_id),
        race_id=row.race_id,
        horse_number=int(row.horse_number),
        voided=bool(row.voided),
        computed_at=row.computed_at,
        post_time=row.post_time,
        odds_observed_at=row.odds_observed_at,
        result_pending_at_compute=bool(row.result_pending_at_compute),
        has_race_result=bool(row.has_race_result),
        horse_has_result=bool(row.horse_has_result),
        won=bool(won),
        dead_heat=int(row.n_winners or 0) != 1,
        odds_used=float(row.odds_used),
        stored_odds=_f(row.stored_odds),
        official_payout_yen=_f(row.official_payout_yen),
        race_payout_known=bool(row.race_payout_known),
        race_payout_consistent=bool(row.race_payout_consistent),
    )


def _has_stored_odds(f: ar.PickFacts) -> bool:
    """Valid current stored win odds (the 137 rule: present and >= 1.0)."""
    return f.stored_odds is not None and f.stored_odds >= 1.0


def _stored_payout(f: ar.PickFacts) -> float:
    """Reference settlement at the CURRENT stored odds (100 yen). Only picks with valid stored odds
    reach here (``_has_stored_odds``): a pick whose stored odds disappeared (re-ingested as null,
    067 re-key) is left out of the stored basis and counted, never settled at the judged odds —
    that would mix the frozen basis into a figure labelled ``stored_odds_mutable``."""
    if not _has_stored_odds(f):
        raise ValueError(f"pick {f.pick_id} has no valid stored odds")
    return 100.0 * float(f.stored_odds) if f.won else 0.0


def _ci(low, high) -> tuple[float, float] | None:
    if low is None or high is None:
        return None
    return (float(low), float(high))


def _decision_view(record: CheckpointRecord) -> CheckpointDecision:
    boot = record.bootstrap if isinstance(record.bootstrap, dict) else {}
    settlement = boot.get("settlement")

    def _opt_int(value) -> int | None:
        return None if value is None else int(value)

    def _opt_str(value) -> str | None:
        return None if value is None else str(value)

    return CheckpointDecision(
        checkpoint=int(record.checkpoint),
        decision=record.decision,
        n_counted=int(record.n_counted),
        n_hits=int(record.n_hits),
        roi_frozen=float(record.roi_frozen),
        valuation_basis=(
            settlement
            if isinstance(settlement, str) and settlement in ar.SETTLEMENT_BASES
            else None
        ),
        ci=_ci(record.ci_low, record.ci_high),
        decided_at=record.decided_at,
        settlement_cutoff=record.settlement_cutoff,
        prospective_start_date=record.prospective_start_date,
        skipped_pending_before_last=int(record.skipped_pending_before_last),
        counted_pick_ids_sha256=record.counted_pick_ids_sha256,
        bootstrap=AttentionCheckpointBootstrap(
            impl=_opt_str(boot.get("impl")),
            b=_opt_int(boot.get("b")),
            seed=_opt_int(boot.get("seed")),
            block_universe=_opt_str(boot.get("block_universe")),
        ),
    )


def _stage(
    records: Sequence[CheckpointRecord], n_counted: int, start: datetime.date | None
) -> StageDetail:
    """Stage from the RECORDED decisions (D18) via ``stage_from``. A record decided under another
    prospective start date is not used and leaves the stage ``checkpoint_pending`` (CX-05); a 600
    record is only meaningful on top of a 300 ``continue``."""
    applied = {int(r.checkpoint): r.decision for r in records if r.prospective_start_date == start}
    if applied.get(ar.CHECKPOINTS[0]) != "continue":
        applied.pop(ar.CHECKPOINTS[1], None)
    stage, checkpoint, pending = ar.stage_from(applied, n_counted)
    return StageDetail(
        stage=stage,
        checkpoint=checkpoint,
        checkpoint_pending=bool(pending or len(applied) < len(records)),
    )


def _freshness_band(f: ar.PickFacts) -> str:
    lead = (f.post_time - f.odds_observed_at).total_seconds()
    return next(name for name, edge in _FRESHNESS_BANDS if lead <= edge)


def _by_judged_freshness(counted: Sequence[ar.PickFacts]) -> AttentionJudgedFreshness:
    groups: dict[str, list[ar.PickFacts]] = {name: [] for name, _ in _FRESHNESS_BANDS}
    for f in counted:
        groups[_freshness_band(f)].append(f)

    def roi(rows: list[ar.PickFacts], payout_of) -> float | None:
        return sum(payout_of(f) for f in rows) / (100.0 * len(rows)) if rows else None

    def band(rows: list[ar.PickFacts]) -> AttentionFreshnessBand:
        return AttentionFreshnessBand(
            n=len(rows),
            hits=sum(1 for f in rows if f.won),
            roi_official=roi(rows, ar.official_payout),
            roi_frozen=roi(rows, ar.frozen_payout),
        )

    return AttentionJudgedFreshness(
        le_10m=band(groups["<=10m"]), le_60m=band(groups["<=60m"]), gt_60m=band(groups[">60m"])
    )


def _odds_drift(counted: Sequence[ar.PickFacts]) -> AttentionOddsDrift:
    logs = [
        math.log(f.stored_odds / f.odds_used)
        for f in counted
        if _has_stored_odds(f) and f.odds_used > 0
    ]
    if not logs:
        return AttentionOddsDrift(n=0, median_log_ratio=None, p10=None, p90=None)
    arr = np.asarray(logs, dtype=float)
    return AttentionOddsDrift(
        n=len(logs),
        median_log_ratio=float(np.median(arr)),
        p10=float(np.percentile(arr, 10)),
        p90=float(np.percentile(arr, 90)),
    )


def _prospective_bootstrap() -> AttentionProspectiveBootstrap:
    boot = ar.BOOTSTRAP
    return AttentionProspectiveBootstrap(
        impl=str(boot["impl"]),
        b=int(boot["b"]),
        seed=int(boot["seed"]),
        block=str(boot["block"]),
        block_universe=str(boot["block_universe"]),
        rng=str(boot["rng"]),
        numpy_version=np.__version__,
    )


def build_rule_tally(
    rule_id: str,
    rows: Iterable[TallyRow],
    started_by_race: Mapping[str, Iterable[str]],
    records: Iterable[CheckpointRecord],
) -> RuleTally:
    """Classify every pick of the rule (exclusive, ``classify_pick``), settle the counted ones on
    every basis (official payout = the stage basis; judged odds and stored odds = references) and
    take the stage from the recorded decisions.

    ``rows`` = the rule's ``kind='pick'`` rows (all dates, voided ones included); ``records`` = the
    recorded checkpoint decisions (current policy and rule set; other rules are ignored).
    By construction ``n_counted + Σcounts == n_picks_total``; the field-change flag is a separate
    non-exclusive count."""
    start = ar.PROSPECTIVE_START_DATE  # read once: the classification and the stage agree
    digests = {race_id: ar.field_digest(ids) for race_id, ids in started_by_race.items()}
    empty_field = ar.field_digest(())
    counts = dict.fromkeys(ar.EXCLUSION_ORDER, 0)
    counted: list[ar.PickFacts] = []
    flagged = 0
    n_total = 0
    for row in rows:
        if row.rule_id != rule_id:
            continue
        n_total += 1
        facts = pick_facts(row)
        klass = ar.classify_pick(facts, start_date=start)
        if klass == "counted":
            counted.append(facts)
        else:
            counts[klass] += 1
        if not facts.voided and row.field_digest != digests.get(row.race_id, empty_field):
            flagged += 1

    own_records = sorted(
        (r for r in records if r.rule_id == rule_id), key=lambda r: int(r.checkpoint)
    )
    stage = _stage(own_records, len(counted), start)
    official = ar.ratio_ci(counted, ar.official_payout)
    frozen = ar.ratio_ci(counted, ar.frozen_payout)
    # the reference basis settles only the counted picks that still have stored odds (numerator and
    # denominator alike); the rest are surfaced as a count, never valued at the judged odds
    with_stored = [f for f in counted if _has_stored_odds(f)]
    stored = ar.ratio_ci(with_stored, _stored_payout)
    nxt = ar.next_checkpoint(stage.stage, stage.checkpoint)
    prospective = AttentionProspective(
        start_date=start,
        policy_version=ar.SELECTION_POLICY_VERSION,
        stage=stage.stage,
        checkpoint=stage.checkpoint,
        checkpoint_pending=stage.checkpoint_pending,
        decisions=[_decision_view(r) for r in own_records],
        next_checkpoint=nxt,
        remaining_to_next=None if nxt is None else max(0, nxt - len(counted)),
        n_counted=len(counted),
        n_hits=sum(1 for f in counted if f.won),
        n_picks_total=n_total,
        official=AttentionOfficialBasis(
            valuation_basis="official_win_payout",
            roi=official["roi"],
            ci=_ci(official["ci_low"], official["ci_high"]),
            p_one_sided=official["p_one_sided"],
        ),
        frozen=AttentionFrozenBasis(
            valuation_basis="frozen_pick_odds",
            roi=frozen["roi"],
            ci=_ci(frozen["ci_low"], frozen["ci_high"]),
            p_one_sided=frozen["p_one_sided"],
        ),
        stored=AttentionStoredBasis(
            valuation_basis="stored_odds_mutable",
            roi=stored["roi"],
            ci=_ci(stored["ci_low"], stored["ci_high"]),
            n=len(with_stored),
            n_missing_stored_odds=len(counted) - len(with_stored),
        ),
        bootstrap=_prospective_bootstrap(),
        counts=AttentionExclusionCounts(**counts),
        flags=AttentionFlags(field_changed_after_pick=flagged),
        by_judged_freshness=_by_judged_freshness(counted),
        odds_drift=_odds_drift(counted),
    )
    return RuleTally(
        rule_id=rule_id, stage=stage, point_roi_official=official["roi"], prospective=prospective
    )


def build_rule_tallies(
    rule_ids: Iterable[str],
    rows: Sequence[TallyRow],
    started_by_race: Mapping[str, Iterable[str]],
    records: Sequence[CheckpointRecord],
) -> dict[str, RuleTally]:
    return {
        rule_id: build_rule_tally(rule_id, rows, started_by_race, records) for rule_id in rule_ids
    }


class TallyMemo:
    """Per-rule memo of ``RuleTally`` keyed by ``queries.attention_memo_key`` (plan D7/D22).

    One entry per rule (bounded); a rule is recomputed only when its key changed (a new pick or
    void, a new checkpoint record, a result, odds or official payout re-ingest of a race holding
    its picks, or a different selection policy / PROSPECTIVE_START_DATE). Thread-safe: request
    handlers run in a thread pool."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[tuple, RuleTally]] = {}

    def get(
        self,
        keys: Mapping[str, tuple],
        load: Callable[[list[str]], Mapping[str, RuleTally]],
    ) -> dict[str, RuleTally]:
        with self._lock:
            stale = [
                rule_id
                for rule_id in ar.RULE_IDS
                if rule_id not in self._entries or self._entries[rule_id][0] != keys[rule_id]
            ]
            if stale:
                fresh = load(stale)
                for rule_id in stale:
                    self._entries[rule_id] = (keys[rule_id], fresh[rule_id])
            return {rule_id: self._entries[rule_id][1] for rule_id in ar.RULE_IDS}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


#: the API process's memo (one per process; bounded to one entry per rule)
TALLY_MEMO = TallyMemo()


# --- /attention-rules -----------------------------------------------------------------------------


def _frozen_stats(s: ar.FrozenStats) -> AttentionFrozenStats:
    return AttentionFrozenStats(
        roi=s.roi, ci_low=s.ci_low, ci_high=s.ci_high, p_one_sided=s.p_one_sided, n=s.n,
        hits=s.hits,
    )


def _selected(s: ar.SelectedCalibration) -> AttentionSelectedCalibration:
    return AttentionSelectedCalibration(n=s.n, mean_ev=s.mean_ev, realized_roi=s.realized_roi)


def _backtest_bootstrap() -> AttentionBacktestBootstrap:
    """Provenance of the FROZEN backtest CIs/p (plan 0.5) — the registry's ``FROZEN_BOOTSTRAP``
    (production refreeze T006; never restated here and never borrowed from the prospective
    ``ar.BOOTSTRAP``, which names a different block universe)."""
    frozen = ar.FROZEN_BOOTSTRAP
    return AttentionBacktestBootstrap(
        impl=str(frozen["impl"]),
        b=int(frozen["b"]),
        seed=int(frozen["seed"]),
        block=str(frozen["block"]),
        block_universe=str(frozen["block_universe"]),
    )


def _buy_time_expectation(rule_id: str) -> AttentionBuyTimeExpectation | None:
    """The registry's buy-time expectation of the rule with its source and version (139 D13) —
    copied, never recomputed here. None until the registry marks it independently verified (D6):
    an unverified conversion is never served."""
    served = ar.buy_time_expectation(rule_id)
    if served is None:
        return None
    return AttentionBuyTimeExpectation(
        range_low=served.range_low,
        range_high=served.range_high,
        ci_low=served.ci_low,
        ci_high=served.ci_high,
        interval_includes_100=served.interval_includes_one,
        included_in=served.included_in,
        source=AttentionBuyTimeSource(**ar.BUY_TIME_EXPECTATION_SOURCE),
    )


def rule_summary(rule: ar.AttentionRule, tally: RuleTally) -> RuleSummary:
    d = rule.definition
    return RuleSummary(
        id=d.id,
        rank=d.rank,
        definition_ja=d.definition_ja,
        uses_ensemble=d.uses_ensemble,
        ev_gt=d.ev_gt,
        odds_band=d.odds_band,
        gap_days=d.gap_days,
        posthoc=d.posthoc,
        control=d.control,
        backtest=AttentionBacktest(
            all=_frozen_stats(rule.backtest_all),
            c=_frozen_stats(rule.backtest_c),
            bets_2024_25_26=list(rule.bets_2024_25_26),
            selected=AttentionSelectedWindows(
                all=_selected(rule.selected_all), c=_selected(rule.selected_c)
            ),
            valuation_basis="closing_odds_approx",
            bootstrap=_backtest_bootstrap(),
        ),
        buy_time_expectation=_buy_time_expectation(d.id),
        price_noise=[
            AttentionPriceNoise(sigma=n.sigma, roi=n.roi, n=n.n, overlap=n.overlap)
            for n in rule.price_noise
        ],
        levels=AttentionRuleLevels(
            backtest=ar.backtest_level(rule), price_noise=ar.price_noise_level(rule)
        ),
        prospective=tally.prospective,
    )


def build_rules_response(tallies: Mapping[str, RuleTally]) -> AttentionRulesResponse:
    """All rules in rank order (fixed — never sorted by results), failed/undecided ones included."""
    rules = sorted(ar.RULES, key=lambda r: r.definition.rank)
    return AttentionRulesResponse(
        rule_set_version=ar.RULE_SET_VERSION,
        items=[rule_summary(r, tallies[r.id]) for r in rules],
        disclaimer=DISCLAIMER,
    )


# --- /races/{race_id}/attention -----------------------------------------------------------------


def _levels(chip: str, tallies: Mapping[str, RuleTally]) -> AttentionLevels:
    rule = ar.rule(chip)
    tally = tallies[chip]
    return AttentionLevels(
        backtest=ar.backtest_level(rule),
        prospective=ar.prospective_level(tally.stage.stage, tally.point_roi_official),
        price_noise=ar.price_noise_level(rule),
    )


def _judged(pick: StoredPick) -> EvSnapshot:
    return EvSnapshot(
        ens_expected_return=_f(pick.ens_expected_return),
        single_expected_return=_f(pick.single_expected_return),
        odds=_f(pick.odds_used),
        odds_observed_at=pick.odds_observed_at,
        computed_at=pick.computed_at,
        run_id=pick.run_id,
        is_pseudo=True,
    )


def _current(ens_row, single_row) -> EvSnapshot:
    same_run = single_row is not None and single_row.run_id == ens_row.run_id
    return EvSnapshot(
        ens_expected_return=float(ens_row.expected_return),
        single_expected_return=float(single_row.expected_return) if same_run else None,
        odds=float(ens_row.odds_used),
        odds_observed_at=ens_row.odds_observed_at,
        computed_at=ens_row.computed_at,
        run_id=ens_row.run_id,
        is_pseudo=True,
    )


def _horse_order(number: int | None, horse_id: str) -> tuple[bool, int, str]:
    return (number is None, int(number) if number is not None else 0, horse_id)


def unavailable_attention(race_id: str, entries: Sequence[Entry]) -> AttentionUnavailable:
    """A race without a scan row (no first computation): ``odds_unavailable`` when a started horse
    lacks valid win odds now (the job skips such a race), otherwise ``not_computed``. Needs only
    the entries, so the router answers it before reading picks, market-ev rows or the tallies."""
    started_odds = [e.odds for e in entries if e.entry_status == EntryStatus.STARTED]
    missing = any(o is None or float(o) < 1.0 for o in started_odds)
    return AttentionUnavailable(
        status="unavailable",
        race_id=race_id,
        reason="odds_unavailable" if started_odds and missing else "not_computed",
    )


def build_attention(
    *,
    race_id: str,
    post_time: datetime.datetime | None,
    has_results: bool,
    scan: StoredScan | None,
    picks: Sequence[StoredPick],
    entries: Sequence[Entry],
    ens_rows: Sequence,
    single_rows: Sequence,
    tallies: Mapping[str, RuleTally],
) -> AttentionAvailable | AttentionUnavailable:
    """Shape one race. ``picks`` = pick and void rows of the race (current rule set); ``entries`` =
    every race_horses row now; ``ens_rows``/``single_rows`` = the latest stored market-ev rows of
    the displayed / single-seed versions; ``tallies`` = all five rules' ``RuleTally``.

    Without a scan row the answer is ``unavailable_attention`` (the race endpoint short-circuits
    to it before loading the rest)."""
    if scan is None:
        return unavailable_attention(race_id, entries)
    started_odds = {e.horse_id: e.odds for e in entries if e.entry_status == EntryStatus.STARTED}

    market = build_market_ev(race_id, ens_rows, started_odds)
    ens_now = (
        {row.horse_id: row for row in ens_rows} if isinstance(market, MarketEvAvailable) else {}
    )
    single_now = {row.horse_id: row for row in single_rows}
    field_now = ar.field_digest(started_odds)
    voided = {p.voids_pick_id for p in picks if p.kind == "void"}
    by_horse: dict[str, dict[str, StoredPick]] = {}
    for p in picks:
        if p.kind == "pick":
            by_horse.setdefault(p.horse_id, {})[p.rule_id] = p
    numbers = {e.horse_id: e.horse_number for e in entries}
    for hid, horse_picks in by_horse.items():
        if numbers.get(hid) is None:
            numbers[hid] = next(iter(horse_picks.values())).horse_number
    stage_of = {rule_id: tallies[rule_id].stage for rule_id in ar.RULE_IDS}

    horse_ids = sorted(
        set(started_odds) | set(by_horse), key=lambda h: _horse_order(numbers.get(h), h)
    )
    horses: list[AttentionHorse] = []
    for hid in horse_ids:
        own = by_horse.get(hid, {})
        pick_status = {
            rule_id: (
                "none" if rule_id not in own
                else "void:scratched" if own[rule_id].pick_id in voided
                else "pick"
            )
            for rule_id in ar.RULE_IDS
        }
        applicable = [rule_id for rule_id in ar.RULE_IDS if pick_status[rule_id] == "pick"]
        chip, chip_s2, _ = ar.chip_rule(
            applicable, {rule_id: stage_of[rule_id].stage for rule_id in applicable}
        )
        source = own[chip] if chip is not None else (own[applicable[0]] if applicable else None)
        ens_row = ens_now.get(hid)
        current = _current(ens_row, single_now.get(hid)) if ens_row is not None else None
        chip_now = None
        if chip is not None:
            chip_now = "unknown" if current is None else ar.chip_now(
                ar.definition(chip),
                current_ens_ev=current.ens_expected_return,
                current_single_ev=current.single_expected_return,
                current_odds=current.odds,
                days_since_last=_f(source.days_since_last),
            )
        horses.append(
            AttentionHorse(
                horse_id=hid,
                horse_number=numbers.get(hid),
                applicable=applicable,
                chip_rule=chip,
                chip_stage=stage_of[chip] if chip is not None else None,
                chip_s2=chip_s2,
                chip_now=chip_now,
                judged=_judged(source) if source is not None else None,
                current=current,
                field_changed_after_pick=any(p.field_digest != field_now for p in own.values()),
                levels=_levels(chip, tallies) if chip is not None else None,
                stages={rule_id: stage_of[rule_id] for rule_id in applicable},
                pick_status=pick_status,
            )
        )
    return AttentionAvailable(
        status="available",
        race_id=race_id,
        post_time=post_time,
        has_results=has_results,
        judged_at=scan.computed_at,
        selection_policy_version=ar.SELECTION_POLICY_VERSION,
        rule_set_version=ar.RULE_SET_VERSION,
        horses=horses,
    )


# --- /attention/day -----------------------------------------------------------------------------


class RaceInfo(Protocol):
    race_id: str
    post_time: datetime.datetime | None
    venue_code: str | None
    race_number: int | None


def build_day_items(
    *,
    date: datetime.date,
    races: Mapping[str, RaceInfo],
    responses: Mapping[str, AttentionAvailable | AttentionUnavailable],
    horse_names: Mapping[tuple[str, str], str | None],
    non_starters: frozenset[tuple[str, str]] = frozenset(),
) -> AttentionDayResponse:
    """Chip horses (S1-S4, live picks) of the day's races, post order only (unknown post time
    last, then race_id, then horse_number). Never sorted by level, ROI or EV.

    A horse that is currently 出走取消 / 競走除外 (``non_starters``: (race_id, horse_id)) is left
    out even before the next computation appends its void — a scratched horse gets no chip."""
    items: list[AttentionDayItem] = []
    for race_id, resp in responses.items():
        if not isinstance(resp, AttentionAvailable):
            continue
        race = races[race_id]
        for h in resp.horses:
            if h.chip_rule is None or (race_id, h.horse_id) in non_starters:
                continue
            items.append(
                AttentionDayItem(
                    race_id=race_id,
                    post_time=race.post_time,
                    has_results=resp.has_results,
                    venue_code=race.venue_code,
                    race_number=race.race_number,
                    horse_id=h.horse_id,
                    horse_number=h.horse_number,
                    horse_name=horse_names.get((race_id, h.horse_id)),
                    chip_rule=h.chip_rule,
                    chip_stage=h.chip_stage,
                    chip_s2=h.chip_s2,
                    chip_now=h.chip_now,
                    stages=h.stages,
                    levels=h.levels,
                    current_odds_observed_at=(
                        h.current.odds_observed_at if h.current is not None else None
                    ),
                )
            )
    items.sort(
        key=lambda i: (
            i.post_time is None,
            i.post_time or _NO_TIME,
            i.race_id,
            *_horse_order(i.horse_number, i.horse_id),
        )
    )
    return AttentionDayResponse(date=date, items=items)
