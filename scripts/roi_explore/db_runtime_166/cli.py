"""Read-only saved-DB benchmark. No collection flags or live admission path exist."""

from __future__ import annotations
import time

PROCESS_STARTED_NS = time.perf_counter_ns()
import argparse  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--out", type=Path, required=True)
    benchmark.add_argument("--no-warm", action="store_true")
    verification = commands.add_parser("verify")
    verification.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    from .run import run, verify

    if args.command == "benchmark":
        result = run(
            args.out, process_started_ns=PROCESS_STARTED_NS, warm=not args.no_warm
        )
    else:
        from scripts.roi_explore.model_connection_163.run import connection_disabled

        with connection_disabled():
            result = verify(args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
