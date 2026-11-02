"""Parsed value objects of the JRA official-site route (Feature 176, data-model §4).

Nothing here is persisted. ``JraPageRef`` carries the dynamic ``cname`` for the duration of one run
only (FR-001): it is excluded from ``repr`` and cannot be pickled, so it does not leak into logs,
archives or caches.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

RaceBlockState = Literal["published", "not_yet", "cancelled", "parse_failed"]
PayoutBlockState = Literal["not_published", "complete", "not_sold", "refund_only", "parse_failed"]
ChangeKind = Literal["cancel", "exclude", "post_time", "jockey"]
OddsKind = Literal["current", "prev_day_final"]
TimeBasis = Literal["measured", "fetched"]

#: Page kinds. Archive / collection permission is keyed on these (contracts/fetch-and-parse.md §1).
KIND_INDEX_D = "index_d"
KIND_INDEX_O = "index_o"
KIND_INDEX_S = "index_s"
KIND_INDEX_H = "index_h"
KIND_DAYLIST = "daylist"
KIND_ENTRIES = "entries"
KIND_CHANGES = "changes"
KIND_ODDS_WIN = "odds_win"
KIND_ODDS_EXOTIC = "odds_exotic"
KIND_RESULTS = "results"
KIND_PAYOUTS = "payouts"
KIND_BABA_FRAGMENT = "baba_fragment"
KIND_ACCESS_J = "accessJ"
KIND_ROBOTS = "robots"

#: Kinds whose raw bytes may be retained (archive AND the jra-fetch collection step). Win/exotic
#: odds pages are deliberately absent: a timestamped raw copy of a quote page is an odds time
#: series through the back door (constitution V).
ARCHIVABLE_KINDS: frozenset[str] = frozenset(
    {
        KIND_INDEX_D,
        KIND_INDEX_O,
        KIND_INDEX_S,
        KIND_INDEX_H,
        KIND_DAYLIST,
        KIND_ENTRIES,
        KIND_CHANGES,
        KIND_RESULTS,
        KIND_PAYOUTS,
        KIND_BABA_FRAGMENT,
        KIND_ACCESS_J,
    }
)


def archivable(kind: str) -> bool:
    return kind in ARCHIVABLE_KINDS


class JraPageRef:
    """A navigable link: URL + dynamic cname. Run-scoped; never persisted (FR-001)."""

    __slots__ = ("url", "cname", "label", "kind")

    def __init__(self, url: str, cname: str, *, label: str = "", kind: str = "") -> None:
        self.url = url
        self.cname = cname
        self.label = label
        self.kind = kind

    def __repr__(self) -> str:  # cname deliberately omitted
        return f"JraPageRef(url={self.url!r}, label={self.label!r}, kind={self.kind!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, JraPageRef):
            return NotImplemented
        return (self.url, self.cname, self.label, self.kind) == (
            other.url,
            other.cname,
            other.label,
            other.kind,
        )

    def __hash__(self) -> int:
        return hash((self.url, self.cname))

    def __reduce__(self):
        raise TypeError("JraPageRef must not be pickled or persisted (FR-001)")


@dataclass(frozen=True)
class JraResponse:
    """One decoded response. ``raw`` is the exact bytes; ``raw_sha256`` is the
    fixture/evidence key."""

    url: str
    cname_sha256: str | None
    status: int
    raw: bytes = field(repr=False)
    text: str = field(repr=False)
    fetched_at: datetime.datetime
    kind: str
    raw_sha256: str


@dataclass(frozen=True)
class JraRaceHeader:
    race_id: str
    race_date: datetime.date
    venue_code: str
    kai: int
    nichime: int
    race_no: int
    race_name: str | None = None
    post_time: datetime.datetime | None = None
    distance: int | None = None
    track_type: str | None = None
    weather: str | None = None
    going: str | None = None
    race_class: str | None = None
    grade: str | None = None
    prize_money: int | None = None


@dataclass(frozen=True)
class JraRaceBlock[T]:
    """One race on a day page, judged independently (FR-008)."""

    race_no: int
    race_id: str | None
    state: RaceBlockState
    payload: T | None = None
    error: str | None = None


@dataclass(frozen=True)
class JraDayPage[T]:
    race_date: datetime.date
    venue_code: str
    kai: int
    nichime: int
    races: tuple[JraRaceBlock[T], ...]

    def state_counts(self) -> dict[str, int]:
        counts = {
            "races_published": 0,
            "races_not_yet": 0,
            "races_cancelled": 0,
            "races_parse_failed": 0,
        }
        for r in self.races:
            counts[f"races_{r.state}"] += 1
        return counts

    def published(self) -> tuple[JraRaceBlock[T], ...]:
        return tuple(r for r in self.races if r.state == "published")


@dataclass(frozen=True)
class JraEntryHorse:
    horse_id: str | None
    horse_name: str | None
    frame: int | None
    horse_number: int | None  # None when the cell reads 取消 (number lost — FR-011 edge case)
    jockey_id: str | None
    jockey_name: str | None
    trainer_id: str | None
    trainer_name: str | None
    weight: int | None
    weight_diff: int | None
    jockey_weight: float | None
    sex: str | None
    age: int | None
    entry_status: str
    unknown_id_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class JraEntry:
    race: JraRaceHeader
    horses: tuple[JraEntryHorse, ...]


@dataclass(frozen=True)
class JraChange:
    race_id: str
    kind: ChangeKind
    horse_number: int | None = None
    horse_name: str | None = None
    new_post_time: datetime.datetime | None = None
    old_post_time: datetime.datetime | None = None
    new_jockey_name: str | None = None
    old_jockey_name: str | None = None


@dataclass(frozen=True)
class JraWinOddsRow:
    horse_number: int | None
    horse_id: str | None
    horse_name: str | None
    win_odds: float | None
    place_low: float | None
    place_high: float | None
    weight: int | None
    weight_diff: int | None
    jockey_weight: float | None
    entry_status: str


@dataclass(frozen=True)
class JraWinOdds:
    race_id: str
    kind: OddsKind
    official_at: datetime.datetime | None
    rows: tuple[JraWinOddsRow, ...]


@dataclass(frozen=True)
class JraResultRow:
    horse_id: str | None
    horse_name: str | None
    horse_number: int | None
    frame: int | None
    finish_order: int | None
    result_status: str
    finish_time: str | None
    margin: str | None
    corner_orders: tuple[str, ...] | None
    last_3f: float | None
    weight: int | None
    weight_diff: int | None
    jockey_id: str | None
    jockey_name: str | None
    trainer_id: str | None
    trainer_name: str | None
    jockey_weight: float | None
    sex: str | None
    age: int | None
    #: 単勝人気 — audit material only; never written to race_horses.popularity (user decision
    #: 2026-11-02, research D9).
    popularity: int | None
    unknown_id_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class JraPayoutLine:
    numbers: tuple[int, ...]
    payout_yen: int  # per 100 yen, as printed
    popularity: int | None


@dataclass(frozen=True)
class JraPayoutBlock:
    bet_type: str  # BetType value, or "bracket_quinella" for 枠連 (parsed, never stored)
    state: PayoutBlockState
    lines: tuple[JraPayoutLine, ...] = ()
    refunds: tuple[int, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class JraPayoutSet:
    by_bet_type: Mapping[str, JraPayoutBlock]

    def block(self, bet_type: str) -> JraPayoutBlock:
        return self.by_bet_type.get(bet_type, JraPayoutBlock(bet_type, "not_published"))


@dataclass(frozen=True)
class JraRaceResult:
    race: JraRaceHeader
    rows: tuple[JraResultRow, ...]
    lap_times: tuple[float, ...]
    pace_last_4f: float | None
    pace_last_3f: float | None
    corner_passing: Mapping[str, str]  # "3コーナー" -> raw passing-order string
    payouts: JraPayoutSet


@dataclass(frozen=True)
class JraTrackReading:
    venue_code: str
    measured_at: datetime.datetime
    time_basis: TimeBasis
    item: str
    value_num: float | None
    value_text: str | None


@dataclass(frozen=True)
class WalkReport:
    rewalks: int = 0
    stopped_for_capture_window: int = 0
    unsupported_date: int = 0
