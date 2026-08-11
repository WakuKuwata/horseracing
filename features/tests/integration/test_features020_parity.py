"""Feature 091 T013/T014: real-DB parity + the invariant that justifies the 1-column design.

INV-W7  every pre-091 column is byte-identical to the captured baseline
INV-W8  source_fingerprint is unchanged (no new source column is read)
INV-W4  a row with a past start always has prev_weight  <- fails CLOSED

INV-W4 is the load-bearing one: FR-003/FR-004 are satisfied by the EXISTING days_since_last /
has_past_race columns only because "most recent weighed start" == "previous start" in this data
(99.95% of started rows carry a weight). If that stops holding, the two stop being equivalent and
this feature needs the dedicated freshness/availability columns after all (research D1).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.orm import Session

BASELINE = (
    Path(__file__).resolve().parents[3]
    / "specs/091-serving-weight-imputation/evidence/baseline_features018.json"
)

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="real-DB parity test requires DATABASE_URL"
)


def _col_digest(s: pd.Series) -> str:
    return hashlib.sha256(s.astype("string").fillna("").str.cat(sep="\x1f").encode()).hexdigest()


@pytest.fixture(scope="module")
def built():
    from horseracing_db.session import create_db_engine

    from horseracing_features.builder import build_feature_matrix

    with Session(create_db_engine()) as session:
        return build_feature_matrix(session)


@pytest.fixture(scope="module")
def baseline():
    if not BASELINE.exists():
        pytest.skip(f"baseline not captured: run evidence/capture_baseline.py first ({BASELINE})")
    return json.loads(BASELINE.read_text())


def test_inv_w7_shared_columns_are_byte_identical(built, baseline):
    """Every column that existed before 091 keeps its exact values (additive merge)."""
    before = set(baseline["columns"])
    now = set(built.columns)
    assert before - now == set(), f"columns disappeared: {sorted(before - now)}"
    assert now - before == {"prev_weight"}, f"unexpected new columns: {sorted(now - before - {'prev_weight'})}"
    assert built.shape[0] == baseline["shape"][0], "row count changed"

    mismatched = [
        c for c in sorted(before) if _col_digest(built[c]) != baseline["per_column_digest"][c]
    ]
    assert not mismatched, f"pre-091 columns changed: {mismatched}"


def test_inv_w8_source_fingerprint_unchanged(baseline):
    """No new SOURCE column is read, so materialized artifacts stay valid."""
    from horseracing_db.session import create_db_engine

    from horseracing_features.loader import load_frames
    from horseracing_features.materialize import source_fingerprint

    with Session(create_db_engine()) as session:
        assert source_fingerprint(load_frames(session)) == baseline["source_fingerprint"]


#: INV-W4 bound. Measured over the whole DB (2007-2026): exactly 1 of 862,274 rows-with-a-past-
#: start has no weighed prior start (0.000116%) — a horse whose only earlier start was 計不. The
#: 1-column reduction (research D1) rests on this being vanishingly rare, NOT on it being zero;
#: the original "0 rows" figure was measured on the 2021-2026 window only. The bound is set two
#: orders of magnitude above the observed rate so ordinary data growth does not trip it, but a
#: real coverage regression (e.g. a source that stops publishing weights) does.
INV_W4_MAX_RATE = 0.0001  # 0.01% of rows with a past start


def test_inv_w4_rows_with_a_past_start_have_prev_weight(built):
    """FAILS CLOSED above the bound. If this breaks, research D1's 1-column reduction no longer
    holds — `has_prev_weight` stops being equivalent to `has_past_race` and `weight_age_days`
    stops being equivalent to `days_since_last`. Add both columns back and re-measure."""
    has_past = pd.to_numeric(built["has_past_race"], errors="coerce") == 1
    n_past = int(has_past.sum())
    missing = built.loc[has_past & built["prev_weight"].isna()]
    rate = len(missing) / n_past if n_past else 0.0
    assert rate <= INV_W4_MAX_RATE, (
        f"{len(missing)}/{n_past} ({rate:.6%}) rows have a past start but no prev_weight, "
        f"above the pre-registered bound {INV_W4_MAX_RATE:.4%}. research D1 assumed 'most recent "
        "weighed start' == 'previous start'; that assumption has degraded. Re-examine adding "
        "weight_age_days / has_prev_weight as explicit columns."
    )


def test_inv_w4_every_exception_is_explained_by_an_unweighed_history(built):
    """The tolerated exceptions must all be the SAME known cause: the horse has earlier starts but
    none of them recorded a usable weight. Any other cause means the resolver is dropping sources
    it should have found, which the rate bound alone would not catch."""
    from horseracing_db.enums import EntryStatus

    from horseracing_features.weight_history_features import build_weight_history_features

    has_past = pd.to_numeric(built["has_past_race"], errors="coerce") == 1
    missing = built.loc[has_past & built["prev_weight"].isna(), ["race_id", "horse_id"]]
    if missing.empty:
        pytest.skip("no exceptions to explain")

    from horseracing_db.session import create_db_engine

    from horseracing_features.loader import load_frames

    with Session(create_db_engine()) as session:
        frames = load_frames(session)
    runs = frames.race_horses[["race_id", "horse_id", "entry_status", "weight"]].merge(
        frames.races[["race_id", "race_date"]], on="race_id", how="left"
    )
    runs["race_date"] = pd.to_datetime(runs["race_date"])
    w = pd.to_numeric(runs["weight"], errors="coerce")
    runs["usable"] = (
        (runs["entry_status"] == EntryStatus.STARTED) & w.between(200, 800, inclusive="both")
    )
    by_horse = {h: g for h, g in runs.groupby("horse_id", sort=False)}

    unexplained = []
    for row in missing.itertuples():
        g = by_horse[row.horse_id]
        target_date = g.loc[g["race_id"] == row.race_id, "race_date"].iloc[0]
        earlier = g[g["race_date"] < target_date]
        if earlier["usable"].any():
            unexplained.append((row.race_id, row.horse_id))
    assert not unexplained, (
        f"{len(unexplained)} rows lack prev_weight despite having a usable earlier weight: "
        f"{unexplained[:5]}. The as-of resolver is dropping valid sources."
    )
    _ = build_weight_history_features  # imported to pin the module under test


def test_prev_weight_is_float64_and_within_plausible_range(built):
    assert built["prev_weight"].dtype == "float64"
    present = built["prev_weight"].dropna()
    assert present.between(200, 800).all()
