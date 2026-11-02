"""176 T006: official-id namespace contract (FR-009) and the audit-only name key (FR-010)."""

from __future__ import annotations

import pytest

from horseracing_scrape.jra.identity import HORSE, JOCKEY, TRAINER, jra_name_key, official_id


def test_horse_id_from_href_keeps_leading_zero_and_drops_check_value():
    assert official_id(HORSE, "/JRADB/accessU.html?CNAME=pw01dud000024107186/DA") == "0024107186"
    assert official_id(HORSE, "/JRADB/accessU.html?CNAME=pw01dud002022105102/98") == "2022105102"


def test_jockey_trainer_ids_from_onclick():
    assert (
        official_id(JOCKEY, "return doAction('/JRADB/accessK.html', 'pw04kmk005339/DD');")
        == "05339"
    )
    assert (
        official_id(TRAINER, "return doAction('/JRADB/accessC.html', 'pw05cmk001165/F9');")
        == "01165"
    )


@pytest.mark.parametrize(
    "kind, href",
    [
        (HORSE, None),
        (HORSE, "pw01dud00202210510/98"),  # 9 digits
        (HORSE, "pw01dud0020221051023/98"),  # 11 digits
        (HORSE, "pw01dud00202210510X/98"),  # non-digit
        (HORSE, "pw04kmk005339/DD"),  # jockey prefix given as horse
        (JOCKEY, "pw05cmk001165/F9"),  # trainer prefix given as jockey
        (JOCKEY, "pw04kmk00533/DD"),  # 4 digits
        (TRAINER, "xpw05cmk001165/F9"),  # prefix not on a boundary
    ],
)
def test_unknown_ids_return_none(kind, href):
    assert official_id(kind, href) is None


def test_same_number_is_a_different_entity_per_kind():
    j = official_id(JOCKEY, "pw04kmk001165/00")
    t = official_id(TRAINER, "pw05cmk001165/00")
    assert j == t == "01165"  # the digits coincide …
    assert official_id(JOCKEY, "pw05cmk001165/00") is None  # … but the kinds never cross


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        official_id("owner", "pw01dud002022105102/98")


def test_name_key_removes_all_whitespace_and_markers():
    assert jra_name_key("和田 勇介") == "和田勇介"
    assert jra_name_key("和田　勇介") == "和田勇介"
    assert jra_name_key("△和田勇介") == "和田勇介"
    assert jra_name_key("Ｃ．ルメール") == "C.ルメール"
    assert jra_name_key(None) == ""
