"""Operator CLI: evaluate baselines and persist results (quickstart.md)."""

from __future__ import annotations

import argparse
import datetime
import json

from horseracing_db.session import create_db_engine
from sqlalchemy.orm import Session

from .baselines import MarketBaseline, UniformBaseline
from .dataset import load_eval_races
from .harness import evaluate
from .store import save_baseline

_BASELINES = {"market": MarketBaseline, "uniform": UniformBaseline}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_eval")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("evaluate-baseline", help="evaluate a baseline and store results")
    p.add_argument("--baseline", choices=sorted(_BASELINES), required=True)
    p.add_argument("--no-save", action="store_true", help="skip writing to model_versions")
    args = parser.parse_args(argv)

    engine = create_db_engine()
    with Session(engine) as session:
        races = load_eval_races(session, start_date=datetime.date(2007, 1, 1))
        predictor = _BASELINES[args.baseline]()
        result = evaluate(predictor, races)
        version = f"baseline-{args.baseline}-v1"
        if not args.no_save:
            save_baseline(session, version, result)

    print(f"baseline={args.baseline} version={version} valid_years={result.valid_years}")
    print(json.dumps(result.overall, indent=2, ensure_ascii=False))
    return 0
