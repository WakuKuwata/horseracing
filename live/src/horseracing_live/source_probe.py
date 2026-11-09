"""段 0 の診断 (Feature 178, US0; tasks T020/T021; FR-001/002/019).

Three read-only probes, none of which writes to the database:

* ``official-odds`` — in each race's 174 capture window, fetch the JRA official 単複 page through
  the 176 walker and record whether its ``official_at`` falls inside the window. The window is
  the capture's own (``near_post.schedule_day``: TARGET_S / WINDOW_S / COLLISION_STEP_S imported,
  not copied — analyze I2), re-derived from the race's CURRENT post time at judgment time (変更後
  基準). ``refresh_race_win_odds`` is NOT used: it commits odds and a job (analyze U2).
* ``dependencies`` — count, per field group, which source currently supplies the day's data
  (行の生成 / マスタ / 現在値 / ID 写像 / 結果 / 確定オッズ / 払戻 / T−3 取得) and how many of the
  day's starters ran the day before (連闘, research D14).
* ``arrivals`` — HEAD the day's own member files every ten minutes and record when
  Last-Modified / Content-Length change (FR-019). Today's files only; the URL is stored as a sha.

Records go to ``artifacts/jrdb/probe/<day>.jsonl`` (append-only); the frozen config to
``<day>-config.json`` (an existing path is refused). 合否には使わない (FR-002).
"""

from __future__ import annotations

import datetime
import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from horseracing_scrape.jrdb import fetch as jrdb_fetch

from . import near_post

JST = datetime.timezone(datetime.timedelta(hours=9))
DEFAULT_PROBE_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "jrdb" / "probe"
MODES = ("official-odds", "dependencies", "arrivals")
ARRIVAL_INTERVAL_S = 600


# --------------------------------------------------------------------------- config


@dataclass(frozen=True)
class RaceWindow:
    race_id: str
    post_time: datetime.datetime
    offset_s: int
    window_open: datetime.datetime
    window_close: datetime.datetime


@dataclass(frozen=True)
class ProbeConfig:
    day: datetime.date
    modes: tuple[str, ...]
    schedule_constants: dict[str, int]
    races: tuple[RaceWindow, ...]
    frozen_at: datetime.datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "modes": list(self.modes),
            "schedule_constants": dict(self.schedule_constants),
            "frozen_at": self.frozen_at.isoformat(),
            "races": [
                {
                    "race_id": r.race_id,
                    "post_time": r.post_time.isoformat(),
                    "offset_s": r.offset_s,
                    "window_open": r.window_open.isoformat(),
                    "window_close": r.window_close.isoformat(),
                }
                for r in self.races
            ],
        }


def schedule_constants() -> dict[str, int]:
    return {
        "target_s": near_post.TARGET_S,
        "window_s": near_post.WINDOW_S,
        "collision_step_s": near_post.COLLISION_STEP_S,
    }


def windows_for(races: Sequence[tuple[str, datetime.datetime]]) -> tuple[RaceWindow, ...]:
    """The 174 schedule, as windows: open/close are the capture's own ±WINDOW_S around the target."""

    out = []
    for s in near_post.schedule_day(races):
        out.append(RaceWindow(s.race_id, s.post_time, s.offset_s, s.window_open, s.window_close))
    return tuple(out)


def build_config(
    session: Session,
    day: datetime.date,
    *,
    modes: Iterable[str] = MODES,
    clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC),
) -> ProbeConfig:
    for m in modes:
        if m not in MODES:
            raise ValueError(f"unknown probe mode {m!r}")
    races = near_post.day_races(session, day)
    return ProbeConfig(day, tuple(modes), schedule_constants(), windows_for(races), clock())


def freeze_config(cfg: ProbeConfig, path: Path) -> Path:
    if path.exists():
        raise FileExistsError(f"probe config already frozen: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cfg.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# --------------------------------------------------------------------------- (a) official odds


def freshness_state(window: RaceWindow, official_at: datetime.datetime | None) -> str:
    """``ok`` when the official page's own timestamp is inside the capture window."""

    if official_at is None:
        return "missing"
    if official_at < window.window_open:
        return "early"
    if official_at > window.window_close:
        return "late"
    return "ok"


def current_window(window: RaceWindow, post_time_now: datetime.datetime) -> RaceWindow:
    """Re-derive the window from the race's CURRENT post time (変更後基準), same offset."""

    if post_time_now == window.post_time:
        return window
    target = post_time_now - datetime.timedelta(seconds=window.offset_s)
    half = datetime.timedelta(seconds=near_post.WINDOW_S)
    return RaceWindow(window.race_id, post_time_now, window.offset_s, target - half, target + half)


def _post_time_now(session: Session, race_id: str) -> datetime.datetime | None:
    return session.execute(
        text("SELECT post_time FROM races WHERE race_id = :rid"), {"rid": race_id}
    ).scalar_one_or_none()


def probe_one_race(
    session: Session,
    window: RaceWindow,
    *,
    fetcher,
    clock: Callable[[], datetime.datetime],
    walker_factory=None,
    parse=None,
    post_time_lookup: Callable[[str], datetime.datetime | None] | None = None,
) -> dict[str, Any]:
    """Fetch the race's 単複 page (walker: index → day → race, ``want`` limits it to this race)
    and judge the official timestamp against the window. Writes nothing to the DB."""

    from horseracing_scrape.jra.navigate import Walker
    from horseracing_scrape.jra.parse.odds import parse_win_odds
    from horseracing_scrape.jra.race_odds import race_want

    walker_factory = walker_factory or (lambda f, d: Walker(f, date=d))
    parse = parse or parse_win_odds
    rid = window.race_id
    lookup = post_time_lookup or (lambda r: _post_time_now(session, r))
    post_now = lookup(rid)
    if post_now is None:
        return {
            "race_id": rid,
            "ok": False,
            "reason": "post_time_unknown",
            "fetched_at": clock().isoformat(),
        }
    w = current_window(window, post_now)
    venue, want = race_want(rid)
    fetched_at = clock()
    official_at = None
    reason = "no_page"
    try:
        for _day, _label, resp in walker_factory(fetcher, window.post_time.date()).odds_pages(
            venue=venue, want=want
        ):
            odds = parse(resp.text, expected_date=window.post_time.date())
            if odds.race_id != rid:
                reason = "race_id_mismatch"
                continue
            official_at = odds.official_at
            reason = freshness_state(w, official_at)
            break
    except Exception as exc:  # noqa: BLE001 — the probe records, it never aborts the day
        reason = f"error:{type(exc).__name__}"
    return {
        "race_id": rid,
        "post_time": w.post_time.isoformat(),
        "post_time_changed": post_now != window.post_time,
        "window_open": w.window_open.isoformat(),
        "window_close": w.window_close.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "official_at": official_at.isoformat() if official_at else None,
        "ok": reason == "ok",
        "reason": reason,
    }


def run_official_odds(
    session: Session,
    cfg: ProbeConfig,
    *,
    fetcher,
    out_path: Path,
    clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC),
    sleep: Callable[[float], None] = time.sleep,
    walker_factory=None,
    parse=None,
    post_time_lookup: Callable[[str], datetime.datetime | None] | None = None,
) -> list[dict[str, Any]]:
    """Walk the day: wait for each window, probe once per race, append the record."""

    out: list[dict[str, Any]] = []
    for w in sorted(cfg.races, key=lambda r: (r.window_open, r.race_id)):
        now = clock()
        if now > w.window_close:
            rec = {
                "race_id": w.race_id,
                "ok": False,
                "reason": "window_late_at_start",
                "fetched_at": now.isoformat(),
            }
        else:
            if now < w.window_open:
                sleep((w.window_open - now).total_seconds())
            rec = probe_one_race(
                session,
                w,
                fetcher=fetcher,
                clock=clock,
                walker_factory=walker_factory,
                parse=parse,
                post_time_lookup=post_time_lookup,
            )
        rec["mode"] = "official-odds"
        append_record(out_path, rec)
        out.append(rec)
    return out


def summarize_official_odds(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    recs = list(records)
    n = len(recs)
    ok = sum(1 for r in recs if r.get("ok"))
    reasons: dict[str, int] = {}
    for r in recs:
        reasons[r.get("reason", "?")] = reasons.get(r.get("reason", "?"), 0) + 1
    return {"races": n, "ok": ok, "rate": (ok / n) if n else None, "reasons": reasons}


# --------------------------------------------------------------------------- (b) dependencies

CATEGORIES = (
    "row_generation",
    "master_generation",
    "current_values",
    "id_mapping",
    "results",
    "final_odds",
    "payouts",
    "near_post",
)

_DEPENDENCY_SQL = {
    # (category, source) rows
    "row_generation": """
        SELECT 'row_generation', coalesce(p.source, 'unknown'), count(*)
        FROM race_horses rh JOIN races r USING (race_id)
        LEFT JOIN race_horse_field_provenance p
          ON p.race_id = rh.race_id AND p.horse_id = rh.horse_id AND p.field_group = 'entry_static'
        WHERE r.race_date = :day GROUP BY 2""",
    "master_generation": """
        SELECT 'master_generation', coalesce(h.data_source, 'unknown'), count(DISTINCT h.horse_id)
        FROM race_horses rh JOIN races r USING (race_id) JOIN horses h USING (horse_id)
        WHERE r.race_date = :day GROUP BY 2""",
    "current_values": """
        SELECT 'current_values', p.field_group || ':' || p.source, count(*)
        FROM race_horse_field_provenance p JOIN races r USING (race_id)
        WHERE r.race_date = :day AND p.field_group IN ('jockey', 'trainer', 'body_weight', 'entry_status')
        GROUP BY 2""",
    "id_mapping": """
        SELECT 'id_mapping', CASE WHEN rh.horse_id LIKE 'nk:%' THEN 'netkeiba_surrogate' ELSE 'canonical' END, count(*)
        FROM race_horses rh JOIN races r USING (race_id) WHERE r.race_date = :day GROUP BY 2""",
    "results": """
        SELECT 'results', coalesce(p.source, 'unknown'), count(*)
        FROM races r LEFT JOIN race_field_provenance p ON p.race_id = r.race_id AND p.field_group = 'results'
        WHERE r.race_date = :day AND EXISTS (SELECT 1 FROM race_results rr WHERE rr.race_id = r.race_id)
        GROUP BY 2""",
    "final_odds": """
        SELECT 'final_odds', coalesce(p.source, 'unknown'), count(*)
        FROM races r LEFT JOIN race_field_provenance p ON p.race_id = r.race_id AND p.field_group = 'win_odds'
        WHERE r.race_date = :day GROUP BY 2""",
    "payouts": """
        SELECT 'payouts', o.source, count(*) FROM official_win_payouts o JOIN races r USING (race_id)
        WHERE r.race_date = :day GROUP BY 2""",
    "near_post": """
        SELECT 'near_post', q.source, count(*) FROM decision_quotes q JOIN races r USING (race_id)
        WHERE r.race_date = :day GROUP BY 2""",
}

_BACK_TO_BACK_SQL = """
    SELECT count(DISTINCT rh.horse_id)
    FROM race_horses rh JOIN races r USING (race_id)
    WHERE r.race_date = :day AND rh.entry_status = 'started'
      AND EXISTS (
        SELECT 1 FROM race_horses rh2 JOIN races r2 USING (race_id)
        WHERE rh2.horse_id = rh.horse_id AND rh2.entry_status = 'started' AND r2.race_date = :prev
      )
"""


def load_dependency_rows(session: Session, day: datetime.date) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for sql in _DEPENDENCY_SQL.values():
        for cat, src, n in session.execute(text(sql), {"day": day}).all():
            rows.append((str(cat), str(src), int(n)))
    return rows


def load_back_to_back(session: Session, day: datetime.date) -> int:
    return int(
        session.execute(
            text(_BACK_TO_BACK_SQL), {"day": day, "prev": day - datetime.timedelta(days=1)}
        ).scalar_one()
    )


def summarize_dependencies(
    rows: Iterable[tuple[str, str, int]], *, back_to_back: int
) -> dict[str, Any]:
    """Pure aggregation: per category, counts by source and the netkeiba share."""

    cats: dict[str, dict[str, int]] = {c: {} for c in CATEGORIES}
    for cat, src, n in rows:
        cats.setdefault(cat, {})[src] = cats.setdefault(cat, {}).get(src, 0) + int(n)
    out: dict[str, Any] = {"categories": {}, "back_to_back_starters": back_to_back}
    for cat in CATEGORIES:
        by = cats.get(cat, {})
        total = sum(by.values())
        nk = sum(v for k, v in by.items() if "netkeiba" in k)
        out["categories"][cat] = {
            "by_source": dict(sorted(by.items())),
            "total": total,
            "netkeiba": nk,
            "netkeiba_share": (nk / total) if total else None,
        }
    return out


def probe_dependencies(session: Session, day: datetime.date) -> dict[str, Any]:
    rows = load_dependency_rows(session, day)
    return {
        "mode": "dependencies",
        "day": day.isoformat(),
        **summarize_dependencies(rows, back_to_back=load_back_to_back(session, day)),
    }


# --------------------------------------------------------------------------- (c) arrivals


def arrival_targets(day: datetime.date) -> dict[str, str]:
    """Today's files only (never a previous year's): label → member URL."""

    return {
        "KYI": jrdb_fetch.url_single("KYI", day),
        "BAC": jrdb_fetch.url_single("BAC", day),
        "UKC": jrdb_fetch.url_single("UKC", day),
        "TYB_tyokuzen": jrdb_fetch.url_tyokuzen(day),
        "TYB": jrdb_fetch.url_single("TYB", day),
        "HJC": jrdb_fetch.url_single("HJC", day),
        "SED_sokuho": jrdb_fetch.url_single("SED", day),
    }


@dataclass
class ArrivalState:
    seen: dict[str, tuple[Any, Any]] = field(default_factory=dict)


def probe_arrivals_once(
    day: datetime.date,
    *,
    head: Callable[[str], Any],
    clock: Callable[[], datetime.datetime],
    state: ArrivalState,
    out_path: Path,
) -> list[dict[str, Any]]:
    """One round of HEADs; a record is appended only when something changed (or first seen)."""

    out = []
    for label, url in arrival_targets(day).items():
        if day.year < clock().astimezone(JST).year:
            raise ValueError("arrival probe only looks at the current year's files")
        now = clock()
        try:
            info = head(url)
            key = (getattr(info, "last_modified", None), getattr(info, "content_length", None))
            status = "present"
        except Exception as exc:  # noqa: BLE001 — 404 before publication is the expected case
            key = (None, None)
            status = f"absent:{type(exc).__name__}"
        if state.seen.get(label) == (status, key):
            continue
        state.seen[label] = (status, key)
        rec = {
            "mode": "arrivals",
            "label": label,
            "source_url_sha256": jrdb_fetch.source_url_sha256(url),
            "observed_at": now.isoformat(),
            "status": status,
            "last_modified": key[0].isoformat() if key[0] else None,
            "content_length": key[1],
        }
        append_record(out_path, rec)
        out.append(rec)
    return out


def run_arrivals(
    day: datetime.date,
    *,
    head: Callable[[str], Any],
    out_path: Path,
    until: datetime.datetime,
    clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC),
    sleep: Callable[[float], None] = time.sleep,
    interval_s: int = ARRIVAL_INTERVAL_S,
) -> list[dict[str, Any]]:
    state = ArrivalState()
    out: list[dict[str, Any]] = []
    while clock() <= until:
        out.extend(probe_arrivals_once(day, head=head, clock=clock, state=state, out_path=out_path))
        sleep(interval_s)
    return out


__all__ = [
    "ARRIVAL_INTERVAL_S",
    "CATEGORIES",
    "DEFAULT_PROBE_DIR",
    "MODES",
    "ArrivalState",
    "ProbeConfig",
    "RaceWindow",
    "append_record",
    "arrival_targets",
    "build_config",
    "current_window",
    "freeze_config",
    "freshness_state",
    "load_back_to_back",
    "load_dependency_rows",
    "probe_arrivals_once",
    "probe_dependencies",
    "probe_one_race",
    "run_arrivals",
    "run_official_odds",
    "schedule_constants",
    "summarize_dependencies",
    "summarize_official_odds",
    "windows_for",
]
