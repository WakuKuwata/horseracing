"""Feature 057: set-model-label CLI — write display_name/purpose, empty→NULL, omit→unchanged,
adoption_status never touched (FR-009), missing model → error."""

from __future__ import annotations

import pytest
from horseracing_db.enums import AdoptionStatus
from horseracing_db.models import ModelVersion

from horseracing_training.cli import main

pytestmark = pytest.mark.integration


def _seed(session, mv="m1"):
    session.merge(ModelVersion(model_version=mv, model_family="t",
                               adoption_status=AdoptionStatus.CANDIDATE))
    session.commit()


def test_set_then_overwrite_empty_and_omit(session, database_url):
    _seed(session)
    rc = main(["set-model-label", "--model-version", "m1",
               "--display-name", "意思決定支援モデル", "--purpose", "独立予測",
               "--database-url", database_url])
    assert rc == 0
    session.expire_all()
    mv = session.get(ModelVersion, "m1")
    assert (mv.display_name, mv.purpose) == ("意思決定支援モデル", "独立予測")
    assert mv.adoption_status == AdoptionStatus.CANDIDATE  # FR-009: adoption untouched

    # empty display-name clears to NULL; omitted purpose stays unchanged
    main(["set-model-label", "--model-version", "m1", "--display-name", "",
          "--database-url", database_url])
    session.expire_all()
    mv = session.get(ModelVersion, "m1")
    assert mv.display_name is None          # "" → NULL
    assert mv.purpose == "独立予測"          # omitted → unchanged
    assert mv.adoption_status == AdoptionStatus.CANDIDATE


def test_unset_stays_null(session, database_url):
    _seed(session, "m2")
    main(["set-model-label", "--model-version", "m2", "--purpose", "x",
          "--database-url", database_url])
    session.expire_all()
    mv = session.get(ModelVersion, "m2")
    assert mv.display_name is None          # never provided → NULL
    assert mv.purpose == "x"


def test_missing_model_version_returns_error(session, database_url):
    rc = main(["set-model-label", "--model-version", "ghost", "--display-name", "x",
               "--database-url", database_url])
    assert rc == 1
