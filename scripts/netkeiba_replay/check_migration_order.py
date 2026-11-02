"""What the NEW scrape code does against a database at the OLD head (0022) vs at 0023/0024.

The running ops imports the live checkout's code on every job, so after a merge the new code
meets the production database immediately. This shows whether the migration has to come first.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

HERE = Path(__file__).resolve().parent


def main(argv=None) -> int:
    new_root, events, work = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    out = {}
    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        admin = pg.get_connection_url()
        eng = create_engine(admin, isolation_level="AUTOCOMMIT")
        for rev in ("0022_decision_quotes", "0023_jra_official_source", "head"):
            db = "m_" + rev.split("_")[0]
            with eng.connect() as c:
                c.execute(text(f"CREATE DATABASE {db}"))
            url = admin.rsplit("/", 1)[0] + f"/{db}"
            state = work / f"state-{db}.json"
            p = subprocess.run(
                [
                    sys.executable,
                    str(HERE / "run_replay.py"),
                    "--code-root",
                    new_root,
                    "--db-url",
                    url,
                    "--events",
                    events,
                    "--out",
                    str(state),
                    "--revision",
                    rev,
                    "--limit",
                    "60",
                ],
                capture_output=True,
                text=True,
            )
            doc = json.loads(state.read_text()) if state.exists() else {}
            out[rev] = {
                "rc": p.returncode,
                "jobs": doc.get("ingestion_jobs"),
                "crashes": doc.get("_meta", {}).get("crashes"),
                "race_horses": len(doc.get("race_horses", {}).get("rows", [])),
                "stderr_tail": p.stderr.strip().splitlines()[-3:],
            }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
