"""C14 (2026-10-29): the frozen JSON, its sha256 and the code constants must agree."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from horseracing_eval import attention_rules as ar
from horseracing_eval import display_update_rule as dur

SPEC = Path(__file__).resolve().parents[3] / "specs" / "174-near-post-decision-quotes"
FROZEN = SPEC / "display-update-rule.json"


def test_frozen_json_matches_code_and_sha():
    text = FROZEN.read_text()
    assert json.loads(text) == dur.frozen_payload()
    recorded = (SPEC / "display-update-rule.json.sha256").read_text().split()[0]
    assert hashlib.sha256(text.encode()).hexdigest() == recorded


def test_constants_are_single_sourced_from_138():
    assert dur.MIN_POINTS == ar.CHECKPOINTS[0] == 300
    assert dur.BOOTSTRAP == ar.BOOTSTRAP
    assert dur.MIN_DIFF_CI_LOW == 0.0


@pytest.mark.parametrize(
    "n, low, expected",
    [
        (0, None, "insufficient"),
        (299, 0.5, "insufficient"),
        (300, 0.0, "keep"),
        (300, -0.01, "keep"),
        (300, 1e-9, "replace"),
        (1000, 0.03, "replace"),
    ],
)
def test_decide(n, low, expected):
    assert dur.decide(n_points=n, diff_ci_low=low) == expected


def test_decide_requires_interval_once_enough_points():
    with pytest.raises(ValueError):
        dur.decide(n_points=300, diff_ci_low=None)
