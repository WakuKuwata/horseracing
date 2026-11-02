"""jra_official source + field-group provenance tables (Feature 176, Phase 1)

Revision ID: 0023_jra_official_source
Revises: 0022_decision_quotes
Create Date: 2026-11-02

Two things, deliberately in ONE migration because the second is useless without the first and the
first is unsafe without the second (research D18):

1. ``jra_official`` becomes a legal ``source`` on ingestion_jobs / id_mappings / exotic_odds /
   race_laps (CHECK replacement, no data change), and ``jra_official_result`` on
   official_win_payouts. Defaults are unchanged.
2. ``race_horse_field_provenance`` (race × horse × field group) and ``race_field_provenance``
   (race × field group) record which route's value is CURRENTLY adopted, when it was fetched and by
   which job. No backfill: existing rows get no provenance row and must not be assumed netkeiba.

Existing table columns are not touched (SC-009). Apply after the last race of the day, with a
short lock_timeout — the CHECK replacements take an ACCESS EXCLUSIVE lock on five tables.

Downgrade refuses to run once any ``jra_official`` / ``jra_official_result`` row exists: the old
CHECK could not be restored over it. The supported retreat is "stop the JRA route, keep the
schema".
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from horseracing_db.sql.triggers import create_updated_at_trigger, drop_updated_at_trigger

revision = "0023_jra_official_source"
down_revision = "0022_decision_quotes"
branch_labels = None
depends_on = None

# Literal, not imported: a migration is a historical record (0004/0015/0020 precedent).
_OLD_SOURCE = "source IN ('jra_van', 'netkeiba')"
_NEW_SOURCE = "source IN ('jra_van', 'netkeiba', 'jra_official')"
_OLD_PAYOUT_SOURCE = "source IN ('netkeiba_result')"
_NEW_PAYOUT_SOURCE = "source IN ('netkeiba_result', 'jra_official_result')"
# 0002/0005/0007 created these inside create_table WITHOUT op.f(), so the metadata naming
# convention turned "ck_ingestion_jobs_source" into "ck_ingestion_jobs_ck_ingestion_jobs_source"
# in the database. Dropping/creating with the same plain name reproduces that exact name;
# official_win_payouts (0020) used op.f() and is handled literally below.
_SOURCE_CHECKS = (
    ("ck_ingestion_jobs_source", "ingestion_jobs"),
    ("ck_id_mappings_source", "id_mappings"),
    ("ck_exotic_odds_source", "exotic_odds"),
    ("ck_race_laps_source", "race_laps"),
)
_HORSE_GROUPS = "('entry_static', 'jockey', 'trainer', 'body_weight', 'entry_status')"
_RACE_GROUPS = (
    "('race_static', 'post_time', 'win_odds', 'place_odds', 'results', 'laps', "
    "'win_payouts', 'exotic_payouts')"
)
_T_HORSE = "race_horse_field_provenance"
_T_RACE = "race_field_provenance"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for name, table in _SOURCE_CHECKS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _NEW_SOURCE)
    op.drop_constraint(
        op.f("ck_official_win_payouts_source"), "official_win_payouts", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_official_win_payouts_source"), "official_win_payouts", _NEW_PAYOUT_SOURCE
    )

    op.create_table(
        _T_HORSE,
        sa.Column("race_id", sa.Text(), nullable=False),
        sa.Column("horse_id", sa.Text(), nullable=False),
        sa.Column("field_group", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingestion_job_id",
            sa.Uuid(),
            sa.ForeignKey("ingestion_jobs.ingestion_job_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("race_id", "horse_id", "field_group", name=op.f(f"pk_{_T_HORSE}")),
        sa.ForeignKeyConstraint(
            ["race_id", "horse_id"],
            ["race_horses.race_id", "race_horses.horse_id"],
            name=op.f("fk_race_horse_field_provenance_race_horse"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"field_group IN {_HORSE_GROUPS}", name=op.f("ck_race_horse_field_provenance_group")
        ),
        sa.CheckConstraint(_NEW_SOURCE, name=op.f("ck_race_horse_field_provenance_source")),
    )
    op.create_index("ix_race_horse_field_provenance_race", _T_HORSE, ["race_id"])
    op.execute(create_updated_at_trigger(_T_HORSE))

    op.create_table(
        _T_RACE,
        sa.Column(
            "race_id", sa.Text(), sa.ForeignKey("races.race_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("field_group", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("official_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingestion_job_id",
            sa.Uuid(),
            sa.ForeignKey("ingestion_jobs.ingestion_job_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("race_id", "field_group", name=op.f(f"pk_{_T_RACE}")),
        sa.CheckConstraint(
            f"field_group IN {_RACE_GROUPS}", name=op.f("ck_race_field_provenance_group")
        ),
        sa.CheckConstraint(_NEW_SOURCE, name=op.f("ck_race_field_provenance_source")),
        sa.CheckConstraint(
            "official_at IS NULL OR field_group IN ('win_odds', 'place_odds')",
            name=op.f("ck_race_field_provenance_official_at_odds_only"),
        ),
    )
    op.execute(create_updated_at_trigger(_T_RACE))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    conn = op.get_bind()
    checks = [
        ("ingestion_jobs", "source = 'jra_official'"),
        ("id_mappings", "source = 'jra_official'"),
        ("exotic_odds", "source = 'jra_official'"),
        ("race_laps", "source = 'jra_official'"),
        ("official_win_payouts", "source = 'jra_official_result'"),
    ]
    for table, where in checks:
        hit = conn.execute(sa.text(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1")).first()
        if hit is not None:
            raise RuntimeError(
                f"downgrade refused: {table} has rows written by the JRA official route; "
                "the old CHECK cannot be restored over them. Stop the JRA route instead."
            )
    op.execute(drop_updated_at_trigger(_T_RACE))
    op.drop_table(_T_RACE)
    op.execute(drop_updated_at_trigger(_T_HORSE))
    op.drop_index("ix_race_horse_field_provenance_race", table_name=_T_HORSE)
    op.drop_table(_T_HORSE)
    op.drop_constraint(
        op.f("ck_official_win_payouts_source"), "official_win_payouts", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_official_win_payouts_source"), "official_win_payouts", _OLD_PAYOUT_SOURCE
    )
    for name, table in _SOURCE_CHECKS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _OLD_SOURCE)
