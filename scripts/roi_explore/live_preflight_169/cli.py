"""Preflight only: inspect configs/catalogs/cache. There is no observe or refresh command."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from .config import init_profile
from .run import inventory, check, verify
from scripts.roi_explore.db_runtime_166.database import database_only


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-profile")
    init.add_argument("--path", type=Path, required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--out", type=Path, required=True)
    chk = sub.add_parser("check")
    chk.add_argument("--profile", type=Path)
    chk.add_argument("--out", type=Path, required=True)
    chk.add_argument("--launch-at")
    val = sub.add_parser("verify")
    val.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init-profile":
        with database_only():
            result = init_profile(args.path)
    elif args.command == "inventory":
        result = inventory(args.out)
    elif args.command == "check":
        launch = dt.datetime.fromisoformat(args.launch_at) if args.launch_at else None
        result = check(args.profile, args.out, launch_at=launch)
    else:
        with database_only():
            result = verify(args.out)
    # Only explicitly constructed public result dictionaries are printed, never the profile/DSN.
    if args.command in ("check", "inventory"):
        result = {
            k: result[k]
            for k in ("state", "can_collect", "new_collection", "database_write")
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
