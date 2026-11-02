"""Run the old-vs-new netkeiba replay parity check end to end (176 pre-merge).

    uv run --project scrape python scripts/netkeiba_replay/orchestrate.py \
        --old-root DIR --new-root DIR --events E.jsonl.gz --work DIR [--orders chrono,dup,shuffle]

Starts a throwaway PostgreSQL (testcontainers), creates one database per (code, order), runs
``run_replay.py`` for each in a subprocess with the same interpreter, and writes
``<work>/report-<order>.json`` comparing old and new. Never touches the production database.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from compare import compare  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-root", required=True)
    ap.add_argument("--new-root", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--orders", default="chrono,dup,shuffle")
    a = ap.parse_args(argv)
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        admin_url = pg.get_connection_url()
        eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        procs = []
        for order in a.orders.split(","):
            for tag, root in (("old", a.old_root), ("new", a.new_root)):
                db = f"{tag}_{order}"
                with eng.connect() as c:
                    c.execute(text(f"DROP DATABASE IF EXISTS {db}"))
                    c.execute(text(f"CREATE DATABASE {db}"))
                url = admin_url.rsplit("/", 1)[0] + f"/{db}"
                out = work / f"state-{tag}-{order}.json"
                log = open(work / f"replay-{tag}-{order}.log", "w")
                procs.append(
                    (
                        order,
                        tag,
                        out,
                        subprocess.Popen(
                            [
                                sys.executable,
                                str(HERE / "run_replay.py"),
                                "--code-root",
                                root,
                                "--db-url",
                                url,
                                "--events",
                                a.events,
                                "--out",
                                str(out),
                                "--order",
                                order,
                            ],
                            stdout=log,
                            stderr=subprocess.STDOUT,
                        ),
                    )
                )
        for order, tag, _out, p in procs:
            rc = p.wait()
            print(f"{tag}/{order}: rc={rc}", flush=True)
            if rc != 0:
                return rc
    for order in a.orders.split(","):
        old = json.loads((work / f"state-old-{order}.json").read_text(encoding="utf-8"))
        new = json.loads((work / f"state-new-{order}.json").read_text(encoding="utf-8"))
        rep = compare(old, new)
        (work / f"report-{order}.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        summary = {
            t: {k: v[k] for k in ("rows_old", "rows_new", "only_old", "only_new", "rows_differing")}
            | {"columns": {c: d["n"] for c, d in v["columns"].items()}}
            for t, v in rep["tables"].items()
        }
        print(
            order,
            json.dumps(
                {
                    "tables": summary,
                    "jobs": rep["ingestion_jobs"],
                    "crashes": [rep["meta"]["old"]["crashes"], rep["meta"]["new"]["crashes"]],
                },
                ensure_ascii=False,
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
