"""178 T003: the public-sample fixtures match their manifest, and nothing member-only leaked into
``scrape/`` (FR-022)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from horseracing_scrape.jrdb.decode import split_records

SCRAPE_DIR = Path(__file__).resolve().parents[2]
PUBLIC = SCRAPE_DIR / "tests" / "fixtures" / "jrdb" / "public"
MANIFEST = json.loads((PUBLIC / "manifest.json").read_text(encoding="utf-8"))

#: the only file allowed to spell the member host path: it builds the URLs (contracts §1.1)
_URL_BUILDER = SCRAPE_DIR / "src" / "horseracing_scrape" / "jrdb" / "fetch.py"
_MEMBER_PATH = "jrdb.com/" + "member/"  # two parts so this file passes its own scan
_CREDENTIAL_VALUE = re.compile(r"JRDB_MEMBER_(USER|PASSWORD)\s*[=:]\s*[\"']?[^\s\"',)]+")


def test_manifest_lists_every_fixture_and_nothing_else():
    files = {p.name for p in PUBLIC.iterdir() if p.suffix == ".txt"}
    assert files == {e["file"] for e in MANIFEST["entries"]}
    assert len(files) == 9


@pytest.mark.parametrize("entry", MANIFEST["entries"], ids=lambda e: e["file"])
def test_raw_sha256_matches(entry):
    raw = (PUBLIC / entry["file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == entry["raw_sha256"]
    assert len(raw) == entry["size_bytes"]


@pytest.mark.parametrize(
    "entry", [e for e in MANIFEST["entries"] if e["record_length"]], ids=lambda e: e["file"]
)
def test_every_row_has_the_documented_data_length(entry):
    raw = (PUBLIC / entry["file"]).read_bytes()
    records, rejected, framing = split_records(raw, entry["record_length"] - 2)
    assert not rejected
    assert len(records) == entry["rows"]
    assert framing.terminator == entry["line_terminator"]
    assert framing.extra_blank_lines == 0
    # each data part decodes as cp932 (strict)
    for _line_no, _record_no, data in records:
        data.decode("cp932")


def test_record_lengths_are_the_documented_ones():
    doc = {
        "BAC": 184,
        "KYI": 1024,
        "UKC": 292,
        "KSA": 272,
        "TYB": 128,
        "HJC": 444,
        "SED": 376,
        "SRB": 852,
    }
    by_kind = {e["kind"]: e["record_length"] for e in MANIFEST["entries"] if e["record_length"]}
    assert by_kind == doc


def test_no_member_url_or_credential_value_in_scrape_tree():
    offenders: list[str] = []
    for path in SCRAPE_DIR.rglob("*"):
        if not path.is_file():
            continue
        if any(
            part in {".venv", "__pycache__", ".pytest_cache", ".ruff_cache"} for part in path.parts
        ):
            continue
        if path.suffix not in {".py", ".json", ".txt", ".md", ".toml", ".html", ".sh"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_bytes().decode("cp932", errors="ignore")
        rel = str(path.relative_to(SCRAPE_DIR))
        if _MEMBER_PATH in text and path != _URL_BUILDER:
            offenders.append(f"{rel}: member URL path")
        if _CREDENTIAL_VALUE.search(text):
            offenders.append(f"{rel}: credential value")
    assert offenders == [], offenders
