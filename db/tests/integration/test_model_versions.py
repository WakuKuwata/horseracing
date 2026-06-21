"""US4 / FR-017: model_versions adoption_status default and CHECK."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion

pytestmark = pytest.mark.integration


def test_default_adoption_status_is_candidate(session):
    mv = ModelVersion(model_version="m-001", model_family="lightgbm")
    session.add(mv)
    session.flush()
    session.refresh(mv)
    assert mv.adoption_status == AdoptionStatus.CANDIDATE
    assert mv.label_schema == "win_top2_top3"


@pytest.mark.parametrize("status", [AdoptionStatus.ACTIVE, AdoptionStatus.RETIRED])
def test_valid_adoption_status_accepted(session, status):
    session.add(ModelVersion(model_version=f"m-{status}", adoption_status=status))
    session.flush()


def test_invalid_adoption_status_rejected(session):
    session.add(ModelVersion(model_version="m-bad", adoption_status="promoted"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
