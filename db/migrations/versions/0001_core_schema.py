"""core schema (US1/US2): races, horses, jockeys, trainers, race_horses, race_results

Revision ID: 0001_core_schema
Revises:
Create Date: 2026-06-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

from horseracing_db.constraints import (
    ENTRY_STATUS,
    FINISH_ORDER_WHEN_FINISHED,
    RACE_ID_FORMAT,
    RACE_NUMBER_RANGE,
    RESULT_STATUS,
)
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.sql.triggers import (
    CREATE_SET_UPDATED_AT_FUNCTION,
    DROP_SET_UPDATED_AT_FUNCTION,
    create_updated_at_trigger,
    drop_updated_at_trigger,
)

revision = "0001_core_schema"
down_revision = None
branch_labels = None
depends_on = None

_CORE_TABLES = ("races", "horses", "jockeys", "trainers", "race_horses", "race_results")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.execute(CREATE_SET_UPDATED_AT_FUNCTION)

    op.create_table(
        "races",
        sa.Column("race_id", sa.Text(), primary_key=True),
        sa.Column("race_name", sa.Text()),
        sa.Column("race_name_short", sa.Text()),
        sa.Column("venue_code", sa.Text()),
        sa.Column("distance", sa.Integer()),
        sa.Column("track_type", sa.Text()),
        sa.Column("race_status", sa.Text()),
        sa.Column("race_date", sa.Date()),
        sa.Column("race_number", sa.Integer()),
        sa.Column("grade", sa.Text()),
        sa.Column("race_class", sa.Text()),
        sa.Column("weather", sa.Text()),
        sa.Column("going", sa.Text()),
        sa.Column("post_time", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(RACE_ID_FORMAT, name="ck_races_race_id_format"),
        sa.CheckConstraint(RACE_NUMBER_RANGE, name="ck_races_race_number_range"),
    )
    op.create_index("ix_races_race_date_post_time", "races", ["race_date", "post_time"])

    op.create_table(
        "horses",
        sa.Column("horse_id", sa.Text(), primary_key=True),
        sa.Column("horse_name", sa.Text()),
        sa.Column("sex", sa.Text()),
        sa.Column("birth_year", sa.Integer()),
        sa.Column("sire_id", sa.Text()),
        sa.Column("dam_id", sa.Text()),
        sa.Column("damsire_id", sa.Text()),
        sa.Column("sire_name", sa.Text()),
        sa.Column("dam_name", sa.Text()),
        sa.Column("damsire_name", sa.Text()),
        sa.Column("data_source", sa.Text()),
        *_timestamps(),
    )

    op.create_table(
        "jockeys",
        sa.Column("jockey_id", sa.Text(), primary_key=True),
        sa.Column("jockey_name", sa.Text()),
        *_timestamps(),
    )

    op.create_table(
        "trainers",
        sa.Column("trainer_id", sa.Text(), primary_key=True),
        sa.Column("trainer_name", sa.Text()),
        *_timestamps(),
    )

    op.create_table(
        "race_horses",
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), primary_key=True),
        sa.Column("horse_id", sa.Text(), sa.ForeignKey("horses.horse_id"), primary_key=True),
        sa.Column("sex", sa.Text()),
        sa.Column("age", sa.Integer()),
        sa.Column("frame", sa.Integer()),
        sa.Column("horse_number", sa.Integer()),
        sa.Column("jockey_id", sa.Text(), sa.ForeignKey("jockeys.jockey_id")),
        sa.Column("trainer_id", sa.Text(), sa.ForeignKey("trainers.trainer_id")),
        sa.Column("weight", sa.Integer()),
        sa.Column("weight_diff", sa.Integer()),
        sa.Column("odds", sa.Numeric()),
        sa.Column("popularity", sa.Integer()),
        sa.Column("running_style", sa.Text()),
        sa.Column("jockey_weight", sa.Numeric()),
        sa.Column(
            "entry_status",
            sa.Text(),
            nullable=False,
            server_default=EntryStatus.STARTED,
        ),
        *_timestamps(),
        sa.CheckConstraint(ENTRY_STATUS, name="ck_race_horses_entry_status"),
    )

    op.create_table(
        "race_results",
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), primary_key=True),
        sa.Column("horse_id", sa.Text(), sa.ForeignKey("horses.horse_id"), primary_key=True),
        sa.Column("finish_order", sa.Integer()),
        sa.Column("finish_time", sa.Interval()),
        sa.Column("finish_time_diff", sa.Interval()),
        sa.Column("corner_orders", ARRAY(sa.Text())),
        sa.Column("last_3f", sa.Numeric()),
        sa.Column(
            "result_status",
            sa.Text(),
            nullable=False,
            server_default=ResultStatus.FINISHED,
        ),
        *_timestamps(),
        sa.CheckConstraint(RESULT_STATUS, name="ck_race_results_result_status"),
        sa.CheckConstraint(
            FINISH_ORDER_WHEN_FINISHED,
            name="ck_race_results_finish_order_when_finished",
        ),
    )

    for table in _CORE_TABLES:
        op.execute(create_updated_at_trigger(table))


def downgrade() -> None:
    for table in _CORE_TABLES:
        op.execute(drop_updated_at_trigger(table))
    op.drop_table("race_results")
    op.drop_table("race_horses")
    op.drop_table("trainers")
    op.drop_table("jockeys")
    op.drop_table("horses")
    op.drop_index("ix_races_race_date_post_time", table_name="races")
    op.drop_table("races")
    op.execute(DROP_SET_UPDATED_AT_FUNCTION)
