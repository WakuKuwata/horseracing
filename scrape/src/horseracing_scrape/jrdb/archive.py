"""Archive extraction for JRDB deliveries (Feature 178, T007; research D4).

zip is the preferred (and, for back-numbers, the only) format and needs nothing beyond the
standard library. ``.lzh`` (the legacy "最近一週間" listing and the old KYH/SEC formats) needs the
optional dependency ``lhafile``; without it the caller gets a typed :class:`JrdbLzhUnsupported`
instead of a bare ImportError.
"""

from __future__ import annotations

import io
import zipfile

from .errors import JrdbDecodeError, JrdbLzhUnsupported

#: hard cap on a single extracted member (the largest yearly pack member, KYI for a whole year,
#: is well under 100 MB); protects against a hostile/broken archive
MAX_MEMBER_BYTES = 512 * 1024 * 1024


def _check_size(name: str, size: int) -> None:
    if size > MAX_MEMBER_BYTES:
        raise JrdbDecodeError(f"archive member {name!r} too large ({size} bytes)")


def extract_zip(data: bytes) -> list[tuple[str, bytes]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise JrdbDecodeError(f"not a zip archive: {exc}") from exc
    out: list[tuple[str, bytes]] = []
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            _check_size(info.filename, info.file_size)
            out.append((info.filename.rsplit("/", 1)[-1], zf.read(info)))
    return out


def lzh_available() -> bool:
    try:
        import lhafile  # noqa: F401
    except ImportError:
        return False
    return True


def extract_lzh(data: bytes) -> list[tuple[str, bytes]]:
    try:
        import lhafile
    except ImportError as exc:
        raise JrdbLzhUnsupported(
            "lzh archive given but the optional dependency 'lhafile' is not installed "
            "(install the scrape package with the [lzh] extra, or use the zip delivery)"
        ) from exc
    try:
        lf = lhafile.Lhafile(io.BytesIO(data))
    except Exception as exc:  # lhafile raises its own BadLhafile
        raise JrdbDecodeError(f"not an lzh archive: {exc}") from exc
    out: list[tuple[str, bytes]] = []
    for info in lf.infolist():
        _check_size(info.filename, info.file_size)
        out.append((info.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1], lf.read(info.filename)))
    return out


def extract(data: bytes, name: str) -> list[tuple[str, bytes]]:
    """Return ``[(member_name, bytes), ...]`` for a zip/lzh archive; a bare ``.txt`` is returned
    as a single member."""

    lower = name.lower()
    if lower.endswith(".zip"):
        return extract_zip(data)
    if lower.endswith(".lzh"):
        return extract_lzh(data)
    if lower.endswith(".txt"):
        return [(name.rsplit("/", 1)[-1], data)]
    raise JrdbDecodeError(f"unknown archive type for {name!r}")


__all__ = ["MAX_MEMBER_BYTES", "extract", "extract_lzh", "extract_zip", "lzh_available"]
