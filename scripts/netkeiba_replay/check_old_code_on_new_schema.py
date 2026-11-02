"""The OLD scrape code against a database the NEW tree migrated to head (0024).

Covers the minutes between the migration and the merge, and a rollback that reverts the code but
keeps the schema (manual.md: rollback = stop JRA fetching, keep the schema). The result must be
the same as the old code on its own 0022 database.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from compare import compare  # noqa: E402


def main() -> int:
    old_root, new_root, events, work = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
    work.mkdir(parents=True, exist_ok=True)
    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        admin = pg.get_connection_url()
        eng = create_engine(admin, isolation_level="AUTOCOMMIT")
        runs = {"old_on_0022": old_root, "old_on_0024": new_root}
        for db, mroot in runs.items():
            with eng.connect() as c:
                c.execute(text(f"CREATE DATABASE {db}"))
            url = admin.rsplit("/", 1)[0] + f"/{db}"
            p = subprocess.run(
                [
                    sys.executable,
                    str(HERE / "run_replay.py"),
                    "--code-root",
                    old_root,
                    "--migrate-root",
                    mroot,
                    "--db-url",
                    url,
                    "--events",
                    events,
                    "--out",
                    str(work / f"state-{db}.json"),
                    "--order",
                    "dup",
                ],
                capture_output=True,
                text=True,
            )
            print(db, p.returncode, p.stdout.strip()[-300:], p.stderr.strip()[-300:])
    a = json.loads((work / "state-old_on_0022.json").read_text())
    b = json.loads((work / "state-old_on_0024.json").read_text())
    rep = compare(a, b)
    bad = {
        t: v
        for t, v in rep["tables"].items()
        if v["only_old"] or v["only_new"] or v["rows_differing"]
    }
    print(
        json.dumps(
            {
                "differing_tables": bad,
                "jobs": rep["ingestion_jobs"],
                "crashes": [a["_meta"]["crashes"], b["_meta"]["crashes"]],
                "heads": [a["_meta"]["head"], b["_meta"]["head"]],
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0 if not bad and not rep["ingestion_jobs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
