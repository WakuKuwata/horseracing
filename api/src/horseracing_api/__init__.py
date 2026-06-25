"""horseracing-api: read-only prediction-serving JSON API (Feature 014).

A thin READ-ONLY FastAPI layer over the existing persisted data (races / predictions / odds /
recommendations) so the future React/Vite front (015) consumes a stable OpenAPI contract
(constitution VI: API/DB contract before any UI). Strictly read-only — every handler is SELECT-only
and the per-request transaction is DB-level READ ONLY; this package depends on ``horseracing-db``
(ORM read) and ``horseracing-probability`` (pure 009/010) ONLY, never ``horseracing-betting``, so no
write path (e.g. recommendation generation) is reachable. Response values never re-enter model
features (leak boundary, constitution II).
"""

from __future__ import annotations

API_VERSION = "v1"
SCHEMA_VERSION = "2026-06-25"
API_PREFIX = "/api/v1"
