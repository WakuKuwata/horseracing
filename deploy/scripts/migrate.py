"""Feature 018 migration one-shot (FR-005/FR-008). Run by the compose `migrate` service using the
API image's venv (alembic + sqlalchemy) — no psql needed. Mounted at /app/deploy/scripts.

Steps (as the OWNER role): (1) alembic upgrade head, (2) idempotently provision the read-only
serving role with SELECT-only privileges. Fail-closed: any error exits non-zero, so the API service
(depends_on: migrate condition: service_completed_successfully) does not start.
"""

from __future__ import annotations

import os
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def main() -> int:
    owner_url = os.environ["DATABASE_URL_OWNER"]
    ro_user = os.environ["APP_RO_USER"]
    ro_pw = os.environ["APP_RO_PASSWORD"]
    db_name = os.environ["POSTGRES_DB"]
    if not ro_user.isidentifier():
        print(f"[migrate] invalid APP_RO_USER: {ro_user!r}", file=sys.stderr)
        return 2

    print("[migrate] alembic upgrade head (owner)")
    cfg = Config("/app/db/alembic.ini")
    cfg.set_main_option(
        "script_location", os.environ.get("ALEMBIC_SCRIPT_LOCATION", "/app/db/migrations")
    )
    os.environ["DATABASE_URL"] = owner_url  # alembic env.py reads DATABASE_URL
    command.upgrade(cfg, "head")

    print(f"[migrate] provisioning read-only role {ro_user}")
    u = f'"{ro_user}"'                 # validated identifier (isidentifier above)
    d = '"' + db_name.replace('"', '""') + '"'
    pw = ro_pw.replace("'", "''")
    eng = create_engine(owner_url)
    with eng.begin() as c:
        exists = c.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :u"), {"u": ro_user}
        ).scalar()
        c.execute(text(f"{'ALTER' if exists else 'CREATE'} ROLE {u} LOGIN PASSWORD '{pw}'"))
        c.execute(text(f"GRANT CONNECT ON DATABASE {d} TO {u}"))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {u}"))
        c.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {u}"))
        c.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {u}"))
        c.execute(
            text(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM {u}")
        )
    eng.dispose()
    print("[migrate] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
