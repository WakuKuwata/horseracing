"""Feature 097: deterministic hash of a frame projection, for mask-provenance assertions.

A pseudo-supply-death simulation masks ``race_results.first_3f`` inside one DB session (never
committed) and rebuilds features from it. The scored-set symmetry check cannot see whether the
feature build actually READ the masked values — a materialised parquet, a cached frame or a second
connection would hand an arm the unmasked column while every race/horse/winner set still matches.
This hash is taken over the exact projection the build is about to consume, so a re-run (or a
second arm) can prove it saw the same bytes.

Pure Python: eval has no pandas dependency and imports neither features nor training (the 020
package boundary). Callers pass row tuples (``df[cols].itertuples(index=False, name=None)``).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from decimal import Decimal


def _canon(v) -> str:
    """One spelling per value: None/NaN → "", numbers → repr(float), everything else → str."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, Decimal):
        return repr(float(v))
    if isinstance(v, int | float):
        f = float(v)
        return "" if math.isnan(f) else repr(f)
    s = str(v)
    return "" if s in ("nan", "NaN", "<NA>", "NaT") else s


def frame_projection_hash(rows: Iterable[tuple], cols: list[str] | tuple[str, ...]) -> str:
    """SHA-256 over ``rows`` (each a tuple aligned to ``cols``), row-order independent.

    A single changed cell — including a value becoming NULL, which is what a mask does — changes
    the digest; reordering rows does not.
    """
    cols = list(cols)
    canon = sorted("\x1f".join(_canon(v) for v in r) for r in rows)
    h = hashlib.sha256()
    h.update(("\x1e".join(cols) + "\x1d").encode("utf-8"))
    for r in canon:
        h.update(r.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()
