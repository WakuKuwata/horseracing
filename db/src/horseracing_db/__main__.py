"""Command-line entry point for database recovery operations."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .dedupe import (
    DedupeChaosSnapshotsError,
    dedupe_chaos_snapshots,
    format_dedupe_result,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m horseracing_db")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dedupe = subparsers.add_parser(
        "dedupe-chaos-snapshots",
        help="report or quarantine duplicate pre-086 chaos snapshots",
    )
    dedupe.add_argument(
        "--apply",
        action="store_true",
        help="quarantine and delete duplicates (default: dry run)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)

    if arguments.command == "dedupe-chaos-snapshots":
        try:
            result = dedupe_chaos_snapshots(apply=arguments.apply)
        except DedupeChaosSnapshotsError as error:
            parser.exit(2, f"error: {error}\n")
        print(format_dedupe_result(result))
        return 0

    parser.error(f"unknown command: {arguments.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
