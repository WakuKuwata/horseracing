"""178 T014: JRDB's own estimates never become attributes (憲法 II) and odds the DB cannot hold are
never read (憲法 V); ``features/`` never imports the JRDB package."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from horseracing_scrape.jrdb import models
from horseracing_scrape.jrdb.layout import all_layouts

REPO_ROOT = Path(__file__).resolve().parents[3]

#: tokens that name JRDB indices, comments, 基準オッズ, 10 時オッズ and the TYB odds snapshot
FORBIDDEN = re.compile(
    r"(idm|index|shisu|odds10|comment|kijun|snapshot|mark|paddock)", re.IGNORECASE
)


def test_no_adopted_layout_field_names_a_forbidden_token():
    bad = []
    for lay in all_layouts():
        for f in lay.fields:
            if f.target != "skip" and FORBIDDEN.search(f.name):
                bad.append((lay.kind, f.name))
    assert bad == []


def test_no_value_object_attribute_names_a_forbidden_token():
    bad = []
    for obj in vars(models).values():
        if dataclasses.is_dataclass(obj) and isinstance(obj, type):
            for fld in dataclasses.fields(obj):
                if FORBIDDEN.search(fld.name):
                    bad.append((obj.__name__, fld.name))
    assert bad == []


def test_tyb_and_kyi_odds_columns_are_skipped():
    tyb = next(lay for lay in all_layouts() if lay.kind == "TYB")
    kyi = next(lay for lay in all_layouts() if lay.kind == "KYI")
    sed = next(lay for lay in all_layouts() if lay.kind == "SED")
    assert tyb.field("tyb_win_odds_snapshot").target == "skip"
    assert tyb.field("tyb_place_odds_snapshot").target == "skip"
    assert kyi.field("kijun_odds").target == "skip"
    assert sed.field("odds10_win").target == "skip" and sed.field("odds10_place").target == "skip"
    assert sed.field("final_place_odds_low").target == "skip"


def test_the_four_canonical_only_columns_are_marked_in_the_layouts():
    marked = {
        (lay.kind, f.name)
        for lay in all_layouts()
        for f in lay.fields
        if f.target == "canonical_only"
    }
    assert marked == {
        ("SED", "first_3f"),
        ("SED", "corner_1"),
        ("SED", "corner_2"),
        ("SED", "corner_3"),
        ("SED", "corner_4"),
        ("SED", "running_style_code"),
        ("UKC", "sire_line_code"),
        ("UKC", "damsire_line_code"),
    }


def test_features_package_does_not_import_the_jrdb_package():
    src = REPO_ROOT / "features" / "src"
    hits = [
        str(p.relative_to(REPO_ROOT))
        for p in src.rglob("*.py")
        if "horseracing_scrape.jrdb" in p.read_text(encoding="utf-8")
        or re.search(r"from\s+horseracing_scrape\s+import\s+jrdb", p.read_text(encoding="utf-8"))
    ]
    assert hits == []
