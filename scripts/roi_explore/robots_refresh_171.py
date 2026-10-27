"""One authorized robots-only request; never invokes a race collector or model."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, event, text

from scripts.roi_explore.live_preflight_169.access import (
    ORIGIN,
    USER_AGENT,
    MAX_BODY_BYTES,
    inspect_robots,
)
from scripts.roi_explore.live_preflight_169.config import (
    load_profile,
    profile_environment,
)
from scripts.roi_explore.live_preflight_169.database import native_readonly_probe
from scripts.roi_explore.db_runtime_166.database import database_only
from scripts.roi_explore.prospective_rehearsal_162.store import publish

URL = ORIGIN + "/robots.txt"
ROOT = Path(__file__).resolve().parents[2]


def require(ok, code):
    if not ok:
        raise ValueError(code)


@contextlib.contextmanager
def exclusive_cache_lock(directory):
    """Use the same cache lock path as production, with confirmed process lock ownership."""
    require(
        directory.is_dir() and not directory.is_symlink(), "cache_directory_invalid"
    )
    path = directory / (hashlib.sha256(ORIGIN.encode()).hexdigest()[:20] + ".lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def request_once(client, policy, cache, output):
    """Injectable IO boundary: reserve once, GET one fixed URL, never follow or retry."""
    from horseracing_scrape.fetch import FetchRefused, _retry_after_s
    from horseracing_scrape import robots_cache

    started = dt.datetime.now(dt.UTC).isoformat()
    policy.pre_request(URL)
    publish(
        output / "slot-reserved.json",
        dict(
            at=dt.datetime.now(dt.UTC).isoformat(),
            url=URL,
            canonical_key="netkeiba.com",
            throttle_state_write=True,
        ),
    )
    with client.stream("GET", URL, follow_redirects=False) as response:
        code = response.status_code
        # Persist all source refusals, including the site's bare-400 refusal shape.
        if code in (400, 403, 429):
            policy.record_refusal(
                FetchRefused(code, URL, retry_after_s=_retry_after_s(response))
            )
            raise ValueError("robots_source_refused")
        require(code in (200, 404, 410), "robots_http_not_cacheable")
        body = bytearray()
        for chunk in response.iter_bytes():
            require(len(body) + len(chunk) <= MAX_BODY_BYTES, "robots_body_too_large")
            body.extend(chunk)
    outcome = robots_cache.RULES if code == 200 else robots_cache.ABSENT
    stored_body = bytes(body) if code == 200 else b""
    cache.put(ORIGIN, outcome=outcome, status=code, body=stored_body)
    path = cache._entry_path(ORIGIN)
    require(path.is_file() and not path.is_symlink(), "robots_cache_not_saved")
    saved = path.read_bytes()
    doc = json.loads(saved)
    import base64

    fetched = dt.datetime.fromisoformat(doc["fetched_at"])
    require(
        fetched.tzinfo is not None
        and dt.datetime.fromisoformat(started) <= fetched <= dt.datetime.now(dt.UTC),
        "robots_cache_not_updated_this_attempt",
    )

    require(
        doc["origin"] == ORIGIN
        and doc["status"] == code
        and doc["outcome"] == outcome
        and base64.b64decode(doc["body"], validate=True) == stored_body,
        "robots_cache_readback_mismatch",
    )
    # Preserve the exact raw response and the durable cache snapshot separately.
    raw_path = output / "robots-response.bin"
    with raw_path.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    publish(output / "robots-cache-snapshot.json", doc)
    checked = inspect_robots(cache.directory, dt.datetime.now(dt.UTC))
    require(
        checked["current_fresh"]
        and checked["sha256"] == hashlib.sha256(saved).hexdigest(),
        "robots_saved_cache_not_fresh",
    )
    return dict(
        started_at=started,
        finished_at=dt.datetime.now(dt.UTC).isoformat(),
        status="ROBOTS_REFRESHED",
        url=URL,
        http_status=code,
        http_requests=1,
        response_bytes=len(body),
        response_sha256=hashlib.sha256(body).hexdigest(),
        cache_sha256=hashlib.sha256(saved).hexdigest(),
        robots=checked,
        redirects_followed=False,
        retries=0,
        race_data_collection=False,
        model_inference=False,
        observe_invocations=0,
        throttle_state_write=True,
        slot_reserved=True,
        robots_refreshed=True,
        can_collect_race_data=False,
        quote_max_age_seconds=1800,
        both_predictions_durable_before_decision_required=True,
    )


def refresh(profile_path, output):
    # Reserve the run directory before IO. A retry must never silently issue another request.
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    profile = load_profile(profile_path)
    require(not profile.problems and profile.interval >= 60, "private_profile_invalid")
    require(
        profile.public["endpoint"]["login"] == "hr_manual_observer"
        and profile.public["endpoint"]["database"] == "horseracing"
        and profile.public["endpoint"]["host"] == "127.0.0.1"
        and profile.public["endpoint"]["port"] == 15432,
        "observer_endpoint_changed",
    )
    expected = json.loads(
        (
            ROOT / "artifacts/170-observer-provision/run-01/provision-receipt.json"
        ).read_text()
    )
    with database_only() as boundary, profile_environment(profile):
        with native_readonly_probe(profile) as (conn, identity, audit):
            oid = conn.execute(
                text(
                    "SELECT oid::text FROM pg_database WHERE datname=current_database()"
                )
            ).scalar_one()
            require(
                identity["current_login"]
                == identity["session_login"]
                == "hr_manual_observer"
                and identity["database_name"] == "horseracing"
                and identity["server_address"] == expected["identity"]["server_address"]
                and oid == expected["identity"]["database_oid"]
                and identity["standby"] is False,
                "connected_target_changed",
            )
        publish(
            output / "target-readonly.json",
            dict(
                identity=identity,
                database_oid=oid,
                network_boundary=boundary,
                race_data_collection=False,
            ),
        )
        publish(output / "target-readonly-audit.json", audit)
    from horseracing_scrape.politeness import RequestPoliteness
    from horseracing_scrape.robots_cache import RobotsCache
    import httpx

    attempts = []

    class RobotsOnlyTransport(httpx.HTTPTransport):
        def handle_request(self, request):
            require(
                request.method == "GET" and str(request.url) == URL,
                "non_robots_http_forbidden",
            )
            attempts.append(
                dict(
                    method=request.method,
                    url=str(request.url),
                    at=dt.datetime.now(dt.UTC).isoformat(),
                )
            )
            return super().handle_request(request)

    engine = create_engine(
        profile.url,
        hide_parameters=True,
        echo=False,
        connect_args=dict(
            hostaddr="127.0.0.1",
            connect_timeout=5,
            application_name="robots171_only",
            options="-c statement_timeout=10000 -c lock_timeout=2000",
        ),
    )
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def sql_boundary(conn, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.split()).upper()
        require(
            normalized.startswith(
                (
                    "SELECT ",
                    "SHOW ",
                    "INSERT INTO FETCH_THROTTLE_STATE ",
                    "UPDATE FETCH_THROTTLE_STATE SET ",
                )
            ),
            "non_throttle_write_forbidden",
        )
        statements.append(
            dict(
                sql=statement,
                sha256=hashlib.sha256(statement.encode()).hexdigest(),
                parameter_values_saved=False,
            )
        )

    try:
        with (
            profile_environment(profile),
            exclusive_cache_lock(profile.robots_directory),
        ):
            policy = RequestPoliteness.for_engine(
                engine, min_interval_s=profile.interval, max_wait_s=0
            )
            with httpx.Client(
                transport=RobotsOnlyTransport(retries=0, trust_env=False),
                headers={"User-Agent": USER_AGENT},
                timeout=20.0,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                result = request_once(
                    client, policy, RobotsCache(profile.robots_directory), output
                )
            result["source_interval_seconds"] = profile.interval
            publish(output / "result.json", result)
            return result
    finally:
        engine.dispose()
        publish(
            output / "http-attempts.json",
            dict(
                attempts=attempts,
                redirects_followed=False,
                transport_retries=0,
                race_data_collection=False,
            ),
        )
        publish(
            output / "policy-sql.json",
            dict(
                statements=statements,
                allowed_mutation_relation="fetch_throttle_state",
                credential_values_saved=False,
            ),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = refresh(args.profile, args.out)
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "status",
                        "http_requests",
                        "http_status",
                        "race_data_collection",
                        "throttle_state_write",
                        "source_interval_seconds",
                    )
                }
            )
        )
    except Exception as error:
        # Transport/DB exceptions can contain credential-bearing connection diagnostics.
        print(
            json.dumps(
                dict(
                    status="STOPPED_NO_RETRY",
                    error_class=type(error).__name__,
                    private_details_not_printed=True,
                    race_data_collection=False,
                )
            )
        )
        raise SystemExit(1) from None
