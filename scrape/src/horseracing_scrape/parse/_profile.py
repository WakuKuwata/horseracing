"""ParserProfile: version + invariants for real-netkeiba parsers (Feature 022).

Surfaces a ``parser_version`` (recorded in ingestion_jobs for audit/reproducibility, constitution V)
and the structural invariants each parser must enforce. When markup changes and a required selector
or invariant breaks, parsers fail-close (raise ParseError) rather than inventing data.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParserProfile:
    name: str
    version: str
    required_selectors: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()


ENTRIES_PROFILE = ParserProfile(
    name="entries",
    version="netkeiba-entries-2026-06",
    required_selectors=("table.Shutuba_Table", "tr.HorseList"),
    invariants=(
        "race_id parsed from body == race_id in URL",
        "horse_number unique within race",
        "every started horse has a horse_id and horse_number",
        "entry_status in {started, cancelled}",
    ),
)
