"""騎手/調教師ページ (JRA 公式) の収集 (Feature 178, T032; research D3; FR-003a).

The official person pages are the only source whose URL IS the canonical id
(``pw04kmk0<5 桁>`` jockeys / ``pw05cmk0<5 桁>`` trainers, 176 survey). Their 生年月日 (plus 名前
as the matching key, 所属 as a note) is what KZA/CZA rows are matched against — never a name alone
(憲法 I).

One-time collection: for every id in the DB's ``jockeys``/``trainers`` (about 1,500) the page is
fetched once through the 176 ``JraFetcher`` (shared slot, 10 s interval) and appended to
``artifacts/jrdb/persons/jra-profiles.jsonl``; ids already in the file are not fetched again.

Navigation: the person pages are reached from a listing page the operator names (its URL and
cname, taken from the public site navigation at rollout R3 — a run never stores a dynamic cname).
Links on that page are filtered with ``identity.official_id``; the per-page POST is the same
``fetcher.post(url, cname, kind=KIND_PERSON)`` the 176 walker uses. ``KIND_PERSON`` is not an
archivable kind, so no raw page is kept.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from .identity import JOCKEY, TRAINER, official_id
from .navigate import links
from .parse.person import parse_person_profile

KIND_PERSON = "person"
DEFAULT_PROFILES_PATH = (
    Path(__file__).resolve().parents[4] / "artifacts" / "jrdb" / "persons" / "jra-profiles.jsonl"
)


@dataclass(frozen=True)
class JraPersonProfile:
    kind: str  # "jockey" | "trainer"
    canonical_id: str  # JRA 5 桁 (= the page's own id)
    name: str | None
    birth_date: datetime.date | None
    affiliation: str | None  # 美浦 / 栗東 / 地方 / 海外 …
    fetched_at: datetime.datetime | None = None

    def to_json(self) -> dict:
        d = asdict(self)
        d["birth_date"] = self.birth_date.isoformat() if self.birth_date else None
        d["fetched_at"] = self.fetched_at.isoformat() if self.fetched_at else None
        return d

    @classmethod
    def from_json(cls, d: dict) -> JraPersonProfile:
        return cls(
            kind=d["kind"],
            canonical_id=d["canonical_id"],
            name=d.get("name"),
            birth_date=datetime.date.fromisoformat(d["birth_date"])
            if d.get("birth_date")
            else None,
            affiliation=d.get("affiliation"),
            fetched_at=(
                datetime.datetime.fromisoformat(d["fetched_at"]) if d.get("fetched_at") else None
            ),
        )


def load_profiles(path: Path | str) -> dict[tuple[str, str], JraPersonProfile]:
    p = Path(path)
    out: dict[tuple[str, str], JraPersonProfile] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        prof = JraPersonProfile.from_json(json.loads(line))
        out[(prof.kind, prof.canonical_id)] = prof
    return out


def append_profile(path: Path | str, profile: JraPersonProfile) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(profile.to_json(), ensure_ascii=False) + "\n")


@dataclass
class CollectReport:
    wanted: int = 0
    already: int = 0
    fetched: int = 0
    not_linked: int = 0
    parse_failed: int = 0
    stopped: str | None = None


def collect_profiles(
    fetcher,
    *,
    kind: str,
    index: tuple[str, str],
    wanted_ids: Iterable[str],
    out_path: Path | str = DEFAULT_PROFILES_PATH,
    clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC),
    max_pages: int | None = None,
) -> CollectReport:
    """Fetch the profile page of every wanted id not already collected. Stops (and reports) on
    the first refusal — the 176 contract: a refusal ends today's run."""

    if kind not in (JOCKEY, TRAINER):
        raise ValueError("kind must be jockey or trainer")
    out = Path(out_path)
    have = load_profiles(out)
    wanted = sorted(set(wanted_ids))
    report = CollectReport(wanted=len(wanted))
    todo = [i for i in wanted if (kind, i) not in have]
    report.already = len(wanted) - len(todo)
    if not todo:
        return report
    index_url, index_cname = index
    page = fetcher.post(index_url, index_cname, kind=KIND_PERSON)
    by_id: dict[str, tuple[str, str]] = {}
    for url, cname, _label in links(page.text):
        pid = official_id(kind, cname)
        if pid is not None and pid not in by_id:
            by_id[pid] = (url, cname)
    n = 0
    for pid in todo:
        ref = by_id.get(pid)
        if ref is None:
            report.not_linked += 1
            continue
        if max_pages is not None and n >= max_pages:
            report.stopped = "max_pages"
            break
        try:
            resp = fetcher.post(ref[0], ref[1], kind=KIND_PERSON)
        except Exception as exc:  # noqa: BLE001 — refusal/parameter error: stop the run
            report.stopped = type(exc).__name__
            break
        n += 1
        try:
            prof = parse_person_profile(resp.text, kind=kind, canonical_id=pid)
        except Exception:  # noqa: BLE001
            report.parse_failed += 1
            continue
        append_profile(out, JraPersonProfile(**{**asdict(prof), "fetched_at": clock()}))
        report.fetched += 1
    return report


__all__ = [
    "DEFAULT_PROFILES_PATH",
    "KIND_PERSON",
    "CollectReport",
    "JraPersonProfile",
    "append_profile",
    "collect_profiles",
    "load_profiles",
]
