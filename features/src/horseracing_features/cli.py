"""Operator CLI: build (and optionally materialize) the feature matrix (US4)."""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from horseracing_db.session import create_db_engine
from sqlalchemy.orm import Session

from .builder import build_feature_matrix
from .loader import load_frames
from .materialize import write_materialized

# Repo-root-anchored default so it does NOT depend on the CWD the CLI is invoked from
# (cli.py -> horseracing_features -> src -> features -> repo root). A bare relative path
# silently wrote to <cwd>/artifacts and diverged from the serving default (../artifacts).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PARQUET = str(_REPO_ROOT / "artifacts" / "features.parquet")


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_features")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build-features", help="build the feature matrix")
    p.add_argument("--from", dest="start", type=_parse_date, default=None)
    p.add_argument("--to", dest="end", type=_parse_date, default=None)
    p.add_argument("--out", default=None, help="parquet path to materialize")
    p.add_argument("--use-materialized", action="store_true",
                   help="read the as-of block from --materialized (Feature 025)")
    p.add_argument("--materialized", default=_DEFAULT_PARQUET, help="materialized parquet path")

    # Feature 025: generate the as-of feature parquet + manifest (one heavy pass).
    m = sub.add_parser("materialize", help="materialize as-of features to parquet + manifest (025)")
    m.add_argument("--out", default=_DEFAULT_PARQUET, help="parquet path (manifest sidecar)")

    args = parser.parse_args(argv)
    engine = create_db_engine()

    if args.command == "materialize":
        with Session(engine) as session:
            frames = load_frames(session)
            manifest = write_materialized(args.out, frames)
        ncols = len(manifest.materialized_columns)
        fp = manifest.source_fingerprint[:12]
        print(f"materialized {manifest.n_rows} rows -> {args.out}")
        print(f"  range={manifest.data_from}..{manifest.data_through} cols={ncols}")
        print(f"  feature_version={manifest.feature_version} fingerprint={fp}…")
        return 0

    kwargs = {}
    if args.start is not None:
        kwargs["start_date"] = args.start
    if args.end is not None:
        kwargs["end_date"] = args.end
    if args.use_materialized:
        kwargs["use_materialized"] = True
        kwargs["materialized_path"] = Path(args.materialized)
    with Session(engine) as session:
        matrix = build_feature_matrix(session, **kwargs)
    if args.out:
        matrix.to_parquet(args.out, index=False)
        print(f"wrote matrix {len(matrix)} rows -> {args.out}")
    print(f"rows={len(matrix)} cols={len(matrix.columns)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
