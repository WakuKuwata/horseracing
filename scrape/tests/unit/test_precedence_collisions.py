"""176 T012: contracts/precedence.md §2 — collision check (pure) and merge-candidate pairing."""

from __future__ import annotations

from horseracing_db.enums import EntryStatus

from horseracing_scrape.precedence import (
    COLLISION_EXISTING_DUP,
    COLLISION_ID,
    COLLISION_INPUT_DUP,
    COLLISION_NULL_NUMBER,
    COLLISION_NUMBER,
    COLLISION_SURPLUS,
    EntryRow,
    HorseIdentity,
    check_entry_collisions,
    pair_merge_candidates,
)

S, C = EntryStatus.STARTED, EntryStatus.CANCELLED


def row(hid, num, status=S):
    return EntryRow(hid, num, status)


def test_clean_field_has_no_collision():
    ex = [row("A", 1), row("B", 2)]
    inc = [row("A", 1), row("B", 2)]
    rep = check_entry_collisions(ex, inc)
    assert not rep.quarantined and rep.collisions == ()


def test_same_number_different_id():
    rep = check_entry_collisions([row("nk:1", 5)], [row("2022105102", 5)])
    # the nk: row is also a started row absent from the input → surplus is reported too
    assert set(rep.kinds()) == {COLLISION_NUMBER, COLLISION_SURPLUS}
    c = next(c for c in rep.collisions if c.kind == COLLISION_NUMBER)
    assert (c.horse_number, c.existing_horse_id, c.incoming_horse_id) == (5, "nk:1", "2022105102")


def test_same_id_different_number():
    rep = check_entry_collisions([row("A", 5)], [row("A", 6)])
    assert rep.kinds() == (COLLISION_ID,)


def test_cancelled_lost_number_is_not_an_id_collision():
    # 取消で馬番セルが消えた馬: incoming number None → §2 の除外
    rep = check_entry_collisions([row("A", 5)], [row("A", None, C)])
    assert COLLISION_ID not in rep.kinds()
    # and the existing started row A is present in the input, so no surplus either
    assert not rep.quarantined


def test_existing_duplicate_number_among_started():
    rep = check_entry_collisions([row("A", 3), row("B", 3)], [row("A", 3), row("B", 4)])
    assert COLLISION_EXISTING_DUP in rep.kinds()


def test_existing_null_number_started():
    rep = check_entry_collisions([row("A", None)], [row("A", 1)])
    assert COLLISION_NULL_NUMBER in rep.kinds()


def test_input_duplicates_number_and_id():
    rep = check_entry_collisions([], [row("A", 1), row("B", 1)])
    assert rep.kinds() == (COLLISION_INPUT_DUP,)
    rep = check_entry_collisions([], [row("A", 1), row("A", 2)])
    assert COLLISION_INPUT_DUP in rep.kinds()


def test_empty_existing_only_checks_input():
    rep = check_entry_collisions([], [row("A", 1), row("B", 2)])
    assert not rep.quarantined


def test_surplus_existing_started_not_in_input():
    rep = check_entry_collisions([row("A", 1), row("B", 2)], [row("A", 1)])
    assert rep.kinds() == (COLLISION_SURPLUS,)
    # a cancelled existing row missing from the input is NOT surplus
    rep = check_entry_collisions([row("A", 1), row("B", 2, C)], [row("A", 1)])
    assert not rep.quarantined


def test_one_hit_is_enough_to_quarantine():
    ex = [row("nk:9", 7)] + [row(f"H{i}", i) for i in range(1, 7)]
    inc = [row("2024100007", 7)] + [row(f"H{i}", i) for i in range(1, 7)]
    assert check_entry_collisions(ex, inc).quarantined


def test_merge_candidates_need_name_and_birth_year():
    ex = [
        HorseIdentity("nk:1", "サヴォーナ", 2020),
        HorseIdentity("nk:2", "サヴォーナ", None),
        HorseIdentity("2019100001", "別馬", 2019),
    ]
    inc = [HorseIdentity("2020100734", "サヴォーナ", 2020), HorseIdentity("2020100999", "", 2020)]
    merge, pending = pair_merge_candidates(ex, inc)
    assert merge == (("nk:1", "2020100734"),)
    assert pending == ()


def test_approved_mapping_is_reported_separately_and_not_merged():
    ex = [HorseIdentity("nk:1", "サヴォーナ", 2020)]
    inc = [HorseIdentity("2020100734", "サヴォーナ", 2020)]
    merge, pending = pair_merge_candidates(ex, inc, approved=[("nk:1", "2020100734")])
    assert merge == () and pending == (("nk:1", "2020100734"),)
