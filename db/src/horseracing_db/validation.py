"""Reusable validators shared by downstream features (contracts/validation.md).

Pure, DB-independent functions. ``is_in_ingest_scope`` is the single source of
truth for the 2007 ingest boundary (research R8 / FR-024); ingest features MUST
use it rather than re-implementing the date comparison.
"""

from __future__ import annotations

import datetime
import re

_RACE_ID_RE = re.compile(r"^[0-9]{12}$")

#: First in-scope race date. 2006 and earlier use a different ID scheme.
INGEST_SCOPE_START = datetime.date(2007, 1, 1)


def is_valid_race_id(race_id: str) -> bool:
    """True iff ``race_id`` is a 12-digit string (^[0-9]{12}$)."""
    return isinstance(race_id, str) and _RACE_ID_RE.fullmatch(race_id) is not None


def is_in_ingest_scope(race_date: datetime.date) -> bool:
    """True iff ``race_date`` is on or after 2007-01-01."""
    return race_date >= INGEST_SCOPE_START
