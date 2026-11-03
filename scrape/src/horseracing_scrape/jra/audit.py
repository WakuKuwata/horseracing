"""監査 = 収集(`jra-fetch`)と照合(`jra-audit`)(Feature 176, FR-023・tasks T083/T084).

収集は公式ページを取って raw と manifest を保存するだけで、業務表・job には書かない(共有スロットの
予約行だけは動く)。単複オッズは kind 許可制で raw を保存しない(憲法 V・契約 §1)。

照合は保存した raw を入力に、DB を ``SET TRANSACTION READ ONLY`` で開き、「同じレース・
同じ公開段階・解決済み ID」の行同士だけを比べる。比較できない行は理由ごとに分母の外で数える。
保存値の由来は来歴があれば添え、無ければ ``None``(netkeiba と決めつけない)。
来歴が無い行も比較はする(導入前の稼働 DB は全行が来歴なし=比べないと監査にならない)が、
``stored_source_unknown`` で件数を分けて出す。
"""

from __future__ import annotations

import datetime
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from horseracing_db.enums import BetType, EntryStatus
from horseracing_db.models import (
    ExoticOdds,
    IdMapping,
    OfficialWinPayout,
    RaceFieldProvenance,
    RaceHorse,
    RaceHorseFieldProvenance,
    RaceResult,
)
from sqlalchemy import select, text

from .. import precedence as P
from ..models import ParseError
from ..upsert import parse_netkeiba_time
from .fetch import _decode
from .models import (
    KIND_ACCESS_J,
    KIND_BABA_FRAGMENT,
    KIND_CHANGES,
    KIND_ODDS_WIN,
    JraEntry,
    JraPayoutSet,
    JraRaceResult,
    archivable,
)
from .navigate import (
    ACCESS_J_CNAME,
    ACCESS_J_URL,
    BABA_CUSHION_URL,
    BABA_MOIST_URL,
    CHANGES_CNAME,
    CHANGES_URL,
    Walker,
)
from .parse.entries import parse_entries_day
from .parse.odds import parse_win_odds
from .parse.payouts import BRACKET_QUINELLA, parse_payouts_day
from .parse.results import parse_results_day

MANIFEST = "manifest.json"
FETCH_KINDS = ("entries", "changes", "odds", "results", "payouts", "baba")
AUDITED_KINDS = ("entries", "results", "payouts")
MAX_EXAMPLES = 20


# --- collection ----------------------------------------------------------------------------------
class SaveDirNotEmpty(Exception):
    pass


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run_fetch(args, *, fetcher, date: datetime.date):
    """Fetch the requested kinds and keep raw + manifest. No job, no business table."""
    from .cli import RunResult

    result = RunResult("jra-fetch", date)
    save_dir = Path(args.save_dir)
    if (save_dir / MANIFEST).exists():
        result.notes.append(f"save_dir already holds a manifest: {save_dir}")
        result.exit_code = 2
        return result
    kinds = [k.strip() for k in (args.kinds or "").split(",") if k.strip()]
    unknown = [k for k in kinds if k not in FETCH_KINDS]
    if unknown:
        result.notes.append(f"unknown kinds: {unknown}")
        result.exit_code = 2
        return result
    save_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict] = []

    def save(resp, page_kind: str, venue: str | None) -> None:
        if not archivable(resp.kind):
            raise AssertionError(f"kind {resp.kind} is not archivable")  # odds never reach here
        name = f"{page_kind}_{venue or 'all'}_{len(pages):02d}.html"
        (save_dir / name).write_bytes(resp.raw)
        pages.append(
            {
                "file": name,
                "kind": page_kind,
                "response_kind": resp.kind,
                "venue": venue,
                "raw_sha256": _sha(resp.raw),
                "cname_sha256": resp.cname_sha256,
                "fetched_at": resp.fetched_at.isoformat(),
                "url": resp.url,
            }
        )

    odds_parsed = 0
    for kind in kinds:
        if kind in ("entries", "results", "payouts"):
            for day, resp in Walker(fetcher, date=date).day_pages(kind, venue=args.venue):
                save(resp, kind, day.venue_code)
        elif kind == "changes":
            save(fetcher.post(CHANGES_URL, CHANGES_CNAME, kind=KIND_CHANGES), "changes", None)
        elif kind == "baba":
            save(fetcher.get(BABA_CUSHION_URL, kind=KIND_BABA_FRAGMENT), "baba_cushion", None)
            save(fetcher.get(BABA_MOIST_URL, kind=KIND_BABA_FRAGMENT), "baba_moist", None)
            save(fetcher.post(ACCESS_J_URL, ACCESS_J_CNAME, kind=KIND_ACCESS_J), "accessJ", None)
        elif kind == "odds":
            for day, label, resp in Walker(fetcher, date=date).odds_pages(venue=args.venue):
                assert resp.kind == KIND_ODDS_WIN
                try:
                    parse_win_odds(resp.text, expected_date=date)
                    odds_parsed += 1
                except ParseError as exc:
                    result.notes.append(f"odds {day.venue_code} {label}: {exc}")
    manifest = {"date": date.isoformat(), "pages": pages, "odds_parsed_not_saved": odds_parsed}
    (save_dir / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    result.requests_sent = fetcher.requests_sent
    result.notes.append(f"saved {len(pages)} page(s) to {save_dir}")
    if odds_parsed:
        result.notes.append(f"odds pages parsed {odds_parsed} (raw not saved)")
    return result


# --- saved pages ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class SavedPage:
    kind: str
    venue: str | None
    text: str
    raw_sha256: str
    fetched_at: datetime.datetime


class ManifestError(Exception):
    pass


def load_saved_pages(directory: Path | str) -> tuple[datetime.date, list[SavedPage]]:
    directory = Path(directory)
    try:
        doc = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"manifest unreadable in {directory}: {exc}") from exc
    out = []
    for p in doc.get("pages", []):
        raw = (directory / p["file"]).read_bytes()
        if _sha(raw) != p["raw_sha256"]:
            raise ManifestError(f"{p['file']}: sha256 differs from the manifest")
        out.append(
            SavedPage(
                p["kind"],
                p.get("venue"),
                _decode(raw, None),
                p["raw_sha256"],
                datetime.datetime.fromisoformat(p["fetched_at"]),
            )
        )
    return datetime.date.fromisoformat(doc["date"]), out


# --- stored state (plain values; the comparison functions stay pure) -----------------------------
@dataclass(frozen=True)
class StoredHorse:
    horse_id: str
    horse_number: int | None
    frame: int | None
    jockey_id: str | None
    trainer_id: str | None
    jockey_weight: float | None
    entry_status: str
    weight: int | None
    weight_diff: int | None
    popularity: int | None


@dataclass
class StoredRace:
    race_id: str
    horses: dict[str, StoredHorse] = field(default_factory=dict)
    results: dict[str, tuple] = field(
        default_factory=dict
    )  # horse_id → (order, status, td, last_3f)
    win_payouts: dict[int, int] = field(default_factory=dict)
    exotic: dict[str, dict[tuple[int, ...], int]] = field(default_factory=dict)  # yen per 100
    horse_prov: dict[tuple[str, str], str] = field(default_factory=dict)
    race_prov: dict[str, str] = field(default_factory=dict)
    #: netkeiba source id → canonical id for APPROVED (mapped) horse mappings
    mapped: dict[str, str] = field(default_factory=dict)

    def by_number(self) -> dict[int, StoredHorse]:
        return {h.horse_number: h for h in self.horses.values() if h.horse_number is not None}


def load_stored(session, race_ids: Iterable[str]) -> dict[str, StoredRace]:
    ids = sorted(set(race_ids))
    out: dict[str, StoredRace] = {}
    if not ids:
        return out
    for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id.in_(ids))):
        sr = out.setdefault(rh.race_id, StoredRace(rh.race_id))
        sr.horses[rh.horse_id] = StoredHorse(
            rh.horse_id,
            rh.horse_number,
            rh.frame,
            rh.jockey_id,
            rh.trainer_id,
            float(rh.jockey_weight) if rh.jockey_weight is not None else None,
            rh.entry_status,
            rh.weight,
            rh.weight_diff,
            rh.popularity,
        )
    for rr in session.scalars(select(RaceResult).where(RaceResult.race_id.in_(ids))):
        out.setdefault(rr.race_id, StoredRace(rr.race_id)).results[rr.horse_id] = (
            rr.finish_order,
            rr.result_status,
            rr.finish_time,
            float(rr.last_3f) if rr.last_3f is not None else None,
        )
    for wp in session.scalars(select(OfficialWinPayout).where(OfficialWinPayout.race_id.in_(ids))):
        out.setdefault(wp.race_id, StoredRace(wp.race_id)).win_payouts[wp.horse_number] = (
            wp.payout_yen
        )
    for ex in session.scalars(select(ExoticOdds).where(ExoticOdds.race_id.in_(ids))):
        sr = out.setdefault(ex.race_id, StoredRace(ex.race_id))
        sr.exotic.setdefault(ex.bet_type, {})[tuple(ex.selection)] = int(
            (Decimal(ex.odds) * 100).to_integral_value()
        )
    for p in session.scalars(
        select(RaceHorseFieldProvenance).where(RaceHorseFieldProvenance.race_id.in_(ids))
    ):
        out.setdefault(p.race_id, StoredRace(p.race_id)).horse_prov[(p.horse_id, p.field_group)] = (
            p.source
        )
    for p in session.scalars(
        select(RaceFieldProvenance).where(RaceFieldProvenance.race_id.in_(ids))
    ):
        out.setdefault(p.race_id, StoredRace(p.race_id)).race_prov[p.field_group] = p.source
    nk_ids = {
        h.horse_id[3:]
        for sr in out.values()
        for h in sr.horses.values()
        if h.horse_id.startswith("nk:")
    }
    if nk_ids:
        mapped = {
            m.source_id: m.canonical_id
            for m in session.scalars(
                select(IdMapping).where(
                    IdMapping.entity_type == "horse",
                    IdMapping.source_id.in_(sorted(nk_ids)),
                    IdMapping.mapping_status == "mapped",
                )
            )
            if m.canonical_id
        }
        for sr in out.values():
            sr.mapped = mapped
    return out


# --- report --------------------------------------------------------------------------------------
@dataclass
class FieldStats:
    comparable: int = 0
    matched: int = 0
    mismatched: int = 0
    stored_source_unknown: int = 0
    examples: list[dict] = field(default_factory=list)
    excluded_by_reason: dict[str, int] = field(default_factory=dict)

    def compare(self, race_id, horse_number, stored, official, stored_source) -> None:
        self.comparable += 1
        if stored_source is None:
            self.stored_source_unknown += 1
        if stored == official:
            self.matched += 1
            return
        self.mismatched += 1
        if len(self.examples) < MAX_EXAMPLES:
            self.examples.append(
                {
                    "race_id": race_id,
                    "horse_number": horse_number,
                    "stored": _jsonable(stored),
                    "official": _jsonable(official),
                    "stored_source": stored_source,
                }
            )

    def exclude(self, reason: str, n: int = 1) -> None:
        self.excluded_by_reason[reason] = self.excluded_by_reason.get(reason, 0) + n

    def to_json(self) -> dict:
        return {
            "comparable": self.comparable,
            "matched": self.matched,
            "mismatched": self.mismatched,
            "match_rate": (self.matched / self.comparable) if self.comparable else None,
            "stored_source_unknown": self.stored_source_unknown,
            "excluded_by_reason": dict(sorted(self.excluded_by_reason.items())),
            "examples": self.examples,
        }


def _jsonable(v):
    if isinstance(v, (datetime.timedelta,)):
        return v.total_seconds()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    return v


@dataclass
class AuditReport:
    fields: dict[str, FieldStats] = field(default_factory=dict)
    merge_candidates: set[tuple[str, str]] = field(default_factory=set)
    #: (nk:X, X) pairs compared through the stored surrogate row (FR-011a) — still re-key material
    alias_matched: set[tuple[str, str]] = field(default_factory=set)
    approved_mapping_pending_rekey: set[tuple[str, str]] = field(default_factory=set)
    races_seen: set[str] = field(default_factory=set)
    pages: int = 0

    def f(self, name: str) -> FieldStats:
        return self.fields.setdefault(name, FieldStats())

    def to_json(self) -> dict:
        return {
            "pages": self.pages,
            "races": len(self.races_seen),
            "fields": {k: v.to_json() for k, v in sorted(self.fields.items())},
            "merge_candidates": sorted(map(list, self.merge_candidates)),
            "alias_matched": sorted(map(list, self.alias_matched)),
            "approved_mapping_pending_rekey": sorted(
                map(list, self.approved_mapping_pending_rekey)
            ),
        }


ENTRY_FIELDS = {  # field → provenance group
    "frame": P.GROUP_ENTRY_STATIC,
    "jockey_weight": P.GROUP_ENTRY_STATIC,
    "jockey_id": P.GROUP_JOCKEY,
    "trainer_id": P.GROUP_TRAINER,
    "entry_status": P.GROUP_ENTRY_STATUS,
}
RESULT_FIELDS = ("finish_order", "result_status", "finish_time", "last_3f")


def _unalias(v):
    """``nk:J`` and ``J`` are the same licence number (FR-011a): compare the digits."""
    return v[3:] if isinstance(v, str) and v.startswith("nk:") else v


def _identity(
    report: AuditReport,
    stored_race: StoredRace,
    st: StoredHorse,
    official_id: str,
    official_name: str | None,
    fields: Iterable[str],
) -> bool:
    """True when the stored row IS the official horse; records why not otherwise."""
    if st.horse_id == official_id:
        return True
    if st.horse_id.startswith("nk:"):
        nk = st.horse_id[3:]
        if stored_race.mapped.get(nk) == official_id:
            report.approved_mapping_pending_rekey.add((st.horse_id, official_id))
        elif nk == official_id:
            # FR-011a: the same row the writer would use — compare through it
            report.merge_candidates.add((st.horse_id, official_id))
            report.alias_matched.add((st.horse_id, official_id))
            return True
        for f in fields:
            report.f(f).exclude("unresolved_id")
        return False
    report.f("horse_id").compare(
        stored_race.race_id, st.horse_number, st.horse_id, official_id, None
    )
    for f in fields:
        report.f(f).exclude("identity_differs")
    return False


def compare_entries(entry: JraEntry, stored: StoredRace | None, report: AuditReport) -> None:
    rid = entry.race.race_id
    report.races_seen.add(rid)
    fields = (*ENTRY_FIELDS, "body_weight")
    for h in entry.horses:
        if stored is None or not stored.horses:
            for f in fields:
                report.f(f).exclude("race_not_in_db")
            continue
        if h.horse_id is None:
            for f in fields:
                report.f(f).exclude("unresolved_official_id")
            continue
        st = stored.by_number().get(h.horse_number) if h.horse_number is not None else None
        if st is None:
            st = stored.horses.get(h.horse_id)
        if st is None:
            for f in fields:
                report.f(f).exclude("not_in_db")
            continue
        if not _identity(report, stored, st, h.horse_id, h.horse_name, fields):
            continue
        report.f("horse_id").compare(
            rid,
            h.horse_number,
            _unalias(st.horse_id),
            h.horse_id,
            stored.horse_prov.get((st.horse_id, P.GROUP_ENTRY_STATIC)),
        )
        official = {
            "frame": h.frame,
            "jockey_weight": h.jockey_weight,
            "jockey_id": h.jockey_id,
            "trainer_id": h.trainer_id,
            "entry_status": h.entry_status,
        }
        for f, group in ENTRY_FIELDS.items():
            if official[f] is None:
                report.f(f).exclude("not_published")
                continue
            stored_v = getattr(st, f)
            report.f(f).compare(
                rid,
                h.horse_number,
                _unalias(stored_v) if f in ("jockey_id", "trainer_id") else stored_v,
                official[f],
                stored.horse_prov.get((st.horse_id, group)),
            )
        if h.weight is None and h.weight_diff is None:
            report.f("body_weight").exclude("not_published")
        else:
            report.f("body_weight").compare(
                rid,
                h.horse_number,
                (st.weight, st.weight_diff),
                (h.weight, h.weight_diff),
                stored.horse_prov.get((st.horse_id, P.GROUP_BODY_WEIGHT)),
            )


def compare_results(res: JraRaceResult, stored: StoredRace | None, report: AuditReport) -> None:
    rid = res.race.race_id
    report.races_seen.add(rid)
    fields = (*RESULT_FIELDS, "body_weight", "popularity")
    for r in res.rows:
        if stored is None or not stored.horses:
            for f in fields:
                report.f(f).exclude("race_not_in_db")
            continue
        if not stored.results:
            for f in fields:
                report.f(f).exclude("stage_differs")  # the DB is still at the pre-result stage
            continue
        if r.horse_id is None:
            for f in fields:
                report.f(f).exclude("unresolved_official_id")
            continue
        st = stored.by_number().get(r.horse_number) if r.horse_number is not None else None
        st = st or stored.horses.get(r.horse_id)
        if st is None:
            for f in fields:
                report.f(f).exclude("not_in_db")
            continue
        if not _identity(report, stored, st, r.horse_id, r.horse_name, fields):
            continue
        result_src = stored.race_prov.get("results")
        if r.result_status in (EntryStatus.CANCELLED, EntryStatus.EXCLUDED):
            report.f("entry_status").compare(
                rid,
                r.horse_number,
                st.entry_status,
                r.result_status,
                stored.horse_prov.get((st.horse_id, P.GROUP_ENTRY_STATUS)),
            )
            continue
        row = stored.results.get(st.horse_id)
        if row is None:
            for f in RESULT_FIELDS:
                report.f(f).exclude("not_in_db")
        else:
            official = {
                "finish_order": r.finish_order,
                "result_status": r.result_status,
                "finish_time": parse_netkeiba_time(r.finish_time) if r.finish_time else None,
                "last_3f": r.last_3f,
            }
            for i, f in enumerate(RESULT_FIELDS):
                if official[f] is None and f in ("finish_time", "last_3f"):
                    report.f(f).exclude("not_published")
                    continue
                report.f(f).compare(rid, r.horse_number, row[i], official[f], result_src)
        if r.weight is None and r.weight_diff is None:
            report.f("body_weight").exclude("not_published")
        else:
            report.f("body_weight").compare(
                rid,
                r.horse_number,
                (st.weight, st.weight_diff),
                (r.weight, r.weight_diff),
                stored.horse_prov.get((st.horse_id, P.GROUP_BODY_WEIGHT)),
            )
        # popularity is never stored from this page: the mismatch is the audit finding itself
        if r.popularity is None or st.popularity is None:
            report.f("popularity").exclude("not_published")
        else:
            report.f("popularity").compare(
                rid, r.horse_number, st.popularity, r.popularity, stored.race_prov.get("win_odds")
            )


def compare_payouts(
    race_id: str, payouts: JraPayoutSet, stored: StoredRace | None, report: AuditReport
) -> None:
    report.races_seen.add(race_id)
    for bet_type, block in sorted(payouts.by_bet_type.items()):
        name = f"payout_{bet_type}"
        if bet_type == BRACKET_QUINELLA:
            report.f(name).exclude("not_mapped_bet_type")
            continue
        if block.state != "complete":
            report.f(name).exclude(f"block_{block.state}")
            continue
        official = {ln.numbers: ln.payout_yen for ln in block.lines}
        if bet_type == BetType.WIN:
            stored_set: Mapping = {
                (n,): y for n, y in (stored.win_payouts if stored else {}).items()
            }
            src = stored.race_prov.get("win_payouts") if stored else None
        else:
            stored_set = stored.exotic.get(bet_type, {}) if stored else {}
            src = stored.race_prov.get("exotic_payouts") if stored else None
        if not stored_set:
            report.f(name).exclude("not_in_db")
            continue
        report.f(name).compare(
            race_id, None, dict(sorted(stored_set.items())), dict(sorted(official.items())), src
        )


# --- the audit command ---------------------------------------------------------------------------
def build_report(pages: list[SavedPage], stored_loader, date: datetime.date) -> AuditReport:
    report = AuditReport()
    parsed = []
    for p in pages:
        if p.kind not in AUDITED_KINDS:
            continue
        report.pages += 1
        if p.kind == "entries":
            parsed.append(("entries", parse_entries_day(p.text, expected_date=date)))
        elif p.kind == "results":
            parsed.append(("results", parse_results_day(p.text, expected_date=date)))
        else:
            parsed.append(("payouts", parse_payouts_day(p.text, expected_date=date)))
    race_ids = {b.race_id for _, page in parsed for b in page.published()}
    stored = stored_loader(race_ids)
    for kind, page in parsed:
        for b in page.published():
            s = stored.get(b.race_id)
            if kind == "entries":
                compare_entries(b.payload, s, report)
            elif kind == "results":
                compare_results(b.payload, s, report)
                compare_payouts(b.race_id, b.payload.payouts, s, report)
            else:
                compare_payouts(b.race_id, b.payload, s, report)
    return report


def run_audit(args, *, session_factory, date: datetime.date):
    from .cli import RunResult

    result = RunResult("jra-audit", date)
    try:
        saved_date, pages = load_saved_pages(args.from_dir)
    except ManifestError as exc:
        result.notes.append(str(exc))
        result.exit_code = 2
        return result
    if saved_date != date:
        result.notes.append(f"saved pages are for {saved_date}, not {date}")
        result.exit_code = 2
        return result
    with session_factory() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            report = build_report(pages, lambda ids: load_stored(session, ids), date)
        finally:
            session.rollback()
    result.audit = report.to_json()
    for name, fs in sorted(report.fields.items()):
        result.mismatches.extend({"field": name, **ex} for ex in fs.examples)
    return result


__all__ = [
    "AuditReport",
    "FieldStats",
    "SavedPage",
    "StoredHorse",
    "StoredRace",
    "build_report",
    "compare_entries",
    "compare_payouts",
    "compare_results",
    "load_saved_pages",
    "load_stored",
    "run_audit",
    "run_fetch",
]
