"""Cutoff, frozen provenance, and year-specific mixture contract for research 142."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_142/run.py"
SPEC = importlib.util.spec_from_file_location("roi_run142", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def test_reader_excludes_future_and_pre2007_before_return(tmp_path):
    path = tmp_path / "dates.parquet"
    pd.DataFrame({"race_date": ["2006-12-31", "2026-06-26", "2026-06-27"],
                  "year": [2006, 2026, 2026], "won": [99, 1, 99]}).to_parquet(path)
    actual = MOD.bounded_read(path)
    assert actual.won.tolist() == [1]


def test_mixture_never_uses_current_year_fit():
    f = pd.DataFrame({"year": [2020, 2020], "race_id": ["r", "r"],
                      "p_raw": [.7, .3], "odds": [2., 3.]})
    record = {"year": 2020, "a": .3, "b": .7, "fit_from_year": 2010,
              "fit_through_year": 2020, "n_races": 1, "n_rows": 2, "converged": True}
    table = {"coefficients": {"2020": record}, "source_sha256": "x"}
    with pytest.raises(ValueError, match="non-past"):
        MOD.mix_by_year(f, table)
    record["fit_through_year"] = 2019
    assert np.isclose(MOD.mix_by_year(f, table).sum(), 1)


def test_noncontiguous_years_are_mixed_within_complete_race():
    f = pd.DataFrame({"year": [2020, 2021, 2020, 2021], "race_id": ["r", "s", "r", "s"],
                      "p_raw": [.7, .2, .3, .8], "odds": [2., 5., 3., 1.5]})
    table = {"coefficients": {str(y): {"year": y, "a": 0., "b": 1.,
              "fit_from_year": 2010, "fit_through_year": y-1, "n_races": 1,
              "n_rows": 2, "converged": True} for y in [2020, 2021]}, "source_sha256": "x"}
    np.testing.assert_allclose(MOD.mix_by_year(f, table), f.p_raw)


def test_protocol_and_source_changes_are_refused(tmp_path, monkeypatch):
    source = tmp_path / "source.py"
    source.write_text("first")
    monkeypatch.setattr(MOD, "ROOT", tmp_path)
    monkeypatch.setattr(MOD, "inputs", lambda: [source])
    monkeypatch.setattr(MOD, "source_files", lambda: [])
    out = tmp_path / "new"
    doc = MOD.freeze(out)
    assert not doc["can_adopt"]
    assert MOD.verify(out) == doc
    with pytest.raises(FileExistsError):
        MOD.freeze(out)
    source.write_text("different")
    with pytest.raises(ValueError, match="input/source changed"):
        MOD.verify(out)


def test_report_writer_never_overwrites(tmp_path):
    out = tmp_path / "result.json"
    MOD.write_new(out, {"a": 1})
    with pytest.raises(FileExistsError):
        MOD.write_new(out, {"a": 2})
    assert json.loads(out.read_text()) == {"a": 1}


def test_whole_field_exclusion_for_invalid_flags_duplicate_or_missing_starter():
    d = pd.DataFrame({"race_id": ["a", "a", "b", "b", "c", "c", "d", "d"],
                      "horse_id": ["h1", "h2"]*4, "horse_number": [1, 2]*4,
                      "year": [2020]*8, "field_size": [2, 2, 2, 2, 2, 2, 3, 3],
                      "won": [1, 0]*4, "race_ok": [True]*8, "dead_heat": [False]*8})
    d.loc[1, "race_ok"] = False
    d.loc[5, "horse_number"] = 1
    actual, excluded = MOD.eligible_existing(d)
    assert actual.race_id.tolist() == ["b", "b"]
    assert excluded == ["c", "d"]
