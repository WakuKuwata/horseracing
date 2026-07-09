"""Steeplechase (障害) track_type derivation (data bugfix).

JRA-VAN col14 (track_type) reads 芝/ダ even for jump races because they run on turf/dirt courses;
the only jump marker is the race name (col9, e.g. "障害未勝利") or the jump-grade class (ＪＧ３).
Reading col14 alone mislabeled ~2,400 jump races (2007-2025) as flat turf/dirt. This guards that
ingest now derives track_type='障' for jumps (matching the netkeiba scrape path) and leaves genuine
flat races — including long-distance flat stayers — untouched.
"""

from __future__ import annotations

from horseracing_ingest import layout
from horseracing_ingest.mapping import _derive_track_type, _is_jump_race
from horseracing_ingest.parser import ParsedRow


def _row(*, name_short="", name_full="", race_class="", track_type="芝") -> ParsedRow:
    fields = [""] * layout.EXPECTED_COLUMNS
    fields[layout.RACE_NAME_SHORT] = name_short
    fields[layout.RACE_NAME_FULL] = name_full
    fields[layout.RACE_CLASS] = race_class
    fields[layout.TRACK_TYPE] = track_type
    return ParsedRow(1, fields)


def test_is_jump_race_by_name() -> None:
    assert _is_jump_race("障害未勝利", None, "未勝利")
    assert _is_jump_race("中山グランドジャンプ", None, "ＪＧ１")
    assert _is_jump_race("中山大障害", None, "ＪＧ１")
    assert _is_jump_race("東京ハイジャンプ", None, "ＪＧ２")


def test_is_jump_race_by_jump_grade_class() -> None:
    # class ＪＧ３ (NFKC -> JG3) marks a jump even if the short name didn't match.
    assert _is_jump_race("なにか特別", None, "ＪＧ３")
    assert _is_jump_race(None, None, "JG2")


def test_flat_races_are_not_jumps() -> None:
    assert not _is_jump_race("未勝利", None, "未勝利")
    assert not _is_jump_race("天皇賞", "天皇賞（春）", "Ｇ１")   # 3200m flat stayer
    assert not _is_jump_race("ステイヤーズＳ", None, "Ｇ２")     # 3600m flat
    assert not _is_jump_race("ダイヤモンドＳ", None, "Ｇ３")     # 3400m flat


def test_derive_track_type_overrides_to_jump() -> None:
    # jump on a turf course: col14=芝 but name says 障害 -> stored as 障
    assert _derive_track_type(_row(name_short="障害未勝利", track_type="芝")) == "障"
    # jump on a dirt course -> still 障
    assert _derive_track_type(_row(name_short="障害オープン", track_type="ダ")) == "障"


def test_derive_track_type_keeps_flat_surface() -> None:
    assert _derive_track_type(_row(name_short="未勝利", track_type="芝")) == "芝"
    assert _derive_track_type(_row(name_short="３歳未勝利", track_type="ダ")) == "ダ"
    # empty col14 stays None (no fabricated surface)
    assert _derive_track_type(_row(name_short="未勝利", track_type="")) is None
