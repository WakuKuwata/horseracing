"""Operator CLI: build (and optionally materialize) the feature matrix (US4)."""

from __future__ import annotations

import argparse
import datetime

from horseracing_db.session import create_db_engine
from sqlalchemy.orm import Session

from .builder import build_feature_matrix


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_features")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build-features", help="build the feature matrix")
    p.add_argument("--from", dest="start", type=_parse_date, default=None)
    p.add_argument("--to", dest="end", type=_parse_date, default=None)
    p.add_argument("--out", default=None, help="parquet path to materialize")
    args = parser.parse_args(argv)

    engine = create_db_engine()
    kwargs = {}
    if args.start is not None:
        kwargs["start_date"] = args.start
    if args.end is not None:
        kwargs["end_date"] = args.end
    with Session(engine) as session:
        matrix = build_feature_matrix(session, **kwargs)
    if args.out:
        matrix.to_parquet(args.out, index=False)
        print(f"materialized {len(matrix)} rows -> {args.out}")
    print(f"rows={len(matrix)} cols={len(matrix.columns)}")
    return 0
