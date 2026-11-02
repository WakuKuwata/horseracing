"""Exception types of the JRA official-site route (Feature 176, contracts/fetch-and-parse.md §1).

Refusals (403/429/503) reuse ``horseracing_scrape.fetch.FetchRefused`` so the shared cooldown
machinery sees them. Everything JRA-specific that must NOT install a cooldown lives here.
"""

from __future__ import annotations

from ..models import NotYetPublished, ParseError

__all__ = [
    "JraDecodeError",
    "JraParameterError",
    "NotYetPublished",
    "ParseError",
    "SourceIntervalUnconfigured",
]


class JraParameterError(Exception):
    """The site answered "parameter error" (HTTP 400, an HTTP 200 error screen, or a redirect to
    ``error013``). This is an implementation/parameter defect, never a refusal: no cooldown is
    installed and the caller may re-walk from the index at most once (FR-005)."""

    def __init__(self, message: str, *, url: str | None = None, status: int | None = None):
        self.url = url
        self.status = status
        super().__init__(message)


class JraDecodeError(ParseError):
    """The response bytes could not be decoded strictly (cp932). The response is discarded —
    never decoded with replacement characters (research D2)."""


class SourceIntervalUnconfigured(RuntimeError):
    """``HORSERACING_JRA_MIN_INTERVAL_S`` is missing or invalid: the JRA route sends nothing
    (FR-003 — the interval must come from the configured policy, never from a default)."""
