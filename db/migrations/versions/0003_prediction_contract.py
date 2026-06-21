"""prediction / recommendation minimal contract (US4)

Revision ID: 0003_prediction_contract
Revises: 0002_ingestion_id_schema
Create Date: 2026-06-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from horseracing_db.constraints import ADOPTION_STATUS, BET_TYPE, PROB_MONOTONIC
from horseracing_db.enums import AdoptionStatus
from horseracing_db.sql.triggers import create_updated_at_trigger, drop_updated_at_trigger

revision = "0003_prediction_contract"
down_revision = "0002_ingestion_id_schema"
branch_labels = None
depends_on = None

_TABLES = (
    "model_versions",
    "prediction_runs",
    "race_predictions",
    "feature_snapshots",
    "recommendations",
)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "model_versions",
        sa.Column("model_version", sa.Text(), primary_key=True),
        sa.Column("model_family", sa.Text()),
        sa.Column("feature_version", sa.Text()),
        sa.Column("label_schema", sa.Text(), nullable=False, server_default="win_top2_top3"),
        sa.Column("adoption_status", sa.Text(), nullable=False, server_default=AdoptionStatus.CANDIDATE),
        sa.Column("metrics_summary", JSONB()),
        sa.Column("weights_uri", sa.Text()),
        sa.Column("calibrator_uri", sa.Text()),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(ADOPTION_STATUS, name="ck_model_versions_adoption_status"),
    )

    op.create_table(
        "prediction_runs",
        sa.Column("prediction_run_id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), nullable=False),
        sa.Column("model_version", sa.Text(), sa.ForeignKey("model_versions.model_version"), nullable=False),
        sa.Column("logic_version", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        *_timestamps(),
    )
    op.create_index("ix_prediction_runs_race_id", "prediction_runs", ["race_id"])

    op.create_table(
        "race_predictions",
        sa.Column("prediction_run_id", sa.Uuid(), sa.ForeignKey("prediction_runs.prediction_run_id"), primary_key=True),
        sa.Column("horse_id", sa.Text(), sa.ForeignKey("horses.horse_id"), primary_key=True),
        sa.Column("win_prob", sa.Numeric()),
        sa.Column("top2_prob", sa.Numeric()),
        sa.Column("top3_prob", sa.Numeric()),
        *_timestamps(),
        sa.CheckConstraint(PROB_MONOTONIC, name="ck_race_predictions_prob_monotonic"),
    )

    op.create_table(
        "feature_snapshots",
        sa.Column("prediction_run_id", sa.Uuid(), sa.ForeignKey("prediction_runs.prediction_run_id"), primary_key=True),
        sa.Column("horse_id", sa.Text(), sa.ForeignKey("horses.horse_id"), primary_key=True),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("features", JSONB(), nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "recommendations",
        sa.Column("recommendation_id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("prediction_run_id", sa.Uuid(), sa.ForeignKey("prediction_runs.prediction_run_id"), nullable=False),
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), nullable=False),
        sa.Column("bet_type", sa.Text(), nullable=False),
        sa.Column("selection", JSONB(), nullable=False),
        sa.Column("market_odds_used", sa.Numeric()),
        sa.Column("estimated_market_odds_used", sa.Numeric()),
        sa.Column("is_estimated_odds", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pseudo_odds", sa.Numeric()),
        sa.Column("pseudo_roi", sa.Numeric()),
        sa.Column("logic_version", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        *_timestamps(),
        sa.CheckConstraint(BET_TYPE, name="ck_recommendations_bet_type"),
    )

    for table in _TABLES:
        op.execute(create_updated_at_trigger(table))


def downgrade() -> None:
    for table in _TABLES:
        op.execute(drop_updated_at_trigger(table))
    op.drop_table("recommendations")
    op.drop_table("feature_snapshots")
    op.drop_table("race_predictions")
    op.drop_index("ix_prediction_runs_race_id", table_name="prediction_runs")
    op.drop_table("prediction_runs")
    op.drop_table("model_versions")
