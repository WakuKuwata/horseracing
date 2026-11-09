"""Fixed-length record layouts (Feature 178, T005).

A ``Layout`` is a transcription of one public specification (``*_doc.txt`` under
``https://jrdb.com/program/``). Positions are the document's 相対 (1-based); the decoder turns
them into byte slices. The doc's レコード長 includes the trailing 2-byte 改行 (ＣＲ・ＬＦ), so
the data part is ``record_length - 2`` bytes.

``target`` decides what the decoder does with a field (research D7a):

* ``current``        — adopted column, decoded under every profile
* ``canonical_only`` — JRDB-defined column that must not enter the current DB (first_3f outside
                       1200 m, corner_orders, running_style, sire/damsire line codes); decoded
                       only under ``profile="jrdb_canonical"``
* ``skip``           — never decoded (indices, comments, 基準オッズ, 10 時オッズ, TYB odds, …)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..errors import JrdbUnsupportedVersion

FieldType = Literal["9", "Z", "X", "F"]
Target = Literal["current", "canonical_only", "skip"]
Codec = Literal["time_m_sss", "hhmm", "yyyymmdd", "yyyy", "signed", "flag", "code"] | None


@dataclass(frozen=True)
class Field:
    """One item of the layout table.

    ``fmt`` is the doc's picture (``ZZZ9.9`` …); a ``.`` in it means the data carries a literal
    decimal point. ``scale`` divides an integer picture (``999`` in 0.1 kg → ``scale=10``).
    ``codec`` names a special decoding: ``time_m_sss`` (1 digit 分 + 3 digits 0.1 秒),
    ``hhmm``, ``yyyymmdd``, ``yyyy``, ``signed`` (``XZ9`` sign + digits with inner blanks),
    ``flag`` (``1`` → True), ``code`` (keep the text as-is, e.g. 条件 ``A3``/``OP``).
    """

    name: str
    start: int  # 1-based 相対
    length: int
    type: FieldType = "X"
    fmt: str = ""
    scale: int = 1
    codec: Codec = None
    target: Target = "current"

    @property
    def slice(self) -> slice:
        return slice(self.start - 1, self.start - 1 + self.length)

    @property
    def end(self) -> int:  # exclusive, 0-based
        return self.start - 1 + self.length


@dataclass(frozen=True)
class Layout:
    kind: str
    version: str
    record_length: int  # the doc's レコード長 (data + 2-byte terminator)
    fields: tuple[Field, ...]
    doc: str = ""  # source document (public URL + version + fetch date)
    aliases: tuple[str, ...] = field(default_factory=tuple)  # other kinds sharing this layout

    @property
    def data_length(self) -> int:
        return self.record_length - 2

    def __post_init__(self) -> None:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            dup = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"{self.kind}: duplicate field names {dup}")
        for f in self.fields:
            if f.start < 1 or f.length < 1:
                raise ValueError(f"{self.kind}.{f.name}: start/length must be positive")
            if f.end > self.data_length:
                raise ValueError(
                    f"{self.kind}.{f.name}: ends at {f.end} > data length {self.data_length}"
                )
        # adopted fields must not overlap each other (skip fields may overlap nothing either, but
        # the doc has no overlaps; checking all keeps the transcription honest)
        spans = sorted((f.start - 1, f.end, f.name) for f in self.fields)
        for (_s0, e0, n0), (s1, _e1, n1) in zip(spans, spans[1:], strict=False):
            if s1 < e0:
                raise ValueError(f"{self.kind}: fields {n0!r} and {n1!r} overlap")

    def field(self, name: str) -> Field:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    def adopted(self, *, include_canonical: bool) -> tuple[Field, ...]:
        wanted = {"current", "canonical_only"} if include_canonical else {"current"}
        return tuple(f for f in self.fields if f.target in wanted)


def _registry() -> dict[tuple[str, str], Layout]:
    # imported lazily so a transcription error surfaces as a plain ValueError at first use
    from . import bac, cza, hjc, kyi, kza, sed, srb, tyb, ukc

    out: dict[tuple[str, str], Layout] = {}
    for mod in (bac, kyi, ukc, kza, cza, tyb, hjc, sed, srb):
        lay: Layout = mod.LAYOUT
        out[(lay.kind, lay.version)] = lay
        for alias in lay.aliases:
            out[(alias, lay.version)] = lay
    return out


#: current (default) layout version per kind
CURRENT_VERSION: dict[str, str] = {
    "BAC": "4",
    "KYI": "11",
    "UKC": "3",
    "KZA": "1",
    "KSA": "1",
    "CZA": "1",
    "CSA": "1",
    "TYB": "4",
    "HJC": "4",
    "SED": "4",
    "SRB": "2",
}


def layout_for(kind: str, version: str | None = None) -> Layout:
    """Return the layout for ``(kind, version)``; ``version=None`` means the kind's current one.

    Unknown kinds (KYH/SEC legacy formats, KAB, CYB, …) and unknown versions raise
    :class:`JrdbUnsupportedVersion` — the decoder never guesses a layout (D2 fail-closed).
    """

    k = (kind or "").upper()
    v = version if version is not None else CURRENT_VERSION.get(k)
    if v is None:
        raise JrdbUnsupportedVersion(k, version)
    lay = _registry().get((k, str(v)))
    if lay is None:
        raise JrdbUnsupportedVersion(k, str(v))
    return lay


def all_layouts() -> tuple[Layout, ...]:
    seen: dict[int, Layout] = {}
    for lay in _registry().values():
        seen[id(lay)] = lay
    return tuple(seen.values())


def race_key_fields(target: Target = "current") -> tuple[Field, ...]:
    """レースキー 8 文字 (場 2・年 2・回 1・日 1 (F)・Ｒ 2) at 相対 1.

    Shared by every per-race file."""

    return (
        Field("venue", 1, 2, "9", "99", target=target),
        Field("yy", 3, 2, "9", "99", target=target),
        Field("kai", 5, 1, "9", "9", target=target),
        Field("day_hex", 6, 1, "F", "F", target=target),
        Field("r", 7, 2, "9", "99", target=target),
    )


__all__ = [
    "CURRENT_VERSION",
    "Codec",
    "Field",
    "FieldType",
    "Layout",
    "Target",
    "all_layouts",
    "layout_for",
    "race_key_fields",
]
