"""2026-11-10: JRDB running style / line → the current DB's vocabulary (jrdb/vocab.py) and the
current profile's vocabulary-checked columns. Synthetic tables only (the real ones are
vendor-derived and gitignored; their pin is checked when present)."""

from __future__ import annotations

import json
import math

import pytest

from horseracing_scrape.jrdb import profile, vocab
from horseracing_scrape.jrdb.errors import ProfileViolation


def _tree(nodes, features=vocab.TREE_FEATURES):
    return {"features": list(features), "nodes": nodes}


#: code <= 1.5 → 逃げ / <= 2.5 → 先行 / else (last ÷ fs <= 0.1 → 差し, else 後方)
SYNTH = vocab.Translator(
    tree=_tree(
        [
            {"f": "code", "t": 1.5, "l": 1, "r": 2},
            {"leaf": "逃げ"},
            {"f": "code", "t": 2.5, "l": 3, "r": 4},
            {"leaf": "先行"},
            {"f": "last", "t": 0.1, "l": 5, "r": 6},
            {"leaf": "差し"},
            {"leaf": "後方"},
        ]
    ),
    lines={"1206": "ロイヤルチャージャー系", "9999": "架空系"},
)


def test_features_are_the_fit_time_definition():
    f = vocab.style_features("3", (5, 2, 2), 1, 16)
    exp = [3.0, 5 / 16, 2 / 16, 2 / 16, 1 / 16, 16.0, 3.0, 3 / 16, 0.0]
    assert f == pytest.approx(exp)
    # one corner: no second-last (−1) and gain2 undefined (−1); no corners / no finish → −1
    one = vocab.style_features(4, (8,), None, 10)
    assert one == pytest.approx([4.0, 0.8, -1.0, 0.8, -1.0, 10.0, 1.0, 0.0, -1.0])
    none = vocab.style_features("1", (), None, 8)
    assert none == pytest.approx([1.0, -1.0, -1.0, -1.0, -1.0, 8.0, 0.0, -1.0, -1.0])
    # stored positions only (0 / None / "" are empty slots of the 4-slot packing)
    assert vocab.style_features("2", (0, None, "", 3), 2, 6)[1] == pytest.approx(0.5)


def test_tree_walk_and_boundaries():
    assert SYNTH.style_label("1", (1, 1), 1, 10) == "逃げ"
    assert SYNTH.style_label(2, (3, 3), 2, 10) == "先行"
    # last ÷ fs == threshold (float32 on both sides, as sklearn) → left
    assert SYNTH.style_label("3", (5, 1), 4, 10) == "差し"
    assert SYNTH.style_label("4", (5, 2), 4, 10) == "後方"
    # nothing is invented
    assert SYNTH.style_label(None, (1, 1), 1, 10) is None
    assert SYNTH.style_label("3", (1, 1), 1, 0) is None


def test_out_of_vocabulary_values_are_refused():
    bad = vocab.Translator(tree=_tree([{"leaf": "3"}]), lines={})
    with pytest.raises(vocab.VocabUnavailable):
        bad.style_label("3", (1,), 1, 8)
    assert SYNTH.line_family("1206") == "ロイヤルチャージャー系"
    assert SYNTH.line_family(" 1206 ") == "ロイヤルチャージャー系"
    assert SYNTH.line_family("0000") is None and SYNTH.line_family(None) is None
    with pytest.raises(vocab.VocabUnavailable):
        SYNTH.line_family("9999")


def test_load_refuses_files_that_are_not_the_pinned_ones(tmp_path):
    (tmp_path / vocab.TREE_FILE).write_text(json.dumps(_tree([{"leaf": "逃げ"}])))
    (tmp_path / vocab.LINE_FILE).write_text(json.dumps({"map": {}}))
    with pytest.raises(vocab.VocabUnavailable, match="sha256"):
        vocab.load_translator(tmp_path)
    with pytest.raises(vocab.VocabUnavailable):
        vocab.load_translator(tmp_path / "missing")


def test_the_real_tables_when_present():
    try:
        t = vocab.load_translator()
    except vocab.VocabUnavailable:
        pytest.skip("artifacts/jrdb/vocab is not on this machine (vendor-derived, gitignored)")
    leaves = {n["leaf"] for n in t.tree["nodes"] if "leaf" in n}
    assert leaves == vocab.RUNNING_STYLE_LABELS
    assert set(t.lines.values()) <= vocab.LINE_FAMILIES
    assert all(not math.isnan(n.get("t", 0.0)) for n in t.tree["nodes"])


def test_current_profile_accepts_only_translated_values():
    for table, column, value in (
        ("race_horses", "running_style", "後方"),
        ("horses", "sire_line", "ナスルーラ系"),
        ("horses", "damsire_line", "マンノウォー系"),
    ):
        profile.check_payload("current", table, {column: value})
        # the raw column ban stays: allows() is False, a JRDB code / None / another word is refused
        assert not profile.CURRENT.allows(table, column)
        for bad in ("3", "1206", None, "差し追込"):
            with pytest.raises(ProfileViolation):
                profile.check_payload("current", table, {column: bad})
    # the canonical profile is unchanged: JRDB's own codes
    profile.check_payload("jrdb_canonical", "race_horses", {"running_style": "3"})
    profile.check_payload("jrdb_canonical", "horses", {"sire_line": "1206"})
