"""Pydantic response schemas for the ops API (Feature 024) — the contract in contracts/ops-api.yaml.

A 202 ack (JobAccepted / BatchAccepted) is returned immediately on enqueue; the front polls Job /
Batch until a terminal status (succeeded / partial / failed / skipped).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel

JobStatusT = Literal["queued", "running", "succeeded", "partial", "failed", "skipped"]
# kind is an informational label transcribed from the worker's summary (e.g. entries+results+odds /
# predict / recommend / refresh_range / discover). Plain str, NOT a Literal: the poll endpoint must
# never 500 on a cosmetic field for a job already committed to the DB — a Literal here once rejected
# the runner's actual "entries+results+odds" and made every terminal refresh job unreadable, so the
# front stayed on 更新中… forever.


class ErrorBody(BaseModel):
    status: int
    code: str
    detail: str


class RefreshRequest(BaseModel):
    force: bool = False


class JobAccepted(BaseModel):
    job_id: uuid.UUID
    status: JobStatusT
    reused: bool
    scope: Literal["race", "range"] = "race"  # Feature 053: range refresh jobs
    scope_value: str
    poll_url: str


class BatchAccepted(BaseModel):
    trace_id: str
    status: JobStatusT
    scope: Literal["day"] = "day"
    scope_value: str
    poll_url: str
    children: list[JobAccepted] = []


class Job(BaseModel):
    job_id: uuid.UUID
    job_type: str
    status: JobStatusT
    scope: str | None = None
    scope_value: str | None = None
    trace_id: str | None = None
    kind: str | None = None
    # why a job ended SKIPPED (transcribed from summary["reason"], e.g. the betting CLI's
    # "recommendations already exist for run …") — lets the front distinguish 生成済み from
    # 予測未生成/オッズ未取得 instead of one opaque 対象なし. Plain str (same rule as kind).
    reason: str | None = None
    # a job this one enqueued as a follow-up (predict → auto recommend, transcribed from
    # summary["recommend_job_id"]) — lets the front keep polling until the buy-ups land too.
    followup_job_id: str | None = None
    processed_rows: int | None = None
    skipped_rows: int | None = None
    error_count: int | None = None
    retry_count: int = 0
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None
    error_message: str | None = None


class Batch(BaseModel):
    trace_id: str
    status: JobStatusT
    scope_value: str | None = None
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    running: int = 0
    children: list[Job] = []
