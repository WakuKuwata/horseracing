"""Shared leak-boundary assertions (Feature 020 T002, 憲法 II / FR-003).

Two reusable checks every as-of / cross-horse feature must satisfy:

* **cutoff invariance** — a target row's feature values must NOT change when data
  dated on/after the target race is mutated (features are strictly-before only).
* **target-row / same-day exclusion** — cross-horse aggregates (jockey / trainer /
  sire …) must NOT change when the target row's own result, or a same-day sibling's
  result, is mutated (a horse's own race-day never feeds its own features).

Both reduce to the same core: build features from a base and a mutated ``Frames`` and
assert the target ``(race_id, horse_id)`` row is bit-identical (NaN-safe). The
feature-specific test owns the build function and the mutation; this module owns the
comparison, so per-feature leak tests stop re-implementing NaN-aware row equality.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pandas as pd

from horseracing_features.loader import Frames

#: builds a per-(race_id, horse_id) feature frame from Frames.
BuildFn = Callable[[Frames], pd.DataFrame]
#: mutates a Frames in place (e.g. change a future/same-day/target result).
MutateFn = Callable[[Frames], None]


def target_row(build: BuildFn, frames: Frames, race_id: str, horse_id: str) -> pd.Series:
    """The single feature row for ``(race_id, horse_id)`` (asserts exactly one)."""
    df = build(frames)
    sel = df[(df["race_id"] == race_id) & (df["horse_id"] == horse_id)]
    assert len(sel) == 1, f"expected 1 row for ({race_id},{horse_id}), got {len(sel)}"
    return sel.iloc[0]


def first_diff(
    a: pd.Series, b: pd.Series, cols: Sequence[str] | None = None
) -> str | None:
    """First column whose value differs (NaN==NaN), as a human message, else None."""
    check = list(cols) if cols is not None else [c for c in a.index if c in b.index]
    for c in check:
        av, bv = a[c], b[c]
        if pd.isna(av) and pd.isna(bv):
            continue
        if av != bv:
            return f"{c}: {av!r} -> {bv!r}"
    return None


def rows_equal(
    a: pd.Series, b: pd.Series, cols: Sequence[str] | None = None
) -> bool:
    """NaN-safe equality over ``cols`` (default: columns common to both rows)."""
    return first_diff(a, b, cols) is None


def assert_invariant(
    build: BuildFn,
    base: Frames,
    mutated: Frames,
    race_id: str,
    horse_id: str,
    *,
    cols: Sequence[str] | None = None,
) -> None:
    """Core: the target row must be identical between ``base`` and ``mutated``."""
    before = target_row(build, base, race_id, horse_id)
    after = target_row(build, mutated, race_id, horse_id)
    diff = first_diff(before, after, cols)
    assert diff is None, (
        f"leak: ({race_id},{horse_id}) feature changed after mutation — {diff}"
    )


def assert_cutoff_invariant(
    build: BuildFn,
    make_frames: Callable[[list[dict]], Frames],
    specs: list[dict],
    race_id: str,
    horse_id: str,
    mutate: MutateFn,
    *,
    cols: Sequence[str] | None = None,
) -> None:
    """Mutating on/after-cutoff data must leave the target row unchanged (strictly-before).

    ``mutate`` receives a fresh Frames (same ``specs``) and should change a value dated
    on or after the target race — e.g. the target race's own result or a future race.
    """
    base = make_frames(specs)
    mut = make_frames(specs)
    mutate(mut)
    assert_invariant(build, base, mut, race_id, horse_id, cols=cols)


def assert_crosshorse_excludes(
    build: BuildFn,
    make_frames: Callable[[list[dict]], Frames],
    specs: list[dict],
    race_id: str,
    horse_id: str,
    mutate: MutateFn,
    *,
    cols: Sequence[str] | None = None,
) -> None:
    """Mutating the target's own / a same-day sibling's result must not move cross-horse stats.

    ``mutate`` receives a fresh Frames (same ``specs``) and should change the target row's
    own result or a same-day sibling's result; cross-horse aggregates (jockey / trainer /
    sire …) must exclude the target row and its race-day, so the target row is unchanged.
    """
    base = make_frames(specs)
    mut = make_frames(specs)
    mutate(mut)
    assert_invariant(build, base, mut, race_id, horse_id, cols=cols)
