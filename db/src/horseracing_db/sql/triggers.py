"""``updated_at`` auto-update trigger DDL helpers (research R5).

A single ``set_updated_at()`` function is created once (in migration 0001) and a
``BEFORE UPDATE`` trigger is attached to every table so the audit column is
writer-independent.
"""

from __future__ import annotations

CREATE_SET_UPDATED_AT_FUNCTION = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

DROP_SET_UPDATED_AT_FUNCTION = "DROP FUNCTION IF EXISTS set_updated_at();"


def _trigger_name(table: str) -> str:
    return f"trg_{table}_set_updated_at"


def create_updated_at_trigger(table: str) -> str:
    return (
        f"CREATE TRIGGER {_trigger_name(table)} "
        f"BEFORE UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def drop_updated_at_trigger(table: str) -> str:
    return f"DROP TRIGGER IF EXISTS {_trigger_name(table)} ON {table};"
