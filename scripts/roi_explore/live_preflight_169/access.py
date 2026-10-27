"""Read existing access facts only; never refresh robots, reserve a slot or authorize capture.

The odds path check is preliminary. The unchanged live fetcher must still check its actual
request URL and reserve a fresh source slot immediately before sending a request.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from urllib.robotparser import RobotFileParser

ORIGIN = "https://race.netkeiba.com"
ODDS_URL = ORIGIN + "/api/api_get_jra_odds.html"
USER_AGENT = "horseracing-live/0.1 (personal use; contact via repo)"
THROTTLE_KEY = "netkeiba.com"
TTL_SECONDS = 86400
MAX_BODY_BYTES = 512 * 1024
MAX_ENCODED_BYTES = 4 * ((MAX_BODY_BYTES + 2) // 3)
MAX_CACHE_FILE_BYTES = MAX_ENCODED_BYTES + 16384
MAX_WAIT_SECONDS = 3.0
DEADLINE_SECONDS = 10.0


def _at(value):
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    if (
        not isinstance(value, dt.datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("aware_timestamp_required")
    return value.astimezone(dt.UTC)


def _clock_pair(now, launch_at):
    now = _at(now)
    launch = None if launch_at is None else _at(launch_at)
    if launch is not None and launch < now:
        raise ValueError("launch_precedes_check")
    return now, launch


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_cache_key")
        result[key] = value
    return result


def _nonfinite(_value):
    raise ValueError("nonfinite_json")


def inspect_robots(directory: Path | None, now, launch_at=None):
    """Inspect an existing cache file without cache APIs, locks, mkdirs, requests or writes."""
    result = dict(
        kind="robots",
        origin=ORIGIN,
        checked_url_path="/api/api_get_jra_odds.html",
        user_agent=USER_AGENT,
        status="BLOCKED_NOW",
        current_ready=False,
        launch_ready=None,
        blocking_reasons=[],
        path=None,
        sha256=None,
        fetched_at=None,
        expires_at=None,
        age_seconds=None,
        allowed=None,
        current_fresh=False,
        ttl_seconds=TTL_SECONDS,
        launch_at=None,
        launch_recheck_required=True,
        actual_request_url_recheck_required=True,
        can_collect=False,
        cache_refreshed=False,
        slot_reserved=False,
    )
    try:
        now, launch = _clock_pair(now, launch_at)
    except (ValueError, TypeError, OverflowError):
        result.update(
            status="INVALID_TIME", blocking_reasons=["invalid_check_or_launch_time"]
        )
        return result
    result.update(
        checked_at=now.isoformat(), launch_at=launch.isoformat() if launch else None
    )
    if directory is None:
        result["blocking_reasons"] = ["robots_cache_disabled"]
        return result
    path = Path(directory) / (
        hashlib.sha256(ORIGIN.encode()).hexdigest()[:20] + ".json"
    )
    result["path"] = str(path)
    try:
        if path.is_symlink():
            raise ValueError("robots_cache_symlink_not_supported")
        with path.open("rb") as source:
            raw = source.read(MAX_CACHE_FILE_BYTES + 1)
        if len(raw) > MAX_CACHE_FILE_BYTES:
            raise ValueError("robots_cache_file_too_large")
        result["sha256"] = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
        if not isinstance(doc, dict) or set(doc) != {
            "schema_version",
            "origin",
            "outcome",
            "status",
            "fetched_at",
            "body",
        }:
            raise ValueError("robots_cache_schema_invalid")
        if (
            type(doc["schema_version"]) is not int
            or doc["schema_version"] != 1
            or doc["origin"] != ORIGIN
        ):
            raise ValueError("robots_cache_identity_invalid")
        if type(doc["status"]) is not int:
            raise ValueError("robots_cache_status_invalid")
        encoded = doc["body"]
        if not isinstance(encoded, str) or len(encoded) > MAX_ENCODED_BYTES:
            raise ValueError("robots_body_encoding_invalid_or_oversize")
        body = base64.b64decode(encoded, validate=True)
        if len(body) > MAX_BODY_BYTES:
            raise ValueError("robots_body_too_large")
        if doc["outcome"] == "rules" and doc["status"] == 200:
            parser = RobotFileParser()
            parser.parse(body.decode("utf-8", errors="replace").splitlines())
            allowed = parser.can_fetch(USER_AGENT, ODDS_URL)
        elif doc["outcome"] == "absent" and doc["status"] in (404, 410) and body == b"":
            allowed = True
        else:
            raise ValueError("robots_outcome_status_mismatch")
        fetched = _at(doc["fetched_at"])
        expiry = fetched + dt.timedelta(seconds=TTL_SECONDS)
    except FileNotFoundError:
        result["blocking_reasons"] = ["robots_cache_missing"]
        return result
    except OSError:
        result["blocking_reasons"] = ["robots_cache_unreadable"]
        return result
    except (ValueError, TypeError, UnicodeError, OverflowError):
        # Do not expose arbitrary exception messages or decoded source bodies.
        result["blocking_reasons"] = ["robots_cache_invalid"]
        return result
    age = (now - fetched).total_seconds()
    fresh = 0 <= age < TTL_SECONDS
    reasons = []
    if age < 0:
        reasons.append("robots_capture_from_future")
    elif not fresh:
        reasons.append("robots_cache_expired")
    if not allowed:
        reasons.append("robots_disallows_odds_path")
    launch_ready = None
    if launch is not None:
        launch_ready = fresh and allowed and fetched <= launch < expiry
        if not (fetched <= launch < expiry):
            reasons.append("robots_cache_not_valid_at_planned_launch")
    result.update(
        fetched_at=fetched.isoformat(),
        expires_at=expiry.isoformat(),
        age_seconds=age,
        allowed=allowed,
        current_fresh=fresh,
        current_ready=fresh and allowed,
        launch_ready=launch_ready,
        blocking_reasons=reasons,
        status="PASS_NOW_ONLY" if fresh and allowed else "BLOCKED_NOW",
    )
    if fresh and allowed and launch is not None and not launch_ready:
        result["status"] = "BLOCKED_AT_PLANNED_LAUNCH"
    return result


def throttle_check(rows, now, source_interval_seconds, launch_at=None):
    """Evaluate current canonical source state only, never book/predict a future free slot."""
    result = dict(
        kind="throttle",
        canonical_key=THROTTLE_KEY,
        status="BLOCKED_NOW",
        current_ready=False,
        blocked_now=None,
        backlog_seconds=None,
        source_interval_seconds=None,
        max_wait_seconds=MAX_WAIT_SECONDS,
        deadline_seconds=DEADLINE_SECONDS,
        launch_at=None,
        launch_ready=None,
        launch_recheck_required=True,
        future_contention_unconfirmed=True,
        quote_request_duration_measured=False,
        blocking_reasons=[],
        can_collect=False,
        slot_reserved=False,
        cooldown_cleared=False,
    )
    try:
        now, launch = _clock_pair(now, launch_at)
    except (ValueError, TypeError, OverflowError):
        result.update(
            status="INVALID_TIME", blocking_reasons=["invalid_check_or_launch_time"]
        )
        return result
    result.update(
        checked_at=now.isoformat(), launch_at=launch.isoformat() if launch else None
    )
    try:
        valid_interval = (
            not isinstance(source_interval_seconds, bool)
            and isinstance(source_interval_seconds, (int, float))
            and math.isfinite(source_interval_seconds)
            and source_interval_seconds >= 1
        )
    except OverflowError:
        valid_interval = False
    if not valid_interval:
        result["blocking_reasons"] = ["source_interval_invalid_or_unconfirmed"]
        return result
    result["source_interval_seconds"] = float(source_interval_seconds)
    if not isinstance(rows, list) or any(
        not isinstance(r, dict) or not isinstance(r.get("domain"), str) for r in rows
    ):
        result["blocking_reasons"] = ["throttle_rows_invalid"]
        return result
    canonical = [r for r in rows if r["domain"] == THROTTLE_KEY]
    result["canonical_rows"] = len(canonical)
    if len(canonical) != 1:
        result["blocking_reasons"] = ["canonical_throttle_row_missing_or_duplicate"]
        return result
    row = canonical[0]
    try:
        next_allowed = _at(row["next_allowed_at"])
        updated = _at(row["updated_at"])
        blocked = row["blocked_until"]
        blocked = None if blocked is None else _at(blocked)
    except (KeyError, ValueError, TypeError, OverflowError):
        result["blocking_reasons"] = ["throttle_timestamps_invalid"]
        return result
    if updated > now:
        result["blocking_reasons"] = ["throttle_state_updated_after_check_time"]
        return result
    blocked_now = blocked is not None and blocked > now
    backlog = max(0.0, (next_allowed - now).total_seconds())
    reasons = []
    if blocked_now:
        reasons.append("source_cooldown_active")
    if backlog > MAX_WAIT_SECONDS:
        reasons.append("source_backlog_exceeds_max_wait")
    result.update(
        next_allowed_at=next_allowed.isoformat(),
        updated_at=updated.isoformat(),
        blocked_until=blocked.isoformat() if blocked else None,
        blocked_now=blocked_now,
        backlog_seconds=backlog,
        blocking_reasons=reasons,
        current_ready=not reasons,
        status="PASS_NOW_ONLY" if not reasons else "BLOCKED_NOW",
    )
    return result
