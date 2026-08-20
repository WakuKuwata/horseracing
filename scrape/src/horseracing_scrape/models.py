"""Parsed (network-free) dataclasses for netkeiba pages."""

from __future__ import annotations

import datetime
from dataclasses import dataclass


class ParseError(ValueError):
    """Raised when a required element is missing (fail-close — never invent data)."""


class NotYetPublished(ParseError):
    """The page parsed fine but the source has not published this datum YET.

    Distinct from ParseError because the two demand opposite responses: a ParseError means the
    markup changed and someone must look at it, while this means the race has not run (no result
    table) or win odds are not on sale — normal operation on a pre-race fetch. Recording the
    second as a failure is not merely cosmetic: 380 such rows in 14 days buried the genuine
    failures in the operator's job history, which is the one screen built to spot them.

    It stays a ParseError subclass so any caller that has not been taught the distinction keeps
    the old fail-closed behaviour rather than silently swallowing a missing element.
    """


@dataclass(frozen=True)
class ScrapedRaceKey:
    year: int
    track_code: str
    kai: int
    nichime: int
    race_no: int


@dataclass(frozen=True)
class ScrapedRace:
    key: ScrapedRaceKey
    race_date: datetime.date | None
    distance: int | None
    track_type: str | None
    going: str | None
    weather: str | None
    race_class: str | None
    race_name: str | None = None
    grade: str | None = None                       # G1/G2/G3 (None if not graded)
    post_time: datetime.datetime | None = None      # 発走時刻 (JST-aware)
    #: 本賞金 1着分, in 万円 — the same unit and meaning as races.prize_money from JRA-VAN
    #: (e.g. 2024 未勝利 = 550). None when the page does not show it.
    prize_money: int | None = None


@dataclass(frozen=True)
class ScrapedEntryHorse:
    netkeiba_horse_id: str
    horse_name: str | None
    frame: int | None
    horse_number: int | None
    netkeiba_jockey_id: str | None
    jockey_name: str | None
    netkeiba_trainer_id: str | None
    trainer_name: str | None
    weight: int | None          # 馬体重 (body weight)
    weight_diff: int | None      # 馬体重増減 (signed; None when 計不/new)
    jockey_weight: float | None  # 斤量 (impost, kg)
    sex: str | None
    age: int | None
    entry_status: str


@dataclass(frozen=True)
class ScrapedEntry:
    race: ScrapedRace
    horses: tuple[ScrapedEntryHorse, ...]


@dataclass(frozen=True)
class ScrapedOddsRow:
    # netkeiba win-odds JSON is keyed by 馬番 (horse_number), not horse id (Feature 022 I1).
    # update_odds matches race_horses by (race_id, horse_number) — no id_mapping needed.
    horse_number: int
    odds: float | None
    popularity: int | None


@dataclass(frozen=True)
class ScrapedPlaceQuoteRow:
    """複勝 (place) market quote — netkeiba publishes a RANGE [low, high], never a point.

    Both ends are carried: the width is itself market information, and the arithmetic midpoint is
    not neutral in probability space (odds invert). A half-present range is invalid and is dropped
    to (None, None) by the parser, so a partial range never reaches the DB.
    """

    horse_number: int
    odds_low: float | None
    odds_high: float | None
    popularity: int | None


@dataclass(frozen=True)
class ScrapedOdds:
    key: ScrapedRaceKey
    rows: tuple[ScrapedOddsRow, ...]
    #: 複勝 quotes from the SAME payload (data.odds["2"]) — 0 extra requests. Empty when absent.
    place_rows: tuple[ScrapedPlaceQuoteRow, ...] = ()
    #: data.official_datetime — the time the SOURCE declares this quote effective (JST in the
    #: payload, stored tz-aware). Provenance of the single latest value, not an odds history.
    official_at: datetime.datetime | None = None


@dataclass(frozen=True)
class ScrapedExoticQuotes:
    """One race's PRE-RACE price grid for a single exotic bet type.

    ``quotes`` maps the canonical selection (int tuple; ordered types keep finishing order) to
    ``(odds_low, odds_high | None, popularity | None)``. Only ワイド publishes a real range; the
    point-priced types carry None as the high end rather than duplicating the low value.
    """

    race_id: str
    bet_type: str
    quotes: dict[tuple[int, ...], tuple[float, float | None, int | None]]
    official_at: datetime.datetime | None


@dataclass(frozen=True)
class ScrapedResultRow:
    netkeiba_horse_id: str
    finish_order: int | None
    result_status: str
    finish_time: str | None
    last_3f: float | None = None                    # 後3F / 上がり3ハロン
    corner_orders: tuple[str, ...] | None = None     # コーナー通過順 ("7-7-4-3" -> (7,7,4,3))


@dataclass(frozen=True)
class ScrapedResult:
    key: ScrapedRaceKey
    rows: tuple[ScrapedResultRow, ...]


@dataclass(frozen=True)
class ScrapedLaps:
    """Feature 034: race-level sectional lap profile (db.netkeiba ラップタイム). RESULT-derived
    (known only after the race) — never a current-race feature, only past races' as-of (leak II).

    lap_times = per-200m segment times (leader-based pace profile). pace_first_3f / pace_last_3f =
    the race's テン3F / 上がり3F split (netkeiba's "(36.0-35.5)")."""

    key: ScrapedRaceKey
    lap_times: tuple[float, ...]
    pace_first_3f: float | None = None
    pace_last_3f: float | None = None


@dataclass(frozen=True)
class ScrapedExoticRow:
    bet_type: str               # place/quinella/exacta/wide/trio/trifecta
    numbers: tuple[int, ...]    # horse_number combination (race-local 馬番, no id-mapping needed)
    odds: float | None


@dataclass(frozen=True)
class ScrapedExoticOdds:
    key: ScrapedRaceKey
    rows: tuple[ScrapedExoticRow, ...]


@dataclass(frozen=True)
class ScrapedRaceList:
    """All race_ids found on a day's race-list fragment (deduped, in page order)."""

    kaisai_date: str                     # YYYYMMDD as supplied to netkeiba
    race_ids: tuple[str, ...]            # 12-digit netkeiba/JRA-VAN race_ids


@dataclass(frozen=True)
class ScrapedHorseProfile:
    """Leak-safe identity/pedigree attributes from a db.netkeiba.com horse page.

    Performance statistics (career starts/wins/earnings/recent finishes) are intentionally NOT
    carried here — they must never reach model features (leak boundary, constitution II). Pedigree
    ids are netkeiba ids (resolved to canonical/surrogate at upsert via id_mappings)."""

    netkeiba_horse_id: str
    horse_name: str | None
    sex: str | None
    birth_year: int | None
    netkeiba_sire_id: str | None
    sire_name: str | None
    netkeiba_dam_id: str | None
    dam_name: str | None
    netkeiba_damsire_id: str | None
    damsire_name: str | None
