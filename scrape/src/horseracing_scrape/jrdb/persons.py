"""KZA/CZA ↔ JRA official profiles → id-mapping material (Feature 178, T026/T033; research D3).

* :func:`match` — one JRDB person against the official profiles: ``mapped`` only when the
  normalised name AND the birth date agree; a name-only agreement (birth date missing or
  different) or a same-birth-date-different-name candidate is ``conflict`` (an operator decides);
  no candidate is ``unmapped``. Affiliation / first licence year never decide; disagreements go
  into the note.
* :func:`build_report` — the progress report. The denominator is the set of UNIQUE codes that
  appear in the target JRDB data (2007〜 KYI/SED), by kind × decade — not the ~1,500 profiles
  fetched, not the matched set, not weighted by appearances (codex D18 #10). 99 % is a progress
  indicator; the gate for ingesting a race day is "every person of that day mapped" (T051/T052).

Writing ``id_mappings`` is US2 (T047); this module is pure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ..jra.person_profile import JraPersonProfile
from .models import JrdbPerson
from .normalize import norm_name

MappingStatus = str  # "mapped" | "conflict" | "unmapped"


@dataclass(frozen=True)
class Match:
    status: MappingStatus
    canonical_id: str | None
    note: str


def _affil_jrdb(code: int | None, region: str | None) -> str | None:
    return {1: "美浦", 2: "栗東", 3: region or "他"}.get(code) if code is not None else None


def match(jrdb: JrdbPerson, candidates: Iterable[JraPersonProfile]) -> Match:
    name = norm_name(jrdb.name)
    cands = [c for c in candidates if c.kind == jrdb.kind]
    if not cands:
        return Match("unmapped", None, "no official profile")
    same_name = [c for c in cands if norm_name(c.name) == name and name is not None]
    same_birth = [
        c for c in cands if jrdb.birth_date is not None and c.birth_date == jrdb.birth_date
    ]
    both = [c for c in same_name if c in same_birth]
    if len(both) == 1:
        c = both[0]
        notes = ["name+birth_date"]
        aff = _affil_jrdb(jrdb.affiliation_code, jrdb.region_name)
        if aff and c.affiliation and aff != c.affiliation:
            notes.append(f"affiliation differs ({aff} vs {c.affiliation})")
        return Match("mapped", c.canonical_id, "; ".join(notes))
    if len(both) > 1:
        return Match("conflict", None, f"{len(both)} profiles share name and birth date")
    if same_name:
        if jrdb.birth_date is None:
            return Match("conflict", None, "name only (JRDB birth date missing)")
        return Match("conflict", None, "name only (birth date differs)")
    if same_birth:
        return Match("conflict", None, "birth date only (name differs)")
    return Match("unmapped", None, "no name or birth-date match")


# --------------------------------------------------------------------------- report


def decade(year: int) -> str:
    return f"{year - year % 10}s"


@dataclass
class KindReport:
    denominator: int = 0
    mapped: int = 0
    conflict: int = 0
    unmapped: int = 0
    not_in_master: int = 0  # code appears in the data but no KZA/CZA row describes it
    by_decade: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def mapped_rate(self) -> float | None:
        return (self.mapped / self.denominator) if self.denominator else None

    def to_json(self) -> dict:
        return {
            "denominator": self.denominator,
            "mapped": self.mapped,
            "conflict": self.conflict,
            "unmapped": self.unmapped,
            "not_in_master": self.not_in_master,
            "mapped_rate": self.mapped_rate,
            "by_decade": self.by_decade,
        }


@dataclass
class PersonMappingReport:
    jockey: KindReport = field(default_factory=KindReport)
    trainer: KindReport = field(default_factory=KindReport)
    matches: dict[tuple[str, str], Match] = field(default_factory=dict)
    examples: dict[str, list[dict]] = field(
        default_factory=lambda: {"conflict": [], "unmapped": []}
    )

    def kind(self, k: str) -> KindReport:
        return self.jockey if k == "jockey" else self.trainer

    def to_json(self) -> dict:
        return {
            "jockey": self.jockey.to_json(),
            "trainer": self.trainer.to_json(),
            "examples": self.examples,
            "shortfall": {
                "jockey": self.jockey.denominator - self.jockey.mapped,
                "trainer": self.trainer.denominator - self.trainer.mapped,
            },
        }


def build_report(
    persons: Iterable[JrdbPerson],
    profiles: Iterable[JraPersonProfile],
    *,
    codes_in_data: Mapping[str, Mapping[str, set[int]]],
    max_examples: int = 20,
) -> PersonMappingReport:
    """``codes_in_data``: kind → {JRDB code → set of race years the code appears in}."""

    profs = list(profiles)
    by_code: dict[tuple[str, str], JrdbPerson] = {}
    for p in persons:
        by_code.setdefault((p.kind, p.code), p)
    report = PersonMappingReport()
    for kind in ("jockey", "trainer"):
        kr = report.kind(kind)
        codes = codes_in_data.get(kind, {})
        kr.denominator = len(codes)
        for code, years in sorted(codes.items()):
            person = by_code.get((kind, code))
            if person is None:
                status, m = "not_in_master", None
            else:
                m = match(person, profs)
                status = m.status
                report.matches[(kind, code)] = m
            setattr(kr, status, getattr(kr, status) + 1)
            for y in years or ():
                d = kr.by_decade.setdefault(decade(y), {"denominator": 0, "mapped": 0})
                d["denominator"] += 1
                if status == "mapped":
                    d["mapped"] += 1
            if status in ("conflict", "unmapped") and len(report.examples[status]) < max_examples:
                report.examples[status].append(
                    {
                        "kind": kind,
                        "code": code,
                        "name": person.name if person else None,
                        "birth_date": person.birth_date.isoformat()
                        if person and person.birth_date
                        else None,
                        "note": m.note if m else None,
                    }
                )
    return report


def apply_manual(
    report: PersonMappingReport, manual: Mapping[str, Mapping[str, str]], *, note: str
) -> dict[str, int]:
    """Operator-approved name-only matches (憲法 I: never automatic). ``manual`` is
    ``{kind: {JRDB code: canonical_id}}``; a code already ``mapped`` by name+birth date is left
    alone, every other code in the report's denominator becomes ``mapped`` with the manual note.
    Codes not in the denominator are ignored (reported). Returns the counts."""

    out = {"applied": 0, "already_mapped": 0, "not_in_denominator": 0}
    for kind in ("jockey", "trainer"):
        kr = report.kind(kind)
        for code, canonical_id in sorted((manual.get(kind) or {}).items()):
            m = report.matches.get((kind, code))
            if m is None:
                out["not_in_denominator"] += 1
                continue
            if m.status == "mapped":
                out["already_mapped"] += 1
                continue
            setattr(kr, m.status, getattr(kr, m.status) - 1)
            kr.mapped += 1
            report.matches[(kind, code)] = Match("mapped", str(canonical_id), f"manual: {note}")
            out["applied"] += 1
    return out


def codes_from_rows(rows: Iterable[tuple[str, str | None, int]]) -> dict[str, dict[str, set[int]]]:
    """``(kind, code, race_year)`` → the ``codes_in_data`` mapping (None codes skipped)."""

    out: dict[str, dict[str, set[int]]] = {"jockey": {}, "trainer": {}}
    for kind, code, year in rows:
        if code is None:
            continue
        out.setdefault(kind, {}).setdefault(code, set()).add(int(year))
    return out


__all__ = [
    "KindReport",
    "apply_manual",
    "Match",
    "PersonMappingReport",
    "build_report",
    "codes_from_rows",
    "decade",
    "match",
    "write_id_mappings",
]


# --------------------------------------------------------------------------- id_mappings (T047)


def write_id_mappings(session, report: PersonMappingReport, *, dry_run: bool) -> dict[str, int]:
    """``id_mappings(source=jrdb)`` from the report: mapped → mapped(canonical),
    conflict → conflict,
    unmapped → unmapped (a row, so the gap is visible). An existing MAPPED row is never touched;
    other existing rows take the new status. ``dry_run`` writes nothing and returns the counts."""

    from horseracing_db.enums import MappingStatus
    from horseracing_db.models import IdMapping
    from sqlalchemy import select

    out = {"mapped": 0, "conflict": 0, "unmapped": 0, "kept_existing_mapped": 0, "written": 0}
    for (kind, code), m in sorted(report.matches.items()):
        out[m.status] += 1
        if dry_run:
            continue
        existing = session.scalar(
            select(IdMapping).where(
                IdMapping.entity_type == kind,
                IdMapping.source == "jrdb",
                IdMapping.source_id == code,
            )
        )
        if existing is not None and existing.mapping_status == MappingStatus.MAPPED:
            out["kept_existing_mapped"] += 1
            continue
        if existing is None:
            existing = IdMapping(entity_type=kind, source="jrdb", source_id=code)
            session.add(existing)
        existing.canonical_id = m.canonical_id
        existing.mapping_status = m.status
        existing.resolution_note = f"178 persons: {m.note}"
        out["written"] += 1
    if not dry_run:
        session.flush()
    return out
