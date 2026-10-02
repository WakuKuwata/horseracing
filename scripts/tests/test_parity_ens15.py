"""138 T020a: the parity acceptance (``compare``) on synthetic frames.

Check (c) must hold both ways: a research selection the production path misses fails it, and so
does a production selection on a common row that the research does not make (over-selection) —
the displayed backtest has to describe the production picks (plan 0.1 (c)・G1・D19). Run from the
training environment (the script imports ``horseracing_training``):

    cd training && uv run pytest ../scripts/tests/test_parity_ens15.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "roi_explore"))
import parity_ens15_20261001 as pz  # noqa: E402

DAY = "2026-01-04"
SNAPSHOT = pd.Timestamp("2026-02-01", tz="UTC")
#: (race_id, horse_id, horse_number, odds, ens_ev, single_ev, days_since_last)
ROWS = [
    ("R1", "H1", 1, 25.0, 1.5, 1.0, 28.0),   # S1 S2 S3 S4
    ("R1", "H2", 2, 10.0, 0.8, 0.9, 28.0),   # nothing
    ("R2", "H3", 1, 30.0, 1.25, 1.3, 40.0),  # S1 S3 S4 S5
]


def _frames(rows=ROWS):
    research = pd.DataFrame({
        "race_id": [r[0] for r in rows], "horse_id": [r[1] for r in rows],
        "race_date": [DAY] * len(rows), "odds_r": [r[3] for r in rows],
        "ens_ev_r": [r[4] for r in rows], "p_r": [r[4] / r[3] for r in rows],
        "single_ev_r": [r[5] for r in rows], "horse_number_r": [r[2] for r in rows],
        "days_since_last_r": [r[6] for r in rows],
    })
    prod = pd.DataFrame({
        "race_id": [r[0] for r in rows], "horse_id": [r[1] for r in rows],
        "horse_number_p": [r[2] for r in rows], "odds_p": [r[3] for r in rows],
        "p_p": [r[4] / r[3] for r in rows], "race_date": [DAY] * len(rows),
        "one_winner": [True] * len(rows),
    })
    old = pd.Timestamp("2026-01-01", tz="UTC")
    ent = pd.DataFrame({
        "race_id": [r[0] for r in rows], "horse_id": [r[1] for r in rows],
        "horse_number": [r[2] for r in rows], "entry_status": ["started"] * len(rows),
        "created_at": [old] * len(rows), "updated_at": [old] * len(rows),
    })
    return research, prod, ent


def _compare(research, prod, ent, p_matched):
    return pz.compare(research, prod, ent, pz.research_matches(research), p_matched,
                      research_max_date="2026-09-30", snapshot=SNAPSHOT)


def _with(p_matched: pd.DataFrame, rows: list[tuple]) -> pd.DataFrame:
    extra = pd.DataFrame(rows, columns=["race_id", "horse_id", "rule_id"])
    return pd.concat([p_matched, extra], ignore_index=True)


def test_agreeing_selections_pass():
    research, prod, ent = _frames()
    r_matched = pz.research_matches(research)
    assert sorted(map(tuple, r_matched.to_numpy())) == [
        ("R1", "H1", "S1"), ("R1", "H1", "S2"), ("R1", "H1", "S3"), ("R1", "H1", "S4"),
        ("R2", "H3", "S1"), ("R2", "H3", "S3"), ("R2", "H3", "S4"), ("R2", "H3", "S5"),
    ]
    res = _compare(research, prod, ent, r_matched.copy())
    assert res["acceptance"] == {"a_common_match_rate": True, "b_one_side_rows_explained": True,
                                 "c_rule_selections_agree": True, "passed": True}
    assert res["rule_failure_count"] == 0


def test_production_over_selection_on_a_common_row_fails_c():
    research, prod, ent = _frames()
    p_matched = _with(pz.research_matches(research), [("R1", "H2", "S3")])
    res = _compare(research, prod, ent, p_matched)
    assert res["acceptance"]["c_rule_selections_agree"] is False
    assert res["acceptance"]["passed"] is False
    assert res["acceptance"]["a_common_match_rate"] and res["acceptance"][
        "b_one_side_rows_explained"]
    assert res["rule_failures"] == [{"rule_id": "S3", "race_id": "R1", "horse_id": "H2",
                                     "reason": "research_not_selected"}]
    assert res["rule_failures_by_reason"] == {"research_not_selected": 1}
    assert res["rules"]["S3"]["production_only_selected_on_common_rows"] == 1
    assert res["rules"]["S3"]["agreement_rate"] == 1.0  # the research direction alone agrees


def test_production_missing_a_research_selection_fails_c():
    research, prod, ent = _frames()
    r_matched = pz.research_matches(research)
    drop = (r_matched["race_id"] == "R2") & (r_matched["rule_id"] == "S5")
    res = _compare(research, prod, ent, r_matched[~drop].reset_index(drop=True))
    assert res["acceptance"]["c_rule_selections_agree"] is False
    assert res["rule_failures"] == [{"rule_id": "S5", "race_id": "R2", "horse_id": "H3",
                                     "reason": "production_not_selected"}]


def test_a_selection_on_an_explained_production_only_row_is_left_to_b():
    research, prod, ent = _frames()
    late = pd.DataFrame({"race_id": ["R9"], "horse_id": ["H9"], "horse_number_p": [1],
                         "odds_p": [25.0], "p_p": [0.06], "race_date": ["2026-10-04"],
                         "one_winner": [True]})
    prod = pd.concat([prod, late], ignore_index=True)
    p_matched = _with(pz.research_matches(research), [("R9", "H9", "S1")])
    res = _compare(research, prod, ent, p_matched)
    assert res["production_only"]["by_reason"] == {"after_research_snapshot": 1}
    assert res["acceptance"]["passed"] is True
