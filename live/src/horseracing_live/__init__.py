"""horseracing-live: live serving for upcoming (result-pending) races (Feature 019).

Thin orchestration layer: guard → (optional) scrape(008) → run_serving(006, as-of leak-safe) →
recommend(011/016 on pre-race odds) → prospective report. Reuses existing leak-safe paths (adds no
new prediction logic). Fail-closed: only result-pending + valid + complete races are served; missing
odds skips odds-dependent recommendations (prediction still produced). Live Kelly is shadow (no real
stakes). No schema change.
"""

from __future__ import annotations

LIVE_LOGIC_VERSION = "live-0.1.0"

from .orchestrate import LiveServeReport, live_serve, list_pending  # noqa: E402

__all__ = ["LIVE_LOGIC_VERSION", "live_serve", "list_pending", "LiveServeReport"]
