"""Feature 057: migration 0011 — model_versions.display_name / purpose (nullable, display-only)."""

from __future__ import annotations

import pytest

from horseracing_db.models import ModelVersion

pytestmark = pytest.mark.integration


def test_purpose_columns_exist_nullable_and_roundtrip(session):
    # display_name / purpose accept NULL (未設定) and round-trip values; label_schema unaffected.
    session.add(ModelVersion(model_version="m-057A", model_family="lightgbm",
                             display_name="意思決定支援モデル", purpose="市場から独立した予測"))
    session.add(ModelVersion(model_version="m-057B", model_family="lightgbm"))  # both NULL ok
    session.commit()

    a = session.get(ModelVersion, "m-057A")
    assert (a.display_name, a.purpose) == ("意思決定支援モデル", "市場から独立した予測")
    assert a.label_schema == "win_top2_top3"  # existing column unaffected
    b = session.get(ModelVersion, "m-057B")
    assert b.display_name is None and b.purpose is None
