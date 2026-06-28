"""horseracing ops — write/ingestion service for on-demand netkeiba refresh (Feature 024).

Separate from the read-only 014 API (`api/`): this package WRITES (owner DB role) and is reached
only by the front's "データ更新" buttons. The read/display path stays on 014 (app_ro). No new DB
schema — reuses `ingestion_jobs` as a durable queue + audit (trace_id / retry_count / summary).
"""

from __future__ import annotations

API_VERSION = "v1"
API_PREFIX = "/ops/v1"

#: how the ops/worker job rows are tagged in ingestion_jobs.job_type
JOB_TYPE_RACE = "refresh_race"
JOB_TYPE_DAY = "refresh_day"
