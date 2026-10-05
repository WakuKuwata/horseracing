"""Feature 138 (plan 0.4・T016): the pure part of the pick writer.

``candidate_picks`` matches on the values exactly as stored (``market_ev.stored_values``) through
the single rule definition in ``horseracing_eval.attention_rules``; ``PickContext`` derives the
per-run columns (result-pending flag, seconds to post, field digest).
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from horseracing_eval import attention_rules as ar

from horseracing_training import attention_picks, market_ev

T0 = datetime.datetime(2026, 9, 27, 1, 0, tzinfo=datetime.UTC)


def _pred(rows: list[tuple]) -> pd.DataFrame:
    """rows: (race_id, horse_id, horse_number, odds, expected_return)."""
    return pd.DataFrame(
        {
            "race_id": [r[0] for r in rows],
            "horse_id": [r[1] for r in rows],
            "horse_number": [r[2] for r in rows],
            "odds_used": [r[3] for r in rows],
            "odds_observed_at": [T0] * len(rows),
            "win_prob": [r[4] / r[3] for r in rows],
            "expected_return": [r[4] for r in rows],
            "booster": ["b"] * len(rows),
            "booster_sha256": ["0" * 64] * len(rows),
        }
    )


def _feats(rows: list[tuple]) -> pd.DataFrame:
    """rows: (race_id, horse_id, days_since_last)."""
    return pd.DataFrame(
        {"race_id": [r[0] for r in rows], "horse_id": [r[1] for r in rows],
         "days_since_last": [r[2] for r in rows]}
    )


def _picks(ens_rows, single_ev: dict, days: dict):
    ens = _pred(ens_rows)
    single = _pred([(r, h, n, o, single_ev[h]) for r, h, n, o, _ in ens_rows])
    feats = _feats([(r, h, days[h]) for r, h, *_ in ens_rows])
    return attention_picks.candidate_picks(ens, single, feats)


def test_inclusion_and_band_follow_the_registry():
    by_race, skipped = _picks(
        [
            ("R1", "A", 1, 25.0, 1.5),   # S1, S2 (>1.3), S3, S4; S5 by the single seed
            ("R1", "B", 2, 25.0, 1.25),  # S1, S3, S4 (not S2)
            ("R1", "C", 3, 40.0, 1.5),   # odds 40 is outside [20, 40): S3 only
            ("R1", "D", 4, 25.0, 1.5),   # no previous run (NaN gap): S3 only
            ("R1", "E", 5, 8.0, 1.15),   # nothing
        ],
        single_ev={"A": 1.25, "B": 1.0, "C": 1.0, "D": 1.0, "E": 1.0},
        days={"A": 28.0, "B": 112.0, "C": 28.0, "D": np.nan, "E": 28.0},
    )
    assert skipped == 0
    got = {(p["horse_id"], p["rule_id"]) for p in by_race["R1"]}
    assert got == {
        ("A", "S1"), ("A", "S2"), ("A", "S3"), ("A", "S4"), ("A", "S5"),
        ("B", "S1"), ("B", "S3"), ("B", "S4"),
        ("C", "S3"),
        ("D", "S3"),
    }
    a = next(p for p in by_race["R1"] if p["horse_id"] == "A")
    win, odds, ev = market_ev.stored_values(1.5 / 25.0, 25.0)
    assert (a["ens_expected_return"], a["odds_used"]) == (ev, odds)
    assert a["single_expected_return"] == market_ev.stored_values(1.25 / 25.0, 25.0)[2]
    assert a["days_since_last"] == Decimal("28.0") and a["horse_number"] == 1
    d = next(p for p in by_race["R1"] if p["horse_id"] == "D")
    assert d["days_since_last"] is None


def _raw_pred(rows: list[tuple]) -> pd.DataFrame:
    """rows: (horse_id, horse_number, odds, win_prob) of race R1 — win_prob given exactly, and
    ``expected_return`` = the float product (what ``predict`` returns before storing)."""
    return pd.DataFrame(
        {
            "race_id": ["R1"] * len(rows),
            "horse_id": [r[0] for r in rows],
            "horse_number": [r[1] for r in rows],
            "odds_used": [r[2] for r in rows],
            "odds_observed_at": [T0] * len(rows),
            "win_prob": [r[3] for r in rows],
            "expected_return": [r[3] * r[2] for r in rows],
            "booster": ["b"] * len(rows),
            "booster_sha256": ["0" * 64] * len(rows),
        }
    )


def _rules_of(ens_rows: list[tuple], single_rows: list[tuple], gap: float) -> dict[str, list]:
    feats = _feats([("R1", r[0], gap) for r in ens_rows])
    by_race, _ = attention_picks.candidate_picks(_raw_pred(ens_rows), _raw_pred(single_rows),
                                                 feats)
    out: dict[str, list] = {r[0]: [] for r in ens_rows}
    for p in by_race.get("R1", []):
        out[p["horse_id"]].append(p["rule_id"])
    return out


@pytest.mark.parametrize(
    ("odds", "ens_win", "single_win", "gap", "expected"),
    [
        (25.0, 0.06, 0.04, 28.0, ["S1", "S2", "S3", "S4"]),  # ens 1.50, single 1.00
        (20.0, 0.075, 0.05, 14.0, ["S1", "S2", "S3", "S4"]),  # odds 20 and gap 14 are inside
        (40.0, 0.0375, 0.025, 28.0, ["S3"]),                  # odds 40 is outside [20, 40)
        (25.0, 0.06, 0.04, 112.0, ["S1", "S2", "S3", "S4"]),  # gap 112 is inside
        (25.0, 0.06, 0.04, 113.0, ["S3"]),                    # gap 113 is outside
        (25.0, 0.06, 0.04, 13.0, ["S3"]),                     # gap 13 is outside
        (20.0, 0.06, 0.06, 28.0, ["S4"]),                     # stored 1.20 exactly: > is strict
        (26.0, 0.05, 0.04, 28.0, ["S1", "S3", "S4"]),         # stored 1.30 exactly: no S2
        (25.0, 0.04, 0.05, 28.0, ["S5"]),                     # only the single seed > 1.2
    ],
)
def test_rule_boundaries_on_the_stored_value(odds, ens_win, single_win, gap, expected):
    got = _rules_of([("A", 1, odds, ens_win)], [("A", 1, odds, single_win)], gap)
    assert got == {"A": expected}


def test_matching_uses_the_stored_product_not_the_float_product():
    """Two horses whose float product and stored (exact decimal) product fall on opposite sides of
    1.2: the stored side decides, so a judged row and the stored row it came from agree."""
    a_win, a_odds = 0.2, 6.0                   # float 1.2000000000000002, stored exactly 1.20
    b_win, b_odds = 0.04013377926421405, 29.9  # float 1.2, stored 1.200000000000000095…
    assert a_win * a_odds > 1.2 and not b_win * b_odds > 1.2
    assert not float(market_ev.stored_values(a_win, a_odds)[2]) > 1.2
    assert float(market_ev.stored_values(b_win, b_odds)[2]) > 1.2
    rows = [("A", 1, a_odds, a_win), ("B", 2, b_odds, b_win)]
    got = _rules_of(rows, rows, 28.0)
    assert got == {"A": [], "B": ["S1", "S3", "S4", "S5"]}


def test_horse_without_number_is_skipped_and_counted():
    by_race, skipped = _picks([("R1", "A", np.nan, 25.0, 1.5)], {"A": 1.0}, {"A": 28.0})
    assert by_race == {} and skipped == 4  # S1, S2, S3, S4


def _ctx(**kw) -> attention_picks.PickContext:
    base = {
        "run_id": uuid.uuid4(), "computed_at": T0, "ensemble_model_version": "mev-ens15-v1",
        "single_model_version": "mev-binary-v2", "with_results": frozenset({"SETTLED"}),
        "post_times": {}, "started": {"R1": ("h2", "h1", "h3")},
    }
    return attention_picks.PickContext(**(base | kw))


@pytest.mark.parametrize(
    ("race_id", "post", "pending"),
    [
        ("R1", None, True),                                   # post time unknown
        ("R1", T0 + datetime.timedelta(minutes=5), True),     # computed before the post
        ("R1", T0, False),                                    # computed at / after the post
        ("SETTLED", T0 + datetime.timedelta(hours=1), False),  # results already in
    ],
)
def test_result_pending_definition(race_id, post, pending):
    assert _ctx().result_pending(race_id, post) is pending


def test_row_columns():
    ctx = _ctx()
    post = T0 + datetime.timedelta(minutes=30, seconds=1)
    cols = ctx.row_columns("R1", post)
    assert cols["seconds_to_post"] == 1801
    assert cols["field_digest"] == ar.field_digest(["h1", "h2", "h3"])
    # Feature 139: picks are written under selection policy v2 (official-payout settlement)
    assert cols["logic_version"] == market_ev.ENSEMBLE_LOGIC_VERSION + ";policy=v2"
    assert cols["selection_policy_version"] == ar.SELECTION_POLICY_VERSION == "v2"
    assert cols["rule_set_version"] == ar.RULE_SET_VERSION
    assert cols["run_id"] == ctx.run_id and cols["computed_at"] == T0
    assert ctx.row_columns("R9", None)["field_digest"] == ar.field_digest([])
    assert ctx.row_columns("R9", None)["seconds_to_post"] is None
