"""Feature 091 (research D16): a paired evaluation must be able to pin its input.

The defect this closes: `paired-eval` read the live database, so a multi-hour run could not be
repeated. Measured — the same arms under the same frozen gate-config gave −0.010592 on one day and
−0.010539 the next, and the cause was that 12,816 of the eval window's 267,744 race_horses rows
(4.8%) had been rewritten by the daily ingest in between. Because only the latest value per row is
stored, it is not even recoverable whether the values changed or only `updated_at` moved. The
frozen `determinism.tolerance: 1e-9` was therefore unverifiable by construction.

Pinning is the one place where skipping the staleness check is correct rather than dangerous, so
these tests pin BOTH halves of that claim: it must reach the fit, and it must be recorded.
"""

from __future__ import annotations

import argparse

import pytest

from horseracing_training.cli import _factory_from_spec, _input_provenance
from horseracing_training.recipe import ModelRecipe, RecipeFactory

SPEC = "pl_topk:isotonic:0.3"


def _args(**kw):
    base = {"use_materialized": False, "materialized_path": None, "pin_snapshot": False}
    return argparse.Namespace(**{**base, **kw})


def test_factory_carries_the_pin_down_to_the_fit():
    f = _factory_from_spec(None, SPEC, use_materialized=True,
                           materialized_path="/tmp/x.parquet", pin_snapshot=True)
    assert isinstance(f, RecipeFactory)
    assert (f.use_materialized, f.materialized_path, f.pin_snapshot) == (
        True, "/tmp/x.parquet", True)


def test_default_is_unchanged_so_every_existing_run_behaves_identically():
    f = _factory_from_spec(None, SPEC)
    assert (f.use_materialized, f.materialized_path, f.pin_snapshot) == (False, None, False)


def test_pinning_is_fit_scope_not_model_identity():
    """recipe_hash identifies the MODEL. Where its rows were read from does not change the model,
    and folding it in would re-identify every existing artifact."""
    a = RecipeFactory(None, ModelRecipe())
    b = RecipeFactory(None, ModelRecipe(), use_materialized=True,
                      materialized_path="/tmp/x.parquet", pin_snapshot=True)
    assert a.recipe_hash == b.recipe_hash


# --- provenance: a pinned run must never be mistakable for a fresh one -------------------------


def test_live_database_runs_are_labelled_irreproducible():
    p = _input_provenance(_args())
    assert p["input_source"] == "live_database"
    assert "NOT reproducible" in p["input_note"]


def test_pinned_runs_record_which_bytes_they_read(tmp_path):
    parquet = tmp_path / "features.parquet"
    parquet.write_bytes(b"")
    (tmp_path / "features.manifest.json").write_text(
        '{"source_fingerprint": "abc123", "data_through": "2026-08-09", '
        '"content_hash": "def456", "feature_version": "features-021"}')
    p = _input_provenance(_args(use_materialized=True, materialized_path=str(parquet),
                                pin_snapshot=True))
    assert p["input_source"] == "materialized_parquet"
    assert p["pinned"] is True
    assert p["snapshot_fingerprint"] == "abc123"
    assert p["snapshot_data_through"] == "2026-08-09"


def test_a_missing_manifest_is_reported_not_swallowed(tmp_path):
    """Provenance must never be silently absent — an artifact with no fingerprint and no error
    reads as 'nobody checked', which is how the last misattribution happened."""
    p = _input_provenance(_args(use_materialized=True,
                                materialized_path=str(tmp_path / "nope.parquet")))
    assert "manifest_error" in p


def test_unpinned_materialized_run_is_still_marked_as_such(tmp_path):
    """--use-materialized without --pin-snapshot still verifies against the DB, so it is NOT
    guaranteed repeatable. The flag has to show up in the record."""
    parquet = tmp_path / "f.parquet"
    (tmp_path / "f.manifest.json").write_text('{"source_fingerprint": "x"}')
    p = _input_provenance(_args(use_materialized=True, materialized_path=str(parquet)))
    assert p["pinned"] is False


# --- the guards must actually refuse ------------------------------------------------------------


@pytest.mark.parametrize(
    ("kw", "why"),
    [({"use_materialized": True}, "a parquet path is required"),
     ({"pin_snapshot": True}, "pinning without materialized is meaningless")],
)
def test_incoherent_flag_combinations_fail_closed(kw, why, capsys):
    """An implicit default parquet would silently pick whichever file happens to be on disk —
    the opposite of pinning."""
    from horseracing_training.cli import _paired_eval

    args = _args(**kw)
    args.candidate, args.active = SPEC, SPEC
    rc = _paired_eval(None, args)
    assert rc == 1, why
