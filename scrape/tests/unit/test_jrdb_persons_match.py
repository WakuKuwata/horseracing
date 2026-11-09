"""178 T026: name+birth date → mapped; name only / birth only → conflict; nothing → unmapped;
the report's denominator is the unique codes in the data."""

from __future__ import annotations

import datetime

from horseracing_scrape.jra.person_profile import JraPersonProfile
from horseracing_scrape.jrdb import persons
from horseracing_scrape.jrdb.models import JrdbPerson


def _p(code="10076", name="岡部幸雄", birth=datetime.date(1948, 10, 31), kind="jockey", aff=1):
    return JrdbPerson(
        kind=kind,
        code=code,
        retired=False,
        retired_date=None,
        name=name,
        kana=None,
        short_name=None,
        affiliation_code=aff,
        region_name=None,
        birth_date=birth,
        first_license_year=1967,
        data_date=None,
    )


def _prof(
    cid="00140", name="岡部　幸雄", birth=datetime.date(1948, 10, 31), kind="jockey", aff="美浦"
):
    return JraPersonProfile(kind, cid, name, birth, aff)


def test_name_and_birth_date_map_even_with_spacing_differences():
    m = persons.match(_p(), [_prof(), _prof("00141", "横山典弘", datetime.date(1968, 2, 23))])
    assert m.status == "mapped" and m.canonical_id == "00140" and "name+birth_date" in m.note


def test_affiliation_difference_is_a_note_not_a_rejection():
    m = persons.match(_p(aff=2), [_prof(aff="美浦")])
    assert m.status == "mapped" and "affiliation differs" in m.note


def test_name_only_is_a_conflict():
    assert persons.match(_p(birth=datetime.date(1950, 1, 1)), [_prof()]).status == "conflict"
    assert persons.match(_p(birth=None), [_prof()]).status == "conflict"
    assert persons.match(_p(), [_prof(birth=None)]).status == "conflict"


def test_same_birth_date_different_name_is_a_conflict_with_reason():
    m = persons.match(_p(name="岡部一郎"), [_prof()])
    assert m.status == "conflict" and "birth date only" in m.note


def test_two_profiles_sharing_name_and_birth_are_a_conflict():
    m = persons.match(_p(), [_prof("00140"), _prof("00999")])
    assert m.status == "conflict" and m.canonical_id is None


def test_no_candidate_or_other_kind_is_unmapped():
    assert persons.match(_p(), []).status == "unmapped"
    assert persons.match(_p(), [_prof(kind="trainer")]).status == "unmapped"
    assert (
        persons.match(_p(name="別人", birth=datetime.date(1900, 1, 1)), [_prof()]).status
        == "unmapped"
    )


def test_report_denominator_is_unique_codes_in_the_data_by_kind_and_decade():
    codes = persons.codes_from_rows(
        [
            ("jockey", "10076", 2008),
            ("jockey", "10076", 2009),  # appearances are not weighted
            ("jockey", "10077", 2015),
            ("jockey", "10078", 2021),  # not in the master at all
            ("trainer", "10379", 2008),
            ("jockey", None, 2008),
        ]
    )
    master = [
        _p("10076"),
        _p("10077", "横山典弘", datetime.date(1968, 2, 23)),
        _p("10379", "中尾秀正", datetime.date(1950, 3, 3), kind="trainer"),
    ]
    profiles = [_prof(), _prof("00141", "横山典弘", datetime.date(1960, 1, 1))]  # birth differs
    rep = persons.build_report(master, profiles, codes_in_data=codes)
    assert rep.jockey.denominator == 3 and rep.trainer.denominator == 1
    assert rep.jockey.mapped == 1 and rep.jockey.conflict == 1 and rep.jockey.not_in_master == 1
    assert rep.trainer.unmapped == 1
    assert rep.jockey.by_decade == {
        "2000s": {"denominator": 2, "mapped": 2},
        "2010s": {"denominator": 1, "mapped": 0},
        "2020s": {"denominator": 1, "mapped": 0},
    }
    j = rep.to_json()
    assert j["shortfall"] == {"jockey": 2, "trainer": 1}
    assert j["jockey"]["mapped_rate"] == 1 / 3
    assert any(e["code"] == "10077" for e in rep.examples["conflict"])
    # profiles fetched (2) are not the denominator, nor the matched set (1)
    assert rep.jockey.denominator not in (len(profiles), rep.jockey.mapped)


def test_apply_manual_maps_only_approved_codes_in_the_denominator():
    """R3 (2026-11-09): 地方・短期免許の人物は名鑑に無く名前だけの一致になる。利用者承認の JSON だけが
    mapped にでき、名前+生年月日で既に mapped の code と分母外の code は触らない。"""

    import datetime

    from horseracing_scrape.jra.person_profile import JraPersonProfile
    from horseracing_scrape.jrdb.models import JrdbPerson
    from horseracing_scrape.jrdb.persons import apply_manual, build_report

    def person(code, name, birth):
        return JrdbPerson(
            kind="jockey",
            code=code,
            retired=False,
            retired_date=None,
            name=name,
            kana=None,
            short_name=None,
            affiliation_code=3,
            region_name="地方",
            birth_date=birth,
            first_license_year=None,
            data_date=None,
        )

    persons = [
        person("10001", "甲 太郎", datetime.date(1990, 1, 1)),
        person("30001", "乙 次郎", datetime.date(1991, 2, 2)),
        person("30002", "丙 三郎", datetime.date(1992, 3, 3)),
    ]
    profiles = [JraPersonProfile("jockey", "01001", "甲 太郎", datetime.date(1990, 1, 1), "美浦")]
    rep = build_report(
        persons,
        profiles,
        codes_in_data={"jockey": {"10001": {2026}, "30001": {2026}}, "trainer": {}},
    )
    assert rep.jockey.mapped == 1 and rep.jockey.unmapped == 1
    counts = apply_manual(
        rep,
        {"jockey": {"30001": "05999", "10001": "01001", "30002": "05998"}},
        note="operator approval 2026-11-09",
    )
    assert counts == {"applied": 1, "already_mapped": 1, "not_in_denominator": 1}
    assert rep.jockey.mapped == 2 and rep.jockey.unmapped == 0
    m = rep.matches[("jockey", "30001")]
    assert m.status == "mapped" and m.canonical_id == "05999" and m.note.startswith("manual:")
