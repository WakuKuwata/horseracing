"""Shared helper for the split-build projection parity gate (feature 072).

Every converted block must satisfy: `build(frames, target_race_ids={R})` == `build(frames)` restricted
to those races' rows, byte-for-byte (values, dtype, row + column order). This helper is the single
adoption gate used by every per-block projection test.
"""

from __future__ import annotations

import pandas as pd
from pandas.testing import assert_frame_equal

_KEYS = ["race_id", "horse_id"]


def assert_projected_equals_full(build_fn, frames, race_ids, *, keys=_KEYS, **build_kwargs):
    """Assert a block's projected output is byte-identical to the full build on the target rows.

    ``build_fn(frames, **build_kwargs)`` is the full build (oracle); ``build_fn`` is called again with
    ``target_race_ids=frozenset(race_ids)`` for the projection. Both are sorted by ``keys`` and the
    projection is compared against the full build's rows for ``race_ids`` with ``check_exact=True``.
    Returns the projected frame for further assertions.
    """
    race_ids = frozenset(race_ids)
    full = build_fn(frames, **build_kwargs)
    proj = build_fn(frames, target_race_ids=race_ids, **build_kwargs)

    full_t = (
        full[full["race_id"].isin(race_ids)]
        .sort_values(keys, kind="stable")
        .reset_index(drop=True)
    )
    proj = proj.sort_values(keys, kind="stable").reset_index(drop=True)
    # column set + order must match (projection must not drop/reorder columns)
    assert list(proj.columns) == list(full.columns), (
        f"projected columns differ: {list(proj.columns)} vs {list(full.columns)}"
    )
    assert_frame_equal(full_t, proj[full_t.columns], check_exact=True, check_dtype=True)
    return proj


def target_keys(frames, race_ids) -> pd.DataFrame:
    """The (race_id, horse_id) started rows of ``race_ids`` — the rows a projection must emit."""
    from horseracing_db.enums import EntryStatus

    rh = frames.race_horses
    sub = rh[rh["race_id"].isin(frozenset(race_ids)) & (rh["entry_status"] == EntryStatus.STARTED)]
    return sub[_KEYS].sort_values(_KEYS, kind="stable").reset_index(drop=True)
