"""176 T082: jra-fetch keeps raw + manifest for archivable kinds only and writes no table."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json

from horseracing_scrape.jra.audit import load_saved_pages, run_fetch
from tests._jra_fake import FakeJraFetcher

D = datetime.date(2026, 11, 1)


def _args(tmp_path, kinds):
    return argparse.Namespace(
        save_dir=str(tmp_path / "raw"), kinds=kinds, venue="05", dry_run=False
    )


def test_fetch_saves_raw_and_manifest(tmp_path):
    f = FakeJraFetcher()
    r = run_fetch(_args(tmp_path, "entries,changes,baba"), fetcher=f, date=D)
    assert r.exit_code == 0 and r.jobs == []
    doc = json.loads((tmp_path / "raw" / "manifest.json").read_text())
    kinds = [p["kind"] for p in doc["pages"]]
    assert kinds == ["entries", "changes", "baba_cushion", "baba_moist", "accessJ"]
    for p in doc["pages"]:
        raw = (tmp_path / "raw" / p["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == p["raw_sha256"]
        assert "cname" not in json.dumps(p).replace("cname_sha256", "")  # no plain cname
    assert r.requests_sent == f.requests_sent
    saved_date, pages = load_saved_pages(tmp_path / "raw")
    assert saved_date == D and pages[0].kind == "entries" and "race_header" in pages[0].text


def test_odds_are_parsed_but_never_saved(tmp_path):
    f = FakeJraFetcher()
    r = run_fetch(_args(tmp_path, "odds"), fetcher=f, date=D)
    doc = json.loads((tmp_path / "raw" / "manifest.json").read_text())
    assert doc["pages"] == [] and doc["odds_parsed_not_saved"] == 12
    assert list((tmp_path / "raw").iterdir()) == [tmp_path / "raw" / "manifest.json"]
    assert r.exit_code == 0


def test_existing_manifest_and_unknown_kind_are_refused(tmp_path):
    run_fetch(_args(tmp_path, "changes"), fetcher=FakeJraFetcher(), date=D)
    again = run_fetch(_args(tmp_path, "changes"), fetcher=FakeJraFetcher(), date=D)
    assert again.exit_code == 2
    bad = run_fetch(
        argparse.Namespace(save_dir=str(tmp_path / "x"), kinds="laps", venue=None),
        fetcher=FakeJraFetcher(),
        date=D,
    )
    assert bad.exit_code == 2 and bad.requests_sent == 0


def test_tampered_file_is_rejected(tmp_path):
    run_fetch(_args(tmp_path, "changes"), fetcher=FakeJraFetcher(), date=D)
    doc = json.loads((tmp_path / "raw" / "manifest.json").read_text())
    (tmp_path / "raw" / doc["pages"][0]["file"]).write_bytes(b"tampered")
    import pytest

    from horseracing_scrape.jra.audit import ManifestError

    with pytest.raises(ManifestError):
        load_saved_pages(tmp_path / "raw")
