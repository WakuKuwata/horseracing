"""178 T025: file identity and the two automatic orderings (速報→正式, 日次→バックナンバー); everything
else is held and Last-Modified never decides."""

from __future__ import annotations

import datetime

import pytest

from horseracing_scrape.jrdb import files
from horseracing_scrape.jrdb.errors import JrdbDecodeError
from horseracing_scrape.jrdb.models import JrdbFileMeta

JST = datetime.timezone(datetime.timedelta(hours=9))
D = datetime.date(2026, 11, 7)  # a Saturday


def _meta(kind="SED", sha="a", variant="daily", edition=None, data_date=D) -> JrdbFileMeta:
    return JrdbFileMeta(kind, "4", data_date, variant, sha, 10, 376, edition=edition)


def test_parse_file_name_and_version():
    assert files.parse_file_name("KYI150712.txt", reference=datetime.date(2015, 7, 12)) == (
        "KYI",
        "11",
        datetime.date(2015, 7, 12),
        "daily",
    )
    assert (
        files.parse_file_name(
            "sed/SED080913.txt", variant="archive", reference=datetime.date(2008, 9, 13)
        )[3]
        == "archive"
    )
    assert files.parse_file_name("KYH080913.txt", reference=datetime.date(2008, 9, 13))[1] == "10"
    assert files.parse_file_name("TYB_E.txt", data_date=D) == ("TYB", "4", D, "venue_e")
    with pytest.raises(JrdbDecodeError):
        files.parse_file_name("TYB_E.txt")
    with pytest.raises(JrdbDecodeError):
        files.parse_file_name("readme.txt")


def test_detect_record_length_counts_the_two_byte_terminator():
    assert files.detect_record_length(b"a" * 374 + b"\r\n" + b"b" * 374 + b"\r\n") == 376
    assert files.detect_record_length(b"a" * 442 + b"\n") == 444  # CR dropped by the delivery
    assert files.detect_record_length(b"") is None


def test_sed_edition_is_decided_by_the_schedule_not_by_headers():
    sat = datetime.date(2026, 11, 7)
    assert files.sed_edition(sat, datetime.datetime(2026, 11, 7, 18, 5, tzinfo=JST)) == "sokuho"
    assert (
        files.sed_edition(sat, datetime.datetime(2026, 11, 9, 10, 0, tzinfo=JST)) == "sokuho"
    )  # Monday
    assert files.sed_edition(sat, datetime.datetime(2026, 11, 12, 15, 59, tzinfo=JST)) == "sokuho"
    assert files.sed_edition(sat, datetime.datetime(2026, 11, 12, 16, 0, tzinfo=JST)) == "formal"
    assert files.sed_edition(sat, datetime.datetime(2027, 1, 1, 0, 0, tzinfo=JST)) == "formal"
    sun = datetime.date(2026, 11, 8)
    assert files.sed_edition(sun, datetime.datetime(2026, 11, 12, 16, 0, tzinfo=JST)) == "formal"


def test_build_meta_sets_edition_only_for_sed_and_archives_are_formal():
    raw = b"x" * 374 + b"\r\n"
    m = files.build_meta(
        "SED261107.txt",
        raw,
        variant="daily",
        fetched_at=datetime.datetime(2026, 11, 7, 18, 5, tzinfo=JST),
    )
    assert m.kind == "SED" and m.edition == "sokuho" and m.record_length == 376
    m = files.build_meta(
        "SED261107.txt",
        raw,
        variant="archive",
        fetched_at=datetime.datetime(2026, 11, 7, 18, 5, tzinfo=JST),
    )
    assert m.edition == "formal"
    m = files.build_meta(
        "KYI261107.txt",
        b"y" * 1022 + b"\r\n",
        variant="daily",
        fetched_at=datetime.datetime(2026, 11, 6, 20, 0, tzinfo=JST),
    )
    assert m.edition is None and m.record_length == 1024


def test_classify_same_apply_supersede_held():
    assert files.classify_version(None, _meta()) == "apply"
    assert files.classify_version(_meta(sha="a"), _meta(sha="a", edition="formal")) == "same"
    # 速報 → 正式
    assert (
        files.classify_version(_meta(sha="a", edition="sokuho"), _meta(sha="b", edition="formal"))
        == "supersede"
    )
    # 正式 → 速報 (older content arriving later) is held
    assert (
        files.classify_version(_meta(sha="a", edition="formal"), _meta(sha="b", edition="sokuho"))
        == "held"
    )
    # 正式 → 正式 with different content: held (an operator decides)
    assert (
        files.classify_version(_meta(sha="a", edition="formal"), _meta(sha="b", edition="formal"))
        == "held"
    )
    # 日次 → バックナンバー supersedes for every kind; the reverse is held
    assert (
        files.classify_version(_meta("KYI", "a"), _meta("KYI", "b", variant="archive"))
        == "supersede"
    )
    assert files.classify_version(_meta("KYI", "a", variant="archive"), _meta("KYI", "b")) == "held"
    # a differing daily KYI (no edition concept) is held
    assert files.classify_version(_meta("KYI", "a"), _meta("KYI", "b")) == "held"
    with pytest.raises(ValueError):
        files.classify_version(_meta("KYI", "a"), _meta("SED", "b"))


def test_published_at_is_not_an_input_of_the_decision():
    import inspect

    sig = inspect.signature(files.classify_version)
    assert list(sig.parameters) == ["existing", "incoming"]
    assert "published_at" not in JrdbFileMeta.__dataclass_fields__


def test_version_family_puts_daily_and_archive_together_and_tyb_venues_apart():
    """R7 (2026-11-09): the year pack and a later 日次 file of the same day compete for the same
    slot (日次→バックナンバー), the TYB 場別上書き files do not."""

    assert files.version_family("daily") == files.version_family("archive") == "day"
    assert files.version_family("venue_e") == "venue_e" != files.version_family("venue_w")


def test_rebuild_loader_reads_second_version_file_names(tmp_path):
    """``BAC251213.<sha12>.txt`` (the 2nd version saved next to ``BAC251213.txt``) is the same
    (kind, day) to the rebuild and the repair loaders."""

    from horseracing_scrape.jrdb.rebuild import load_archive_days, plain_file_name

    d = tmp_path / "BAC" / "20251213"
    d.mkdir(parents=True)
    (d / "BAC251213.txt").write_bytes(b"x" * 10)
    (d / "BAC251213.52cfbb39a855.txt").write_bytes(b"y" * 10)
    assert plain_file_name(d / "BAC251213.52cfbb39a855.txt") == "BAC251213.txt"
    by_day, skipped = load_archive_days(
        tmp_path, datetime.date(2025, 12, 1), datetime.date(2025, 12, 31)
    )
    assert sorted(f.path.name for f in by_day[datetime.date(2025, 12, 13)]) == [
        "BAC251213.52cfbb39a855.txt",
        "BAC251213.txt",
    ]
    assert skipped == []
