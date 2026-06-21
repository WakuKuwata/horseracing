"""Polish / SC-005: migrations apply and roll back idempotently."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

pytestmark = pytest.mark.integration

ALL_TABLES = {
    "races", "horses", "jockeys", "trainers", "race_horses", "race_results",
    "id_mappings", "ingestion_jobs",
    "model_versions", "prediction_runs", "race_predictions", "feature_snapshots",
    "recommendations",
}


def test_upgrade_downgrade_roundtrip(alembic_cfg, database_url, _migrated):
    # head -> base -> head -> base -> head, all clean.
    command.downgrade(alembic_cfg, "base")
    eng = create_engine(database_url)
    try:
        remaining = set(inspect(eng).get_table_names()) - {"alembic_version"}
        assert remaining == set(), f"downgrade base left tables: {remaining}"

        command.upgrade(alembic_cfg, "head")
        names = set(inspect(eng).get_table_names())
        assert ALL_TABLES.issubset(names)

        # second round confirms idempotency
        command.downgrade(alembic_cfg, "base")
        command.upgrade(alembic_cfg, "head")
        names = set(inspect(eng).get_table_names())
        assert ALL_TABLES.issubset(names)
    finally:
        eng.dispose()
