"""Feature 097 T013: shared-column byte parity — early_mid_pace is purely additive (SC-001 /
INV-EM5). Same method as parity_088: ONE source snapshot, TWO in-process builds (with the leaf
block / with ``skip_blocks={"early_mid_pace"}`` = the features-021 column set from the very same
code paths). Comparing two parquet files would confound the change with the DB moving under us.

Usage:
    DATABASE_URL=... uv run --project features python scripts/parity_097.py
"""

from __future__ import annotations

import os
import pathlib

from pandas.testing import assert_frame_equal
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from horseracing_features.early_mid_pace_features import EARLY_MID_PACE_COLUMNS
from horseracing_features.loader import load_frames
from horseracing_features.materialize import build_asof_features

_KEYS = ["race_id", "horse_id"]
EVIDENCE = pathlib.Path(__file__).resolve().parent.parent / "specs/097-early-mid-pace/evidence-parity.md"


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: DATABASE_URL unset"); return 1
    with Session(create_engine(url)) as s:
        frames = load_frames(s)  # ONE snapshot for both builds

    without = build_asof_features(frames, skip_blocks=frozenset({"early_mid_pace"}))
    with_block = build_asof_features(frames)
    shared = [c for c in without.columns if c in with_block.columns]
    added = [c for c in with_block.columns if c not in without.columns]
    dropped = [c for c in without.columns if c not in with_block.columns]
    lines = [f"rows: without={len(without):,} with={len(with_block):,}",
             f"cols: without={len(without.columns)} with={len(with_block.columns)} "
             f"shared={len(shared)} added={len(added)}", f"added: {sorted(added)}"]
    ok = True
    if dropped:
        lines.append(f"FAIL: columns disappeared: {sorted(dropped)}"); ok = False
    if sorted(added) != sorted(EARLY_MID_PACE_COLUMNS):
        lines.append("FAIL: added != EARLY_MID_PACE_COLUMNS"); ok = False
    if len(without) != len(with_block):
        lines.append("FAIL: row count changed"); ok = False
    if ok:
        a = without.sort_values(_KEYS, kind="stable").reset_index(drop=True)[shared]
        b = with_block.sort_values(_KEYS, kind="stable").reset_index(drop=True)[shared]
        try:
            assert_frame_equal(a, b, check_exact=True, check_dtype=True)
            lines.append(f"PASS: all {len(shared)} shared columns byte-identical over {len(a):,} rows "
                         "(check_exact + check_dtype) — mismatch 0")
        except AssertionError as exc:
            bad = [c for c in shared if not a[c].equals(b[c])]
            lines.append(f"FAIL: {len(bad)} shared column(s) differ: {bad[:20]}"); lines.append(str(exc)[:1500])
            ok = False
        nn = with_block[list(EARLY_MID_PACE_COLUMNS)].notna().mean()
        lines.append("new-column non-missing rate (all rows):")
        for c in EARLY_MID_PACE_COLUMNS:
            lines.append(f"  {c:<26} {nn[c]:7.3%}")
    out = "\n".join(lines)
    print(out)
    EVIDENCE.write_text("# 097 T013 shared-column parity (SC-001 / INV-EM5)\n\n```\n" + out + "\n```\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
