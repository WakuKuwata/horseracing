"""ParserProfile: version + invariants for real-netkeiba parsers (Feature 022).

Surfaces a ``parser_version`` (recorded in ingestion_jobs for audit/reproducibility, constitution V)
and the structural invariants each parser must enforce. When markup changes and a required selector
or invariant breaks, parsers fail-close (raise ParseError) rather than inventing data.

Also hosts ``parse_horse_profile`` — the leak-safe db.netkeiba.com horse-page parser used by the
opt-in profile-completion pass. It reads identity/pedigree ONLY (name / sex / birth_year /
sire・dam・damsire); career performance stats on the page are never touched (leak boundary).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import ParseError, ScrapedHorseProfile
from ._common import id_from_href, soup_of


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

HORSE_PROFILE_PROFILE = ParserProfile(
    name="horse_profile",
    version="netkeiba-horse-profile-2026-06",
    required_selectors=(".horse_title", "table.db_prof_table"),
    invariants=(
        "horse_name present (fail-close otherwise)",
        "only identity/pedigree stored — never career performance stats (leak boundary)",
        "sex in {牡, 牝, セ} or None",
        "pedigree read from the dedicated /horse/ped/ blood_table (profile page is JS-rendered)",
    ),
)

_SEX_RE = re.compile(r"([牡牝セせ騙])")
_BIRTH_YEAR_RE = re.compile(r"(\d{4})年")


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def _sex_from_title(profile_txt: str) -> str | None:
    # e.g. "現役 牡4歳 鹿毛" / "抹消 牝3歳 栗毛"
    m = _SEX_RE.search(profile_txt)
    if not m:
        return None
    return "セ" if m.group(1) in "セせ騙" else m.group(1)


def _birth_year(soup) -> int | None:
    """生年月日 row in the profile table -> birth year (e.g. '2022年1月28日' -> 2022)."""
    for row in soup.select("table.db_prof_table tr"):
        th = _text(row.find("th"))
        if "生年月日" in th:
            m = _BIRTH_YEAR_RE.search(_text(row.find("td")))
            return int(m.group(1)) if m else None
    return None


def parse_horse_profile(html: str, netkeiba_horse_id: str) -> ScrapedHorseProfile:
    """Parse the profile page for **identity only** (name / sex / birth_year).

    Pedigree is NOT here — the profile page's pedigree box is JS-rendered. Use
    ``parse_horse_pedigree`` on ``horse_pedigree_url`` and merge. Career stats are never read."""
    soup = soup_of(html)
    title = soup.select_one(".horse_title")
    name = _text(title.find("h1")) if title is not None else ""
    if not name:
        raise ParseError("missing required element: .horse_title h1 (horse name)")

    profile_txt = _text(title.select_one(".txt_01")) if title else ""
    return ScrapedHorseProfile(
        netkeiba_horse_id=netkeiba_horse_id,
        horse_name=name,
        sex=_sex_from_title(profile_txt),
        birth_year=_birth_year(soup),
        netkeiba_sire_id=None, sire_name=None,
        netkeiba_dam_id=None, dam_name=None,
        netkeiba_damsire_id=None, damsire_name=None,
    )


def _ped_pair(cell) -> tuple[str | None, str | None]:
    """(netkeiba_id, name) from a blood_table cell's first horse link (the plain /horse/{id}/;
    the [血統]/[産駒] links follow it). None if no horse link (text-only foreign ancestor)."""
    if cell is None:
        return None, None
    link = cell.find("a", href=re.compile(r"/horse/\d"))
    if link is None:
        return None, None
    return id_from_href(link.get("href"), "horse"), (_text(link) or None)


def parse_horse_pedigree(
    html: str, netkeiba_horse_id: str
) -> tuple[tuple[str | None, str | None], ...]:
    """Parse the /horse/ped/ blood table -> (sire, dam, damsire) as (id, name) pairs.

    Real markup (verified live 2026-06-28): ``table.blood_table`` with sire line cells classed
    ``b_ml`` and dam line cells ``b_fml``. The two top-level cells (largest rowspan) are
    sire (b_ml) and dam (b_fml); the damsire (母父) is the first b_ml cell AFTER the dam cell in
    document order. Missing pieces stay None (Unknown) — never guessed."""
    soup = soup_of(html)
    table = soup.select_one("table.blood_table")
    if table is None:
        raise ParseError("missing required element: table.blood_table")

    cells = table.find_all("td")
    none_pair: tuple[str | None, str | None] = (None, None)
    if not cells:
        return none_pair, none_pair, none_pair

    def _rowspan(td) -> int:
        try:
            return int(td.get("rowspan") or 1)
        except ValueError:
            return 1

    top = max(_rowspan(c) for c in cells)
    sire_cell = next((c for c in cells if _rowspan(c) == top and "b_ml" in (c.get("class") or [])),
                     None)
    dam_idx = next((i for i, c in enumerate(cells)
                    if _rowspan(c) == top and "b_fml" in (c.get("class") or [])), None)
    dam_cell = cells[dam_idx] if dam_idx is not None else None
    # damsire = dam's sire: first b_ml cell after the dam cell
    damsire_cell = None
    if dam_idx is not None:
        damsire_cell = next((c for c in cells[dam_idx + 1:]
                             if "b_ml" in (c.get("class") or [])), None)
    return _ped_pair(sire_cell), _ped_pair(dam_cell), _ped_pair(damsire_cell)
