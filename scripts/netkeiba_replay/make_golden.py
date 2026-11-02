"""Write the regression goldens: the OLD code's final state for the committed four-race fixture.

    uv run --project scrape python scripts/netkeiba_replay/make_golden.py OLD_ROOT [FULL_EVENTS]

OLD_ROOT is the pre-176 tree (``git archive 8e373520 db scrape``). With FULL_EVENTS (the same races
before scripts/styles were stripped) it also checks that stripping did not change the outcome.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FIX = REPO / "scrape" / "tests" / "fixtures" / "netkeiba_replay"
sys.path.insert(0, str(HERE))
from compare import compare  # noqa: E402


def _run(admin: str, eng, db: str, root: str, events: Path, out: Path, order: str) -> dict:
    with eng.connect() as c:
        c.execute(text(f"CREATE DATABASE {db}"))
    url = admin.rsplit("/", 1)[0] + f"/{db}"
    subprocess.run(
        [
            sys.executable,
            str(HERE / "run_replay.py"),
            "--code-root",
            root,
            "--db-url",
            url,
            "--events",
            str(events),
            "--out",
            str(out),
            "--order",
            order,
        ],
        check=True,
    )
    return json.loads(out.read_text(encoding="utf-8"))


def main() -> int:
    old_root = sys.argv[1]
    full = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        admin = pg.get_connection_url()
        eng = create_engine(admin, isolation_level="AUTOCOMMIT")
        for order in ("chrono", "dup", "shuffle"):
            state = _run(
                admin,
                eng,
                f"g_{order}",
                old_root,
                FIX / "events.jsonl.gz",
                FIX / f"golden-old-{order}.json",
                order,
            )
            state["_meta"].pop("code_root", None)
            (FIX / f"golden-old-{order}.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8"
            )
            print(order, state["_meta"])
        if full is not None:
            tmp = Path("/tmp") / "golden-full-chrono.json"
            a = _run(admin, eng, "g_full", old_root, full, tmp, "chrono")
            b = json.loads((FIX / "golden-old-chrono.json").read_text(encoding="utf-8"))
            rep = compare(a, b)
            bad = {
                t: v
                for t, v in rep["tables"].items()
                if v["only_old"] or v["only_new"] or v["rows_differing"]
            }
            print("stripped == full:", not bad and not rep["ingestion_jobs"], json.dumps(bad)[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
