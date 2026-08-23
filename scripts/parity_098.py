"""Feature 098: prove raw@features-023 parity and canonical-v1's exact change boundary.

WRITE ONLY during implementation. The operator runs this against the real database after the
features-021 baseline has been captured.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from horseracing_features.race_class_canon import SPLIT_TOKENS, canonicalise
from pandas.testing import assert_frame_equal, assert_series_equal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from horseracing_training.dataset import TrainingMatrix, build_training_matrix

DEFAULT_DATABASE_URL = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
BASELINE = Path("/Users/kuwatawaku/workspace/horseracing-098/out/098-baseline-features021.parquet")
EVIDENCE = (
    Path(__file__).resolve().parent.parent
    / "specs/098-race-class-spelling/evidence-parity.md"
)
KEYS = ["race_id", "horse_id"]


def _aligned(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    missing = [column for column in columns if column not in frame]
    if missing:
        raise AssertionError(f"missing columns: {missing}")
    selected = frame.loc[:, columns]
    if selected.duplicated(KEYS).any():
        raise AssertionError("duplicate (race_id, horse_id) keys")
    return selected.set_index(KEYS).sort_index()


def _compare_baseline(baseline: pd.DataFrame, raw: TrainingMatrix) -> None:
    if set(baseline.columns) != set(raw.frame.columns):
        missing = sorted(set(baseline.columns) - set(raw.frame.columns))
        extra = sorted(set(raw.frame.columns) - set(baseline.columns))
        raise AssertionError(f"raw/baseline column mismatch: missing={missing}, extra={extra}")
    expected = _aligned(baseline, list(baseline.columns))
    actual = _aligned(raw.frame, list(baseline.columns))
    assert_frame_equal(actual, expected, check_exact=True, check_dtype=True)


def _compare_representations(raw: TrainingMatrix, canonical: TrainingMatrix) -> tuple[int, dict]:
    if set(raw.frame.columns) != set(canonical.frame.columns):
        raise AssertionError("raw/canonical column sets differ")
    columns = list(raw.frame.columns)
    raw_frame = _aligned(raw.frame, columns)
    canonical_frame = _aligned(canonical.frame, columns)

    other_columns = [column for column in raw_frame.columns if column != "race_class"]
    assert_frame_equal(
        raw_frame[other_columns],
        canonical_frame[other_columns],
        check_exact=True,
        check_dtype=True,
    )

    raw_values = raw_frame["race_class"].astype(object)
    canonical_values = canonical_frame["race_class"].astype(object)
    expected_values, expected_audit = canonicalise(raw_values)
    assert_series_equal(canonical_values, expected_values, check_names=False)

    equal = raw_values.eq(canonical_values) | (raw_values.isna() & canonical_values.isna())
    differing = ~equal
    expected_differing = raw_values.isin(SPLIT_TOKENS)
    assert_series_equal(differing, expected_differing, check_names=False)
    if not differing.any():
        raise AssertionError("no split-token rows differed; canonical-v1 was not exercised")

    audit = canonical.build_audit.get("race_class")
    if audit != expected_audit:
        raise AssertionError(f"canonical audit mismatch: {audit!r} != {expected_audit!r}")
    return int(differing.sum()), audit


def _write_evidence(n_rows_differing: int, audit: dict, n_rows: int, n_columns: int) -> None:
    EVIDENCE.write_text(
        "\n".join(
            [
                "# Feature 098 parity evidence",
                "",
                "- Result: PASS",
                f"- Rows: {n_rows:,}",
                f"- Columns: {n_columns}",
                "- raw@features-023 vs captured features-021: all columns exact, dtype exact",
                "- canonical-v1 vs raw: every non-race_class column exact, dtype exact",
                f"- n_rows_differing: {n_rows_differing:,}",
                "",
                "## Canonical build audit",
                "",
                "```json",
                json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    )


def main() -> int:
    try:
        baseline = pd.read_parquet(BASELINE)
        url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
        engine = create_engine(url, isolation_level="REPEATABLE READ")
        try:
            with Session(engine) as session:
                session.execute(text("SET TRANSACTION READ ONLY"))
                raw = build_training_matrix(session, representation="raw")
                _compare_baseline(baseline, raw)
                del baseline
                canonical = build_training_matrix(session, representation="canonical-v1")
        finally:
            engine.dispose()

        n_rows_differing, audit = _compare_representations(raw, canonical)
        _write_evidence(n_rows_differing, audit, len(raw.frame), len(raw.frame.columns))
        print(
            f"PASS: raw parity exact over {len(raw.frame):,} rows "
            f"and {len(raw.frame.columns)} columns"
        )
        print(f"n_rows_differing={n_rows_differing:,}")
        print(f"audit={json.dumps(audit, ensure_ascii=False, sort_keys=True)}")
        print(f"evidence={EVIDENCE}")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
