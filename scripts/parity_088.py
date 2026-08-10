"""Feature 088 T011: shared-column byte parity — the finish_decomp bundle is purely additive
(SC-002 / FR-007 / INV-C7).

Method: load the source frames ONCE and build the as-of matrix TWICE in the same process —
with the bundle (`skip_blocks=∅`, features-020) and without it (`skip_blocks={"finish_decomp"}`,
= the features-018 column set produced by the very same code paths). Every column of the
bundle-less build must be byte-identical in the bundle build.

Why not compare two parquet files: the DB moves under us (the ops worker ingests odds/results
continuously — constitution V overwrites odds in place), so two builds taken minutes apart are
built from DIFFERENT source snapshots and their shared columns may legitimately differ. A single
in-process double build removes that confound entirely.

Usage:
    DATABASE_URL=... uv run --project features python scripts/parity_088.py
"""

from __future__ import annotations

import os

from pandas.testing import assert_frame_equal
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from horseracing_features.finish_decomposition_features import FINISH_DECOMP_COLUMNS
from horseracing_features.loader import load_frames
from horseracing_features.materialize import build_asof_features

_KEYS = ["race_id", "horse_id"]


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: DATABASE_URL unset")
        return 1
    eng = create_engine(url)
    with Session(eng) as s:
        frames = load_frames(s)  # ONE snapshot for both builds

    without = build_asof_features(frames, skip_blocks=frozenset({"finish_decomp"}))
    with_bundle = build_asof_features(frames)

    shared = [c for c in without.columns if c in with_bundle.columns]
    added = [c for c in with_bundle.columns if c not in without.columns]
    dropped = [c for c in without.columns if c not in with_bundle.columns]

    print(f"rows: without={len(without):,} with={len(with_bundle):,}")
    print(f"cols: without={len(without.columns)} with={len(with_bundle.columns)} "
          f"shared={len(shared)} added={len(added)}")
    print(f"added columns: {sorted(added)}")

    if dropped:
        print(f"FAIL: columns disappeared (not additive): {sorted(dropped)}")
        return 1
    if sorted(added) != sorted(FINISH_DECOMP_COLUMNS):
        print(f"FAIL: added columns != the bundle ({sorted(FINISH_DECOMP_COLUMNS)})")
        return 1
    if len(without) != len(with_bundle):
        print(f"FAIL: row count changed {len(without):,} -> {len(with_bundle):,}")
        return 1

    a = without.sort_values(_KEYS, kind="stable").reset_index(drop=True)[shared]
    b = with_bundle.sort_values(_KEYS, kind="stable").reset_index(drop=True)[shared]
    try:
        assert_frame_equal(a, b, check_exact=True, check_dtype=True)
    except AssertionError as exc:
        bad = [c for c in shared if not a[c].equals(b[c])]
        print(f"FAIL: {len(bad)} shared column(s) differ: {bad[:20]}")
        print(str(exc)[:2000])
        return 1

    print(f"PASS: all {len(shared)} shared columns byte-identical over {len(a):,} rows "
          "(check_exact + check_dtype)")
    nn = with_bundle[FINISH_DECOMP_COLUMNS].notna().mean()
    print("\nbundle non-missing rate:")
    for c in FINISH_DECOMP_COLUMNS:
        print(f"  {c:<24} {nn[c]:7.3%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
