"""jrdb source + jrdb_files (Feature 178, Phase 3)

Revision ID: 0025_jrdb_source
Revises: 0024_jra_track_conditions
Create Date: 2026-11-08

1. ``jrdb`` becomes a legal ``source`` on ingestion_jobs / id_mappings / exotic_odds / race_laps
   and on the two 176 provenance tables (CHECK replacement, no data change), and
   ``jrdb_result`` on official_win_payouts (the PAGE kind, next to netkeiba_result /
   jra_official_result). Defaults are unchanged.
2. ``jrdb_files``: one row per JRDB file version (content sha) with per-race apply status — the
   idempotency unit of the JRDB route (research D9). No member-only content: the URL is stored
   as a sha, the text lives under artifacts/.

Existing table columns are not touched (SC-008). Apply after the last race of the day with a
short lock_timeout — the CHECK replacements take an ACCESS EXCLUSIVE lock on seven tables
(rollout R2 rehearses this on a 0024 copy first).

Downgrade refuses once any ``jrdb`` / ``jrdb_result`` row or any ``jrdb_files`` row exists: the
old CHECK could not be restored over them and the file register has no other home. The supported
retreat is "jrdb_enabled=false, keep the schema".
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from horseracing_db.sql.triggers import create_updated_at_trigger, drop_updated_at_trigger

revision = "0025_jrdb_source"
down_revision = "0024_jra_track_conditions"
branch_labels = None
depends_on = None

# Literal, not imported: a migration is a historical record (0004/0015/0020/0023 precedent).
_OLD_SOURCE = "source IN ('jra_van', 'netkeiba', 'jra_official')"
_NEW_SOURCE = "source IN ('jra_van', 'netkeiba', 'jra_official', 'jrdb')"
_OLD_PAYOUT_SOURCE = "source IN ('netkeiba_result', 'jra_official_result')"
_NEW_PAYOUT_SOURCE = "source IN ('netkeiba_result', 'jra_official_result', 'jrdb_result')"
# 0002/0005/0007 created these inside create_table WITHOUT op.f(), so the naming convention made
# "ck_ingestion_jobs_ck_ingestion_jobs_source" in the database; drop/create with the plain name
# reproduces that exact name (0023 did the same). The 0020/0023 tables used op.f() → literal.
_PLAIN_SOURCE_CHECKS = (
    ("ck_ingestion_jobs_source", "ingestion_jobs"),
    ("ck_id_mappings_source", "id_mappings"),
    ("ck_exotic_odds_source", "exotic_odds"),
    ("ck_race_laps_source", "race_laps"),
)
_OPF_SOURCE_CHECKS = (
    ("ck_race_horse_field_provenance_source", "race_horse_field_provenance"),
    ("ck_race_field_provenance_source", "race_field_provenance"),
)
_T = "jrdb_files"
_KINDS = (
    "('BAC', 'KYI', 'KYH', 'KYG', 'UKC', 'KZA', 'KSA', 'CZA', 'CSA', 'TYB', 'HJC', 'SED', 'SEC', "
    "'SRB', 'SKB', 'OZ', 'OW', 'OU', 'OT', 'OV', 'CYB', 'CHA', 'KAB', 'MZA', 'MSA')"
)
_VARIANTS = "('daily', 'venue_e', 'venue_w', 'venue_l', 'archive')"
_STATUSES = "('fetched', 'partially_applied', 'applied', 'superseded', 'rejected', 'held')"


def _replace_checks(new_source: str, new_payout: str) -> None:
    for name, table in _PLAIN_SOURCE_CHECKS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, new_source)
    for name, table in _OPF_SOURCE_CHECKS:
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, new_source)
    op.drop_constraint(
        op.f("ck_official_win_payouts_source"), "official_win_payouts", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_official_win_payouts_source"), "official_win_payouts", new_payout
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    _replace_checks(_NEW_SOURCE, _NEW_PAYOUT_SOURCE)

    op.create_table(
        _T,
        sa.Column(
            "jrdb_file_id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("data_date", sa.Date(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("record_length", sa.Integer(), nullable=False),
        sa.Column("source_url_sha256", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'fetched'")),
        sa.Column(
            "race_status",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
        sa.PrimaryKeyConstraint("jrdb_file_id", name=op.f(f"pk_{_T}")),
        sa.UniqueConstraint(
            "kind",
            "data_date",
            "variant",
            "sha256",
            name=op.f("uq_jrdb_files_kind_date_variant_sha"),
        ),
        sa.CheckConstraint(f"kind IN {_KINDS}", name=op.f("ck_jrdb_files_kind")),
        sa.CheckConstraint(f"variant IN {_VARIANTS}", name=op.f("ck_jrdb_files_variant")),
        sa.CheckConstraint(f"status IN {_STATUSES}", name=op.f("ck_jrdb_files_status")),
    )
    op.create_index("ix_jrdb_files_kind_date", _T, ["kind", "data_date"])
    op.create_index("ix_jrdb_files_status", _T, ["status"])
    op.execute(create_updated_at_trigger(_T))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    conn = op.get_bind()
    checks = [
        ("ingestion_jobs", "source = 'jrdb'"),
        ("id_mappings", "source = 'jrdb'"),
        ("exotic_odds", "source = 'jrdb'"),
        ("race_laps", "source = 'jrdb'"),
        ("race_horse_field_provenance", "source = 'jrdb'"),
        ("race_field_provenance", "source = 'jrdb'"),
        ("official_win_payouts", "source = 'jrdb_result'"),
        (_T, "TRUE"),
    ]
    for table, where in checks:
        hit = conn.execute(sa.text(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1")).first()
        if hit is not None:
            raise RuntimeError(
                f"downgrade refused: {table} has rows written by the JRDB route; the old CHECK "
                "cannot be restored over them. Set jrdb_enabled=false instead."
            )
    op.execute(drop_updated_at_trigger(_T))
    op.drop_index("ix_jrdb_files_status", table_name=_T)
    op.drop_index("ix_jrdb_files_kind_date", table_name=_T)
    op.drop_table(_T)
    _replace_checks(_OLD_SOURCE, _OLD_PAYOUT_SOURCE)
