"""Operator CLI for JRA-VAN ingestion (contracts/cli.md)."""

from __future__ import annotations

import argparse
from pathlib import Path

from horseracing_db.session import create_db_engine
from sqlalchemy.orm import Session

from .pipeline import IngestSummary, ingest_year


def _print_summary(summary: IngestSummary) -> None:
    print(
        f"year={summary.year} races={summary.races} race_horses={summary.race_horses} "
        f"race_results={summary.race_results} skipped={summary.skipped} "
        f"skipped_rows={summary.skipped_rows} errors={summary.errors}"
    )


def _exit_code(summary: IngestSummary) -> int:
    if summary.skipped:
        return 3
    if summary.errors:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    p_year = sub.add_parser("ingest-year", help="ingest one year file")
    p_year.add_argument("path")

    p_all = sub.add_parser("ingest-all", help="ingest all year files in a directory")
    p_all.add_argument("dir")

    args = parser.parse_args(argv)
    engine = create_db_engine()

    if args.command == "ingest-year":
        with Session(engine) as session:
            summary = ingest_year(session, args.path)
        _print_summary(summary)
        return _exit_code(summary)

    if args.command == "ingest-all":
        files = sorted(
            p for p in Path(args.dir).iterdir() if p.is_file() and p.stem.isdigit()
        )
        codes: list[int] = []
        with Session(engine) as session:
            for f in files:
                summary = ingest_year(session, f)
                _print_summary(summary)
                codes.append(_exit_code(summary))
        if any(c == 1 for c in codes):
            return 1
        if any(c == 2 for c in codes):
            return 2
        return 0

    return 1
