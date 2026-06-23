"""Operator CLI: predict --race-id / --date (quickstart.md)."""

from __future__ import annotations

import argparse
import datetime

from horseracing_db.session import create_db_engine
from sqlalchemy.orm import Session

from .model_loader import ServingError
from .pipeline import run_serving


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_serving")
    sub = parser.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("predict", help="infer + persist for a race or a date")
    pr.add_argument("--race-id", default=None)
    pr.add_argument("--date", type=_parse_date, default=None)
    pr.add_argument("--model-version", default=None, help="explicit model (else single active)")
    pr.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    if args.command == "predict":
        if (args.race_id is None) == (args.date is None):
            parser.error("exactly one of --race-id or --date is required")
        engine = create_db_engine(args.database_url)
        try:
            with Session(engine) as session:
                results = run_serving(
                    session, race_id=args.race_id, date=args.date,
                    model_version=args.model_version,
                )
        except ServingError as e:
            parser.error(str(e))
        if not results:
            print("no races inferred (no started horses / out of scope)")
            return 0
        print(f"model_version={results[0].model_version} logic_version={results[0].logic_version}")
        for r in results:
            print(f"  race={r.race_id} run={r.prediction_run_id} horses={r.n_horses}")
        print(f"total races persisted: {len(results)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
