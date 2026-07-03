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
KindT = Literal["entries+odds", "results", "predict", "recommend"]  # 028 predict / 043 recommend


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
    kind: KindT | None = None
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
