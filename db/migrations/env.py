"""Alembic environment.

Resolves the database URL from the ``DATABASE_URL`` environment variable so the
same migrations run against local Postgres and the testcontainers instance.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import the package metadata so autogenerate (if ever used) and tooling can see it.
from horseracing_db.base import Base
import horseracing_db.models  # noqa: F401  (ensure all models are registered)

config = context.config

db_url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
if not db_url:
    raise RuntimeError("DATABASE_URL is not set and sqlalchemy.url is empty in alembic.ini")
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
