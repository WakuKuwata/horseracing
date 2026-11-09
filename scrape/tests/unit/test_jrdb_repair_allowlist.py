"""178 T054: the repair allowlist is exactly the stage-1 definition-equal columns; the five
JRDB-defined physical columns are refused; a column whose parity report is below the contract
threshold is refused at run time (the threshold lives in ``parity.COLUMN_RULES``, not here)."""

from __future__ import annotations

import json

import pytest

from horseracing_scrape.jrdb import parity
from horseracing_scrape.jrdb.repair import (
    REFUSED_COLUMNS,
    REPAIRABLE_COLUMNS,
    RepairColumnRefused,
    assert_repairable,
)

EXPECTED = {
    "odds",
    "popularity",
    "weight",
    "last_3f",
    "owner_name",
    "breeder_name",
    "race_class",
    "prize_money",
    "post_time",
    "win_payouts",
}


def _report(rates: dict[str, float | None], *, year: int = 2008) -> dict:
    """A stage-1 report with one segment per column at the given rate (None = no compared cell)."""

    cols = {}
    for name, rule in parity.COLUMN_RULES.items():
        rate = rates.get(name, 1.0)
        n = 0 if rate is None else 1000
        cols[name] = {
            "meaning": rule["meaning"],
            "threshold": rule["threshold"],
            "rate": rate,
            "by_segment": {"all": {"n": n, "agree": int(n * (rate or 0)), "rate": rate}},
        }
    return {"year": year, "columns": cols}


@pytest.fixture
def parity_dir(tmp_path):
    (tmp_path / "2008.json").write_text(json.dumps(_report({})), encoding="utf-8")
    return tmp_path


def test_allowlist_is_the_definition_equal_set():
    assert set(REPAIRABLE_COLUMNS) == EXPECTED
    for name, spec in REPAIRABLE_COLUMNS.items():
        for pc in spec.parity_columns:
            rule = parity.COLUMN_RULES[pc]
            assert rule["meaning"] in ("same", "normalized", "jrdb_only"), (name, pc)
            assert rule["threshold"] is not None, (name, pc)


@pytest.mark.parametrize(
    "column", ["first_3f", "corner_orders", "running_style", "sire_line", "damsire_line"]
)
def test_jrdb_defined_columns_are_refused_before_anything_is_read(column, parity_dir):
    assert column in REFUSED_COLUMNS
    with pytest.raises(RepairColumnRefused) as exc:
        assert_repairable(column, parity_dir)
    assert exc.value.reason == "definition_mismatch"
    # even with no parity dir at all: the refusal does not depend on the reports
    with pytest.raises(RepairColumnRefused) as exc2:
        assert_repairable(column, None)
    assert exc2.value.reason == "definition_mismatch"


def test_unknown_column_is_refused(parity_dir):
    with pytest.raises(RepairColumnRefused) as exc:
        assert_repairable("finish_order", parity_dir)
    assert exc.value.reason == "not_in_allowlist"


def test_no_parity_report_refuses(tmp_path):
    with pytest.raises(RepairColumnRefused) as exc:
        assert_repairable("odds", tmp_path)
    assert exc.value.reason == "no_parity_report"
    with pytest.raises(RepairColumnRefused) as exc2:
        assert_repairable("odds", None)
    assert exc2.value.reason == "no_parity_dir"


def test_passing_reports_return_the_rates(parity_dir):
    rates = assert_repairable("odds", parity_dir)
    assert rates == {"odds": {"2008": 1.0}, "popularity": {"2008": 1.0}}


def test_a_report_below_threshold_refuses_at_run_time(tmp_path):
    thr = parity.COLUMN_RULES["owner_name"]["threshold"]
    (tmp_path / "2008.json").write_text(json.dumps(_report({})), encoding="utf-8")
    (tmp_path / "2009.json").write_text(
        json.dumps(_report({"owner_name": thr - 0.01}, year=2009)), encoding="utf-8"
    )
    with pytest.raises(RepairColumnRefused) as exc:
        assert_repairable("owner_name", tmp_path)
    assert exc.value.reason == "parity_below_threshold" and "2009" in exc.value.detail
    # the other columns of the same reports still pass
    assert assert_repairable("breeder_name", tmp_path)["breeder_name"] == {"2008": 1.0, "2009": 1.0}


def test_rate_exactly_at_threshold_passes(tmp_path):
    thr = parity.COLUMN_RULES["odds"]["threshold"]
    (tmp_path / "2008.json").write_text(json.dumps(_report({"odds": thr})), encoding="utf-8")
    assert assert_repairable("odds", tmp_path)["odds"] == {"2008": thr}


def test_undecided_jrdb_only_column_does_not_refuse(tmp_path):
    """post_time: the DB is NULL before 2026 so stage 1 has no compared cell → None, not False;
    its contract meaning is ``jrdb_only``, the one meaning allowed to be undecided."""

    (tmp_path / "2008.json").write_text(json.dumps(_report({"post_time": None})), encoding="utf-8")
    assert assert_repairable("post_time", tmp_path) == {"post_time": {"2008": None}}


def test_undecided_comparable_column_refuses(tmp_path):
    """codex D23 #1: a column stage 1 never compared is not a PASSED column."""

    (tmp_path / "2008.json").write_text(json.dumps(_report({"odds": None})), encoding="utf-8")
    with pytest.raises(RepairColumnRefused) as exc:
        assert_repairable("odds", tmp_path)
    assert exc.value.reason == "parity_undecided"
    # a report that lacks the column altogether refuses the same way
    doc = _report({})
    del doc["columns"]["owner_name"]
    (tmp_path / "2009.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(RepairColumnRefused) as exc2:
        assert_repairable("owner_name", tmp_path)
    assert exc2.value.reason == "parity_undecided"
