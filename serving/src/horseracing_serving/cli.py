"""Operator CLI: predict --race-id / --date (quickstart.md)."""

from __future__ import annotations

import argparse
import datetime
import os

from horseracing_db.session import create_db_engine
from horseracing_probability.calib_activation import ActivationError
from horseracing_probability.calib_manifest import ManifestError
from sqlalchemy.orm import Session

from .model_loader import ServingError
from .pipeline import run_serving, run_serving_backfill

#: Feature 055: default parquet path for --use-materialized. Follows the weights_uri convention
#: (relative to cwd=serving/, e.g. the 028 ops subprocess runs there). Fail-closed: missing/stale
#: raises with re-materialize guidance, never a silent in-memory fallback.
_MAT_DEFAULT = "../artifacts/features.parquet"


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _add_calib_args(p: argparse.ArgumentParser) -> None:
    """Feature 076: shared --calib-manifest / --calib-mode flags (predict / predict-backfill)."""
    p.add_argument("--calib-manifest", dest="calib_manifest", default=None,
                   help="076: absolute path to the immutable 074 manifest (stage-discount λ). "
                        "Requires --calib-mode manifest-required. WIN is untouched.")
    p.add_argument("--calib-mode", dest="calib_mode",
                   choices=["legacy-runtime", "manifest-required"], default="legacy-runtime",
                   help="076: legacy-runtime (default) fits the discount at runtime; "
                        "manifest-required reads λ from --calib-manifest (fail-closed).")


def _validate_calib(args) -> None:
    """Feature 076: reject relative path / mode↔manifest contradiction (cli-contract)."""
    mode = getattr(args, "calib_mode", "legacy-runtime")
    path = getattr(args, "calib_manifest", None)
    if mode == "manifest-required" and not path:
        raise SystemExit("--calib-mode manifest-required requires --calib-manifest")
    if path and mode != "manifest-required":
        raise SystemExit("--calib-manifest requires --calib-mode manifest-required")
    if path and not os.path.isabs(path):
        raise SystemExit(f"--calib-manifest must be an absolute path (got {path!r})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_serving")
    sub = parser.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("predict", help="infer + persist for a race or a date")
    pr.add_argument("--race-id", default=None)
    pr.add_argument("--date", type=_parse_date, default=None)
    pr.add_argument("--model-version", default=None, help="explicit model (else single active)")
    pr.add_argument("--use-materialized", action="store_true",
                    help="read as-of features from the 025 parquet (bit-parity, fail-closed)")
    pr.add_argument("--materialized-path", default=_MAT_DEFAULT)
    _add_calib_args(pr)
    pr.add_argument("--database-url", default=None)

    # Feature 044: date-range predict backfill (idempotent per model; fills the product with data).
    pb = sub.add_parser("predict-backfill", help="infer + persist over a date range (044)")
    pb.add_argument("--from", dest="from_", type=_parse_date, required=True)
    pb.add_argument("--to", type=_parse_date, required=True)
    pb.add_argument("--model-version", default=None, help="explicit model (else single active)")
    pb.add_argument("--force", action="store_true",
                    help="regenerate even if the model already has a run for the race")
    pb.add_argument("--use-materialized", action="store_true",
                    help="read as-of features from the 025 parquet (verify once, then per-day)")
    pb.add_argument("--materialized-path", default=_MAT_DEFAULT)
    _add_calib_args(pb)
    pb.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    _validate_calib(args)  # Feature 076: relative path / mode↔manifest contradiction (exit != 0)
    if args.command == "predict-backfill":
        engine = create_db_engine(args.database_url)
        try:
            with Session(engine) as session:
                counts = run_serving_backfill(
                    session, date_from=args.from_, date_to=args.to,
                    model_version=args.model_version, force=args.force,
                    calib_manifest=args.calib_manifest, calib_mode=args.calib_mode,
                    use_materialized=args.use_materialized,
                    materialized_path=args.materialized_path if args.use_materialized else None,
                )
        except (ServingError, ActivationError, ManifestError) as e:
            parser.error(str(e))  # Feature 076: bad manifest fails closed BEFORE any write
        c = counts.as_dict()
        print(f"predict-backfill {args.from_}..{args.to}")
        print(f"  generated={c['generated']} skip_exists={c['skip_exists']} "
              f"skip_no_started={c['skip_no_started']} error_days={c['error_days']}")
        return 0
    if args.command == "predict":
        if (args.race_id is None) == (args.date is None):
            parser.error("exactly one of --race-id or --date is required")
        engine = create_db_engine(args.database_url)
        try:
            with Session(engine) as session:
                results = run_serving(
                    session, race_id=args.race_id, date=args.date,
                    model_version=args.model_version,
                    calib_manifest=args.calib_manifest, calib_mode=args.calib_mode,
                    use_materialized=args.use_materialized,
                    materialized_path=args.materialized_path if args.use_materialized else None,
                )
        except (ServingError, ActivationError, ManifestError) as e:
            parser.error(str(e))  # Feature 076: bad manifest fails closed BEFORE any write
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
