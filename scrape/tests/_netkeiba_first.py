"""176 FR-011a test helper: the netkeiba route's 出馬表 written BEFORE the official route.

The official route never creates race_horses / master rows (FR-011a §1), so every test of the
official writers first lays down the field the way production does: through the common writer with
``source=netkeiba``. ``alias=True`` reproduces the 2026 state (new horses / people as ``nk:X``
surrogates, no canonical master); ``alias=False`` writes the canonical ids (horses JRA-VAN knew).
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Sequence

from horseracing_db.enums import Source

from horseracing_scrape.jra.models import JraChange, JraDayPage, JraEntry
from horseracing_scrape.jra.upsert_jra import race_values_of, resolve_cancelled_numbers
from horseracing_scrape.models import ResolvedEntryHorse
from horseracing_scrape.upsert import Counts, write_entries

NK = Source.NETKEIBA


def nk(x: str | None) -> str | None:
    return None if x is None else f"nk:{x}"


def netkeiba_rows(
    entry: JraEntry,
    changes: Sequence[JraChange] = (),
    *,
    alias: bool = True,
    canonical: Iterable[str] = (),
    blank_numbers: Iterable[str] = (),
) -> list[ResolvedEntryHorse]:
    """The field as the netkeiba route would write it. Ids in ``canonical`` stay canonical even
    with ``alias=True``; horses in ``blank_numbers`` are written without a 馬番."""
    keep = set(canonical)
    blank = set(blank_numbers)
    numbers = resolve_cancelled_numbers(entry, changes, {}) if changes else {}

    def rid(x):
        return x if (not alias or x in keep) else nk(x)

    year = entry.race.race_date.year
    out = []
    for h in entry.horses:
        number = h.horse_number if h.horse_number is not None else numbers.get(h.horse_id)
        out.append(
            ResolvedEntryHorse(
                horse_id=rid(h.horse_id),
                horse_name=h.horse_name,
                frame=h.frame,
                horse_number=None if h.horse_id in blank else number,
                jockey_id=rid(h.jockey_id),
                jockey_name=h.jockey_name,
                trainer_id=rid(h.trainer_id),
                trainer_name=h.trainer_name,
                weight=None,
                weight_diff=None,
                jockey_weight=h.jockey_weight,
                sex=h.sex,
                age=h.age,
                entry_status=h.entry_status,
                birth_year=year - h.age if h.age else None,
                data_source=NK,
            )
        )
    return out


def netkeiba_first(
    session,
    page: JraDayPage[JraEntry],
    changes: Sequence[JraChange] = (),
    *,
    observed_at: datetime.datetime,
    alias: bool = True,
    canonical: Iterable[str] = (),
    race_ids: Sequence[str] | None = None,
    blank_numbers: Iterable[str] = (),
) -> Counts:
    """Write every published block of ``page`` through the netkeiba route, then commit."""
    total = Counts()
    canonical = tuple(canonical)
    blank_numbers = tuple(blank_numbers)
    for b in page.published():
        if race_ids is not None and b.race_id not in race_ids:
            continue
        e: JraEntry = b.payload
        values = race_values_of(e.race) | {"post_time": None}
        total.merge(
            write_entries(
                session,
                e.race.race_id,
                values,
                netkeiba_rows(
                    e,
                    changes,
                    alias=alias,
                    canonical=canonical,
                    blank_numbers=blank_numbers,
                ),
                source=NK,
                observed_at=observed_at,
                job_id=None,
                page_kind="entries",
            )
        )
    session.commit()
    return total
