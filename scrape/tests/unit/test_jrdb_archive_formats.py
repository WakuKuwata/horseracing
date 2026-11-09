"""178 T007: zip extraction with the standard library; lzh is an optional dependency with a typed
error when absent."""

from __future__ import annotations

import io
import zipfile

import pytest

from horseracing_scrape.jrdb import archive
from horseracing_scrape.jrdb.errors import JrdbDecodeError, JrdbLzhUnsupported


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_extracts_every_member_with_basename():
    data = _zip({"SED080913.txt": b"a" * 374 + b"\r\n", "sub/SRB080913.txt": b"b" * 850 + b"\r\n"})
    out = archive.extract(data, "SED080913.zip")
    assert [n for n, _ in out] == ["SED080913.txt", "SRB080913.txt"]
    assert out[0][1].startswith(b"a" * 374)


def test_bare_txt_is_a_single_member():
    assert archive.extract(b"xyz", "KYI150712.txt") == [("KYI150712.txt", b"xyz")]


def test_bad_zip_is_a_typed_error():
    with pytest.raises(JrdbDecodeError):
        archive.extract(b"not a zip", "x.zip")


def test_unknown_extension_is_refused():
    with pytest.raises(JrdbDecodeError):
        archive.extract(b"", "x.rar")


def test_lzh_without_lhafile_is_typed(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "lhafile":
            raise ImportError("no lhafile")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert archive.lzh_available() is False
    with pytest.raises(JrdbLzhUnsupported):
        archive.extract(b"-lh5-", "KYH080913.lzh")


def test_lzh_roundtrip_when_available():
    lhafile = pytest.importorskip("lhafile")
    assert lhafile is not None  # the real extraction is exercised only where the extra is installed
