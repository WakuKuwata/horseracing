"""Leak guards for the margin-teacher label-side auxiliary columns."""

from __future__ import annotations

import pandas as pd
from horseracing_features.registry import model_input_features

from horseracing_training.artifacts import feature_hash
from horseracing_training.dataset import (
    MARGIN_SCALE_S2,
    MARGIN_SCALE_S3,
    TrainingMatrix,
    _margin_stage_scales,
)

MARGIN_AUX_COLUMNS = (MARGIN_SCALE_S2, MARGIN_SCALE_S3)


def test_margin_aux_columns_are_not_registered_model_inputs() -> None:
    feature_cols = model_input_features()

    assert all(column not in feature_cols for column in MARGIN_AUX_COLUMNS)


def test_margin_aux_columns_do_not_change_feature_hash_or_snapshot_source() -> None:
    """Serving snapshots iterate ``model.feature_cols`` only.

    Training copies ``TrainingMatrix.feature_cols`` into that model contract. Keeping
    these label-side columns in ``frame`` but out of ``feature_cols`` therefore proves
    their exclusion from both the training feature hash and persisted snapshots.
    """
    registry_cols = model_input_features()
    registry_hash = feature_hash(registry_cols)
    frame = pd.DataFrame(
        {
            MARGIN_SCALE_S2: pd.Series([0.25], dtype="float64"),
            MARGIN_SCALE_S3: pd.Series([1.0], dtype="float64"),
        }
    )
    matrix = TrainingMatrix(
        frame=frame,
        feature_cols=list(registry_cols),
        categorical_cols=[],
    )

    assert all(column in matrix.frame for column in MARGIN_AUX_COLUMNS)
    assert all(column not in matrix.feature_cols for column in MARGIN_AUX_COLUMNS)
    assert feature_hash(matrix.feature_cols) == registry_hash


class _Rows:
    def all(self) -> list[tuple[str, int, float | None]]:
        return [
            ("race-a", 1, 0.3),
            ("race-a", 2, 0.1),
            ("race-a", 3, None),
            ("race-b", 2, -0.1),
            ("race-b", 3, 0.4),
        ]


class _Session:
    statement: str = ""

    def execute(self, statement: object) -> _Rows:
        self.statement = str(statement)
        return _Rows()


def test_margin_scale_sql_keeps_the_full_finished_window() -> None:
    session = _Session()

    scales, audit = _margin_stage_scales(session)  # type: ignore[arg-type]
    sql = " ".join(session.statement.split())

    assert "WHERE result_status = 'finished'" in sql
    assert "finish_time IS NOT NULL" not in sql
    assert sql.index("lead(finish_time)") < sql.index("WHERE finish_order <= 3")
    assert scales == {
        "race-a": (0.5, 1.0),
        "race-b": (0.25, 1.0),
    }
    # INV-MT9 の分計の材料: gap の定義可否はこの関数でしか数えられない
    assert audit == {
        "s2_defined": 2,
        "s2_undefined": 0,
        "s3_defined": 1,
        "s3_undefined": 1,
        "races_in_map": 2,
    }
