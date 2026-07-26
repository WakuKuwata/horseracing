"""Fresh pre-race market capture for the Feature 084 top-3 chaos readout.

The capture path deliberately bypasses the single-latest values in ``race_horses``.  It fetches
the volatile win-odds payload without cache, freezes the adapter response, then writes the snapshot
and its readout in one transaction.  Result presence is used only as a fail-closed timing guard.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol

from horseracing_db.enums import EntryStatus
from horseracing_db.models import ChaosReadout, ChaosSnapshot, Race, RaceHorse
from horseracing_eval.stage_discount import StageDiscount
from horseracing_probability.chaos_artifact import (
    ChaosArtifactUnavailableError,
    ChaosBandsArtifact,
    load_chaos_artifact,
)
from horseracing_probability.chaos_distribution import chaos_readout
from horseracing_probability.chaos_events import CHAOS_EVENTS_V1
from horseracing_probability.market_odds import market_implied_win_probs
from horseracing_scrape.fetch import PoliteFetcher
from horseracing_scrape.models import ScrapedOdds
from horseracing_scrape.odds_adapter import fetch_win_odds
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import guards

CaptureStrength = Literal["confirmatory", "weak", "unknown"]
CaptureStatus = Literal["captured", "skipped", "rejected"]
PendingCheck = Callable[[Session, str], tuple[bool, str] | bool]

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_APPROVED_MANIFEST = _REPO_ROOT / "config" / "chaos_bands_approved.json"
DEFAULT_ARTIFACT_DIR = _REPO_ROOT / "artifacts" / "chaos_bands"


class ChaosOddsFetcher(PoliteFetcher, Protocol):
    """A volatile-data fetcher whose source is declared by the adapter itself."""

    source: str


class NetkeibaOddsFetcher:
    """Source-bearing adapter around the repository's polite HTTP fetcher.

    ``source`` is a class invariant, not a constructor argument.  The CLI therefore has no path
    for an operator-supplied source claim to reach a persisted snapshot.
    """

    source = "netkeiba"

    def __init__(self, delegate: PoliteFetcher):
        self._delegate = delegate

    def get(self, url: str, *, use_cache: bool = True) -> str:
        return self._delegate.get(url, use_cache=use_cache)


@dataclass(frozen=True)
class FrozenEntry:
    """DB identity needed to bind fresh horse-number keyed odds to the canonical field."""

    horse_id: str
    horse_number: int


@dataclass(frozen=True)
class FreshChaosCapture:
    """One eligible, freshly fetched observation before persistence."""

    captured_at: datetime.datetime
    source: str
    seconds_to_post: int | None
    capture_strength: CaptureStrength
    field: list[dict]
    content_digest: str


@dataclass(frozen=True)
class DerivedChaosReadout:
    """Persistable values derived only from a frozen field and a verified artifact."""

    artifact_version: str
    artifact_digest: str
    band: str
    band_axis: str
    p_s_ge_20: float
    p_himo_are: float
    p_total_collapse: float
    raw_p_s_ge_20: float
    raw_p_himo_are: float
    raw_p_total_collapse: float
    expected_s: float
    structural_zeros: dict[str, str]


@dataclass(frozen=True)
class ChaosCaptureReport:
    """Typed per-race result used by both the CLI summary and tests."""

    race_id: str
    status: CaptureStatus
    reason: str
    capture_strength: CaptureStrength | None = None
    chaos_snapshot_id: object | None = None
    content_digest: str | None = None
    seconds_to_post: int | None = None

    @property
    def captured(self) -> bool:
        return self.status == "captured"


class ChaosCaptureRejected(ValueError):
    """An expected fail-closed capture rejection with a stable summary reason."""

    def __init__(self, reason: str, detail: str, *, status: CaptureStatus = "rejected"):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.status = status


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def decide_capture_strength(
    *,
    fresh_fetch: bool,
    pending_before: bool | None,
    pending_after: bool | None,
    post_time: datetime.datetime | None,
    captured_at: datetime.datetime,
) -> CaptureStrength:
    """Apply the preregistered CAP-1..7 strength table without inventing evidence."""

    if not fresh_fetch:
        return "unknown"
    if (
        pending_before is True
        and pending_after is True
        and post_time is not None
        and captured_at < post_time
    ):
        return "confirmatory"
    if post_time is None or sum(value is True for value in (pending_before, pending_after)) == 1:
        return "weak"
    return "unknown"


def _pending_ok(
    pending_check: PendingCheck,
    session: Session,
    race_id: str,
) -> tuple[bool, str]:
    result = pending_check(session, race_id)
    if isinstance(result, tuple):
        return bool(result[0]), str(result[1])
    return bool(result), "ok" if result else "race is not result-pending"


def _canonical_field_bytes(field: Sequence[dict]) -> bytes:
    return json.dumps(
        list(field),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _content_digest(field: Sequence[dict]) -> str:
    return hashlib.sha256(_canonical_field_bytes(field)).hexdigest()


def build_frozen_field(
    entries: Sequence[FrozenEntry],
    scraped: ScrapedOdds,
) -> list[dict]:
    """Bind fresh odds to started entries and apply the fit-identical eligibility predicate."""

    if not entries:
        raise ChaosCaptureRejected("no_started_horses", "canonical field has no started horses")
    if len(entries) < 4:
        raise ChaosCaptureRejected(
            "field_too_small",
            f"canonical field has {len(entries)} horses; at least four are required",
        )

    by_number = {}
    for row in scraped.rows:
        if row.horse_number in by_number:
            raise ChaosCaptureRejected(
                "partial_market_odds",
                f"fresh payload has duplicate horse_number={row.horse_number}",
            )
        by_number[row.horse_number] = row
    missing_numbers = [
        entry.horse_number for entry in entries if entry.horse_number not in by_number
    ]
    if missing_numbers:
        raise ChaosCaptureRejected(
            "partial_market_odds",
            f"fresh payload has no row for started horse number(s) {missing_numbers}",
        )

    ranks = [by_number[entry.horse_number].popularity for entry in entries]
    if any(
        rank is None or isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0
        for rank in ranks
    ) or len(set(ranks)) != len(ranks):
        raise ChaosCaptureRejected(
            "invalid_popularity_ranks",
            "started-horse popularity must be positive, present, and unique; gaps are allowed",
        )

    field: list[dict] = []
    for entry in sorted(entries, key=lambda item: item.horse_id):
        row = by_number[entry.horse_number]
        try:
            odds = float(row.odds)
        except (TypeError, ValueError):
            odds = math.nan
        if not math.isfinite(odds) or odds <= 0.0:
            raise ChaosCaptureRejected(
                "partial_market_odds",
                f"started horse {entry.horse_id} has no positive finite fresh odds",
            )
        field.append(
            {
                "horse_id": entry.horse_id,
                "horse_number": entry.horse_number,
                "popularity": int(row.popularity),
                "odds": str(Decimal(str(row.odds))),
            }
        )
    return field


def acquire_fresh_capture(
    session: Session,
    *,
    race_id: str,
    entries: Sequence[FrozenEntry],
    post_time: datetime.datetime | None,
    fetcher: ChaosOddsFetcher,
    clock: Callable[[], datetime.datetime] = _now,
    pending_check: PendingCheck = guards.is_result_pending,
    min_seconds_to_post: int = 0,
) -> FreshChaosCapture:
    """Fetch one no-cache observation with pending checks on both sides of the fetch."""

    if min_seconds_to_post < 0:
        raise ValueError("min_seconds_to_post must be non-negative")

    pending_before, detail = _pending_ok(pending_check, session, race_id)
    if not pending_before:
        raise ChaosCaptureRejected("result_settled", f"before fetch: {detail}")

    # fetch_win_odds is the existing volatile-data adapter and always calls
    # fetcher.get(..., use_cache=False).  No race_horses odds/popularity value is read here.
    scraped = fetch_win_odds(fetcher, race_id)
    captured_at = clock()
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ChaosCaptureRejected(
            "invalid_capture_time", "capture clock must return a timezone-aware datetime"
        )

    pending_after, detail = _pending_ok(pending_check, session, race_id)
    if not pending_after:
        raise ChaosCaptureRejected("result_settled", f"during fetch: {detail}")

    source = getattr(fetcher, "source", None)
    if not isinstance(source, str) or not source.strip():
        raise ChaosCaptureRejected(
            "source_unavailable", "fresh fetch adapter did not provide a non-empty source"
        )

    seconds_to_post: int | None = None
    if post_time is not None:
        if post_time.tzinfo is None or post_time.utcoffset() is None:
            raise ChaosCaptureRejected(
                "invalid_post_time", "race post_time must be timezone-aware when present"
            )
        if captured_at >= post_time:
            raise ChaosCaptureRejected(
                "post_time_elapsed", "fresh payload completed at or after post_time"
            )
        seconds_to_post = int((post_time - captured_at).total_seconds())
        if seconds_to_post < min_seconds_to_post:
            raise ChaosCaptureRejected(
                "min_seconds_to_post",
                f"only {seconds_to_post}s remain; minimum is {min_seconds_to_post}s",
                status="skipped",
            )

    field = build_frozen_field(entries, scraped)
    strength = decide_capture_strength(
        fresh_fetch=True,
        pending_before=pending_before,
        pending_after=pending_after,
        post_time=post_time,
        captured_at=captured_at,
    )
    return FreshChaosCapture(
        captured_at=captured_at,
        source=source.strip(),
        seconds_to_post=seconds_to_post,
        capture_strength=strength,
        field=field,
        content_digest=_content_digest(field),
    )


def derive_chaos_readout(
    field: Sequence[dict],
    artifact: ChaosBandsArtifact,
) -> DerivedChaosReadout:
    """Derive a readout exclusively from the frozen JSON field."""

    odds = {str(row["horse_id"]): float(row["odds"]) for row in field}
    ranks = {str(row["horse_id"]): int(row["popularity"]) for row in field}
    q = market_implied_win_probs(odds)
    raw, adjusted, band = chaos_readout(
        q,
        ranks,
        CHAOS_EVENTS_V1,
        stage_discount=StageDiscount(
            lambda2=artifact.lambda2,
            lambda3=artifact.lambda3,
        ),
        edges=artifact.quintile_edges,
    )
    return DerivedChaosReadout(
        artifact_version=artifact.version,
        artifact_digest=artifact.artifact_digest,
        band=band,
        band_axis=artifact.band_axis,
        p_s_ge_20=adjusted.event_mass["s_ge_20"],
        p_himo_are=adjusted.event_mass["himo_are"],
        p_total_collapse=adjusted.event_mass["total_collapse"],
        raw_p_s_ge_20=raw.event_mass["s_ge_20"],
        raw_p_himo_are=raw.event_mass["himo_are"],
        raw_p_total_collapse=raw.event_mass["total_collapse"],
        expected_s=adjusted.expected_s,
        structural_zeros=dict(adjusted.structural_zero),
    )


def _started_entries(session: Session, race_id: str) -> list[FrozenEntry]:
    rows = session.execute(
        select(RaceHorse.horse_id, RaceHorse.horse_number)
        .where(RaceHorse.race_id == race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
        .order_by(RaceHorse.horse_id)
    ).all()
    return [
        FrozenEntry(horse_id=str(horse_id), horse_number=int(horse_number))
        for horse_id, horse_number in rows
    ]


def _void_reason(old: ChaosSnapshot, new_field: Sequence[dict]) -> str:
    old_entries = {(str(row["horse_id"]), int(row["horse_number"])) for row in old.field}
    new_entries = {(str(row["horse_id"]), int(row["horse_number"])) for row in new_field}
    return "late_scratch" if old_entries != new_entries else "recaptured"


class _ResultSettledBeforeReadout(RuntimeError):
    pass


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def capture_chaos(
    session: Session,
    *,
    race_id: str,
    fetcher: ChaosOddsFetcher,
    artifact: ChaosBandsArtifact,
    min_seconds_to_post: int = 0,
    clock: Callable[[], datetime.datetime] = _now,
    pending_check: PendingCheck = guards.is_result_pending,
) -> ChaosCaptureReport:
    """Capture and persist one race, returning typed skips instead of partial writes."""

    valid, detail = guards.valid_race_id(race_id)
    if not valid:
        return ChaosCaptureReport(race_id, "rejected", "invalid_race_id")
    race = session.get(Race, race_id)
    if race is None:
        return ChaosCaptureReport(race_id, "rejected", "race_not_found")
    if race.race_date is None:
        return ChaosCaptureReport(race_id, "rejected", "race_date_unknown")

    complete, detail = guards.entries_complete(session, race_id)
    if not complete:
        return ChaosCaptureReport(race_id, "rejected", "entries_incomplete")
    entries = _started_entries(session, race_id)

    try:
        fresh = acquire_fresh_capture(
            session,
            race_id=race_id,
            entries=entries,
            post_time=race.post_time,
            fetcher=fetcher,
            clock=clock,
            pending_check=pending_check,
            min_seconds_to_post=min_seconds_to_post,
        )
        derived = derive_chaos_readout(fresh.field, artifact)
    except ChaosCaptureRejected as exc:
        return ChaosCaptureReport(race_id, exc.status, exc.reason)

    snapshot: ChaosSnapshot | None = None
    try:
        # A transaction-scoped advisory lock serializes first capture as well as recapture; the
        # partial unique index remains the final database invariant.
        with session.begin_nested():
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"chaos-capture:{race_id}"},
            )
            old = session.scalar(
                select(ChaosSnapshot)
                .where(ChaosSnapshot.race_id == race_id)
                .where(ChaosSnapshot.status == "active")
                .with_for_update()
            )
            if old is not None:
                old.status = "void"
                old.void_reason = _void_reason(old, fresh.field)
                # Make the partial-unique slot available before the replacement INSERT.
                # Both statements are still inside the same savepoint/outer transaction.
                session.flush()

            snapshot = ChaosSnapshot(
                race_id=race_id,
                captured_at=fresh.captured_at,
                source=fresh.source,
                seconds_to_post=fresh.seconds_to_post,
                capture_strength=fresh.capture_strength,
                field=fresh.field,
                n=len(fresh.field),
                content_digest=fresh.content_digest,
                status="active",
            )
            session.add(snapshot)
            session.flush()

            # CAP-8: this is deliberately after the snapshot INSERT and immediately before the
            # readout INSERT.  Raising rolls the savepoint back, including an old-row void.
            pending_now, _detail = _pending_ok(pending_check, session, race_id)
            if not pending_now:
                raise _ResultSettledBeforeReadout

            session.add(
                ChaosReadout(
                    chaos_snapshot_id=snapshot.chaos_snapshot_id,
                    artifact_version=derived.artifact_version,
                    artifact_digest=derived.artifact_digest,
                    band=derived.band,
                    band_axis=derived.band_axis,
                    p_s_ge_20=_decimal(derived.p_s_ge_20),
                    p_himo_are=_decimal(derived.p_himo_are),
                    p_total_collapse=_decimal(derived.p_total_collapse),
                    raw_p_s_ge_20=_decimal(derived.raw_p_s_ge_20),
                    raw_p_himo_are=_decimal(derived.raw_p_himo_are),
                    raw_p_total_collapse=_decimal(derived.raw_p_total_collapse),
                    expected_s=_decimal(derived.expected_s),
                    structural_zeros=derived.structural_zeros,
                    computed_at=fresh.captured_at,
                )
            )
            session.flush()
    except _ResultSettledBeforeReadout:
        return ChaosCaptureReport(race_id, "rejected", "result_settled")

    assert snapshot is not None
    return ChaosCaptureReport(
        race_id=race_id,
        status="captured",
        reason="ok",
        capture_strength=fresh.capture_strength,
        chaos_snapshot_id=snapshot.chaos_snapshot_id,
        content_digest=fresh.content_digest,
        seconds_to_post=fresh.seconds_to_post,
    )


def approved_digests_from_manifest(
    manifest_path: str | Path = DEFAULT_APPROVED_MANIFEST,
) -> tuple[str, ...]:
    """Read the committed approval manifest without interpreting artifact payloads."""

    path = Path(manifest_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["approved"]
        digests = tuple(str(row["digest"]) for row in rows)
    except (OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ChaosArtifactUnavailableError(
            f"invalid chaos approval manifest {path}: {exc}"
        ) from exc
    if not digests or any(len(digest) != 64 for digest in digests):
        raise ChaosArtifactUnavailableError(f"chaos approval manifest has no valid digest: {path}")
    return digests


def load_current_chaos_artifact(
    target_date: datetime.date,
    *,
    manifest_path: str | Path = DEFAULT_APPROVED_MANIFEST,
    artifact_dir: str | Path = DEFAULT_ARTIFACT_DIR,
    approved_digests: Collection[str] | None = None,
) -> ChaosBandsArtifact:
    """Load the current (last pinned) artifact through the shared fail-closed loader."""

    approved = (
        tuple(approved_digests)
        if approved_digests is not None
        else approved_digests_from_manifest(manifest_path)
    )
    if not approved:
        raise ChaosArtifactUnavailableError("approved_digests must not be empty")
    current_digest = approved[-1]
    return load_chaos_artifact(
        Path(artifact_dir) / f"{current_digest}.json",
        approved_digests=approved,
        target_date=target_date,
    )


__all__ = [
    "ChaosCaptureRejected",
    "ChaosCaptureReport",
    "ChaosOddsFetcher",
    "DerivedChaosReadout",
    "FreshChaosCapture",
    "FrozenEntry",
    "NetkeibaOddsFetcher",
    "acquire_fresh_capture",
    "approved_digests_from_manifest",
    "build_frozen_field",
    "capture_chaos",
    "decide_capture_strength",
    "derive_chaos_readout",
    "load_current_chaos_artifact",
]
