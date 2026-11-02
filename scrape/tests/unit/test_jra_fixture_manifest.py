"""176 T003: the JRA fixtures are raw bytes whose sha256 matches the manifest, and no dynamic cname
is stored in plaintext anywhere a run could read it back (FR-001 / FR-007 / SC-007 の前提)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from tests.jra_fixtures import JRA_FIXTURES, jra_text, manifest

#: A dynamic NAVIGATION cname looks like ``pw01des`` + the day/race payload + ``/XX`` (page
#: prefix + 20-odd digits + check value). Fixed index entry points (pw01dli00/F3 etc.) are allowed as code
#: constants, and entity links (pw01dud00<馬 ID>, pw04kmk0<騎手>, pw05cmk0<調教師>) are ids, not
#: navigation — tests may spell those out.
_DYNAMIC_CNAME = re.compile(
    r"pw(?:01des|01drl|01ses|01srl|01hde|15\dou|15\dop)[0-9A-Za-z]{8,}/[0-9A-F]{2}"
)

_ALLOWED_CHARSETS = {"shift_jis", "ascii", "utf-8"}


def _entries():
    m = manifest()
    return list(m["fixtures"]) + list(m.get("derived", []))


@pytest.mark.parametrize("e", _entries(), ids=lambda e: e["file"])
def test_raw_sha256_matches(e):
    data = (JRA_FIXTURES / e["file"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == e["raw_sha256"], e["file"]
    assert e["charset"] in _ALLOWED_CHARSETS


def test_manifest_has_no_plaintext_cname():
    text = (JRA_FIXTURES / "manifest.json").read_text(encoding="utf-8")
    assert not _DYNAMIC_CNAME.search(text), "manifest must carry cname_sha256 only"
    for e in manifest()["fixtures"]:
        assert "cname" not in e, e["file"]
        if e.get("cname_sha256") is not None:
            assert re.fullmatch(r"[0-9a-f]{64}", e["cname_sha256"])


def test_test_code_has_no_dynamic_cname():
    """Tests may reference fixture FILES, never the cname that fetched them."""
    here = Path(__file__).resolve().parent
    offenders = []
    for py in list(here.glob("test_jra_*.py")) + list(
        (here.parent / "integration").glob("test_jra_*.py")
    ):
        if _DYNAMIC_CNAME.search(py.read_text(encoding="utf-8")):
            offenders.append(py.name)
    assert offenders == []


def test_fixtures_decode_strictly():
    """Every Shift_JIS fixture must decode with cp932 strict — the fetcher refuses anything else."""
    for e in _entries():
        if e["charset"] == "shift_jis":
            jra_text(e["file"])  # raises UnicodeDecodeError on a corrupt byte


def test_initial_set_present():
    kinds = {e["kind"] for e in manifest()["fixtures"]}
    assert {
        "index_d",
        "daylist_d",
        "entries",
        "changes",
        "odds_win",
        "index_s",
        "daylist_s",
        "results",
        "index_h",
        "payouts",
        "baba_cushion",
        "baba_moist",
        "accessJ",
        "error_param",
        "error_redirect",
    } <= kinds
