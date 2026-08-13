"""One invariant, stated once: a graded race must not leave the parser looking like an open race.

The per-icon fixture tests pin today's markup. This pins the RULE, so a future netkeiba change —
a new icon suffix, a reordered class token, a selector rename — is caught as a contract violation
rather than as a slow accuracy drift nobody attributes to the scraper. That drift is exactly what
happened at the JRA-VAN cutover: `grade` and `race_class` disagreed for ten months and the only
symptom was that graded races predicted slightly worse (winner NLL −0.0129).

The same predicate is worth running against the database periodically; here it is enforced on the
parser's own output, which is where the value is born.
"""

from __future__ import annotations

import pytest

from horseracing_scrape.parse.entries import _CLASS_BY_GRADE, parse_entries

from .test_parse_entries import ENTRIES, _graded_html, real_fixture


def _violates(race) -> bool:
    """A row is inconsistent when the grade field claims a grade the class field does not carry."""
    if race.grade not in _CLASS_BY_GRADE:
        return False
    return race.race_class != _CLASS_BY_GRADE[race.grade]


@pytest.mark.parametrize("icon", ["Icon_GradeType1", "Icon_GradeType2", "Icon_GradeType3"])
def test_no_graded_race_escapes_with_a_plain_class(icon):
    assert not _violates(parse_entries(_graded_html(icon)).race)


def test_the_real_fixture_satisfies_the_invariant():
    assert not _violates(parse_entries(real_fixture(ENTRIES)).race)


def test_the_invariant_can_actually_fail():
    """An assertion that cannot fail protects nothing. Hand-build the pre-fix state and require
    the predicate to flag it."""

    class _Race:
        grade = "G1"
        race_class = "オープン"

    assert _violates(_Race())


def test_ungraded_races_are_outside_the_invariant():
    """It must not fire on races that simply have no grade — otherwise every maiden trips it."""

    class _Race:
        grade = None
        race_class = "未勝利"

    assert not _violates(_Race())


def test_jump_grades_are_deliberately_out_of_scope():
    """netkeiba's icon map yields only the three flat grades, and this repo has a separate known
    bug mislabelling jump races as flat — so inferring ＪＧ１ here would be guessing on top of a
    known-bad signal. All 111 affected rows measured in the database were 芝/ダ, none jump."""
    assert set(_CLASS_BY_GRADE) == {"G1", "G2", "G3"}
