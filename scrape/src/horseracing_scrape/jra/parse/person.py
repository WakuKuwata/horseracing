"""騎手/調教師ページの解析 (Feature 178, T032).

The page layout was not captured during the 176 survey (only the race pages were), so this parser
reads the facts by their LABELS (生年月日 / 所属 / the name heading) rather than by a fixed DOM
path, and tolerates either a definition list or a table. It raises :class:`ParseError` when the
birth date is absent — a profile without a birth date cannot anchor an identity (憲法 I) and is
not written. The synthetic test fixture mirrors this label-based contract; the first real page
(rollout R3) confirms it before the 1,500-page collection starts.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from ...models import ParseError
from ..identity import JOCKEY, TRAINER
from ._common import nfkc, soup

_BIRTH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_AFFIL_RE = re.compile(r"(美浦|栗東|地方|海外|外国|招待|フリー)")
_LABEL_BIRTH = re.compile(r"生年月日")
_LABEL_AFFIL = re.compile(r"所属")


@dataclass(frozen=True)
class ParsedPerson:
    kind: str
    canonical_id: str
    name: str | None
    birth_date: datetime.date | None
    affiliation: str | None


def _labelled_values(doc) -> dict[str, str]:
    """``{label: value}`` from <dt>/<dd> pairs and from 2-cell <tr> rows."""

    out: dict[str, str] = {}
    for dt in doc.select("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            out[nfkc(dt.get_text(" ", strip=True))] = nfkc(dd.get_text(" ", strip=True))
    for tr in doc.select("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2:
            out[nfkc(cells[0].get_text(" ", strip=True))] = nfkc(cells[1].get_text(" ", strip=True))
    return out


def _name(doc) -> str | None:
    for sel in ("h1", ".name", ".profile_name", "h2"):
        el = doc.select_one(sel)
        if el is not None:
            t = nfkc(el.get_text(" ", strip=True))
            t = re.sub(r"(騎手|調教師)(プロフィール|情報)?$", "", t).strip()
            if t:
                return t
    return None


def parse_person_profile(html: str, *, kind: str, canonical_id: str) -> ParsedPerson:
    if kind not in (JOCKEY, TRAINER):
        raise ValueError("kind must be jockey or trainer")
    doc = soup(html)
    values = _labelled_values(doc)
    birth = None
    affiliation = None
    for raw_label, value in values.items():
        label = re.sub(r"\s+", "", raw_label)  # 「生 年 月 日」 → 「生年月日」
        if _LABEL_BIRTH.search(label):
            m = _BIRTH_RE.search(value)
            if m:
                try:
                    birth = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    raise ParseError(f"person {canonical_id}: 生年月日 is not a date") from None
        elif _LABEL_AFFIL.search(label):
            am = _AFFIL_RE.search(value)
            affiliation = am.group(1) if am else (value or None)
    if birth is None:
        # fall back to any 生年月日 text in the document body
        text = nfkc(doc.get_text(" ", strip=True))
        m = re.search(r"生年月日[^\d]{0,10}(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
        if m:
            birth = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if birth is None:
        raise ParseError(f"person {canonical_id}: 生年月日 missing")
    return ParsedPerson(
        kind=kind,
        canonical_id=canonical_id,
        name=_name(doc),
        birth_date=birth,
        affiliation=affiliation,
    )


__all__ = ["ParsedPerson", "parse_person_profile"]
