"""Lazy manual-observation adapters. Constructing the disabled adapter performs no I/O.

The opt-in production path is implemented for a future explicitly enabled manual command.
Tests use fake engines/fetchers; this module never opens a database or network at import time.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGES = ("before", "after", "decision")
SQL = {
    "race": "SELECT race_id, race_date, post_time FROM races WHERE race_id = :race_id",
    "entries": (
        "SELECT horse_id, horse_number, entry_status FROM race_horses "
        "WHERE race_id = :race_id ORDER BY horse_number, horse_id"
    ),
    "results": "SELECT race_id FROM race_results WHERE race_id = :race_id",
}


class ObservationDisabled(RuntimeError):
    pass


def _source_paths():
    for package in ("db", "scrape", "live"):
        path = str(ROOT / package / "src")
        if path not in sys.path:
            sys.path.insert(0, path)


def _race_id(value):
    if not isinstance(value, str) or re.fullmatch(r"\d{12}", value) is None:
        raise ValueError("invalid_race_id")
    return value


def _stamp(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",
            value,
        )
        is None
    ):
        raise ValueError("observation_clock_timezone_or_precision_invalid")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observation_clock_naive")
    return parsed.astimezone(dt.UTC)


def _encode(value):
    if isinstance(value, dt.datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("database_timestamp_naive")
        return value.astimezone(dt.UTC).isoformat(timespec="microseconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if value is None or type(value) in (str, int, bool, float):
        return value
    raise ValueError("unsupported_database_value")


class ObservationAdapters:
    """Production factories are lazy and only reachable after explicit enabled=True.

    Enabling this object is not done by any rehearsal entry point. Each guard obtains a new
    transaction and snapshot; an already-open Session/Connection cannot be injected.
    """

    def __init__(self, *, enabled=False, engine_factory=None, fetcher_factory=None):
        if type(enabled) is not bool:
            raise ValueError("enabled_must_be_boolean")
        self._enabled = enabled
        self._engine_factory = engine_factory
        self._fetcher_factory = fetcher_factory
        self._owned_engine = None
        self._fetcher = None

    def _require_enabled(self):
        if self._enabled is not True:
            raise ObservationDisabled("new_collection_disabled")

    def engine(self):
        """Create only after opt-in; the feature observer may reuse this ENGINE, not a snapshot."""
        self._require_enabled()
        if self._owned_engine is None:
            if self._engine_factory is None:
                _source_paths()
                from horseracing_db.session import create_db_engine

                self._owned_engine = create_db_engine()
            else:
                self._owned_engine = self._engine_factory()
        return self._owned_engine

    def _polite_fetcher(self):
        self._require_enabled()
        if self._fetcher is None:
            if self._fetcher_factory is None:
                _source_paths()
                from horseracing_live.chaos_politeness import make_capture_fetcher

                # Reuse robots, per-origin reservations and refusal/cooldown protection.
                # These operational throttle receipts may write their own short-lived state;
                # the race/feature observation transactions themselves stay read-only.
                self._fetcher = make_capture_fetcher(
                    engine=self.engine(), deadline_s=10.0
                )
            else:
                self._fetcher = self._fetcher_factory()
        return self._fetcher

    def observe_guard(self, stage, race_id, clock):
        """Return all query bytes/values without inferring pending or repairing entry IDs."""
        self._require_enabled()
        if stage not in STAGES:
            raise ValueError("invalid_guard_stage")
        rid = _race_id(race_id)
        # This lower bound precedes connection checkout and transaction creation. Using it for
        # every query lets the five-second gate reject an old or slow snapshot as a whole.
        started = clock.now()
        _stamp(started)
        from sqlalchemy import text

        observations = {}
        with self.engine().connect() as base:
            conn = base.execution_options(isolation_level="REPEATABLE READ")
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                for kind, statement in SQL.items():
                    rows = (
                        conn.execute(text(statement), {"race_id": rid}).mappings().all()
                    )
                    completed = clock.now()
                    if _stamp(completed) < _stamp(started):
                        raise ValueError("observation_clock_reversed")
                    observations[kind] = dict(
                        started_at=started,
                        completed_at=completed,
                        rows=[
                            {key: _encode(value) for key, value in dict(row).items()}
                            for row in rows
                        ],
                    )
        return observations

    def fetch_quote(self, race_id, clock):
        self._require_enabled()
        rid = _race_id(race_id)
        url = (
            "https://race.netkeiba.com/api/api_get_jra_odds.html"
            f"?race_id={rid}&type=1&action=update"
        )
        started = clock.now()
        _stamp(started)
        raw = self._polite_fetcher().get(url, use_cache=False)
        received = clock.now()
        if _stamp(received) < _stamp(started):
            raise ValueError("observation_clock_reversed")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("quote_response_empty_or_nontext")
        return dict(
            raw_text=raw, source_url=url, fetch_started_at=started, received_at=received
        )

    def close(self):
        if self._fetcher is not None and callable(
            getattr(self._fetcher, "close", None)
        ):
            self._fetcher.close()
        if self._owned_engine is not None and callable(
            getattr(self._owned_engine, "dispose", None)
        ):
            self._owned_engine.dispose()
        self._fetcher, self._owned_engine = None, None
