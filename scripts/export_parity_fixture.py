"""Export a netkeiba parity fixture from the scrape ARCHIVE (Feature 176 T066).

Reads only the gzip copies the netkeiba fetcher archived (``artifacts/scrape_archive/<day>/
race.netkeiba.com/<key>/{url.txt,*.html.gz}``) and parses them with the EXISTING netkeiba parsers.
It never opens a database: after results land the DB has overwritten the pre-race state, so the
archive is the only faithful record of what netkeiba said before the race (codex Q1-7).

ID state: the archive has no id_mappings snapshot. netkeiba ids are written as-is and the parity
tests treat "netkeiba id == official id" as resolved (the 62% same-numbering finding of 067);
``id_resolution`` records that convention.

    uv run --project scrape python scripts/export_parity_fixture.py \
        --day 2026-11-01 --venue 05 --stage pre_result \
        --source-archive /abs/artifacts/scrape_archive \
        --out scrape/tests/fixtures/jra/parity/netkeiba_20261101_tokyo_pre_result.json
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

from horseracing_scrape.final_odds_repair import _decode, archived_at
from horseracing_scrape.models import ParseError
from horseracing_scrape.parse.entries import parse_entries
from horseracing_scrape.parse.exotic_odds import parse_win_payouts
from horseracing_scrape.parse.results import parse_results

_URL_RE = {
    "pre_result": re.compile(r"/race/shutuba\.html\?(?:.*&)?race_id=(\d{12})(?:&|$)"),
    "post_result": re.compile(r"/race/result\.html\?(?:.*&)?race_id=(\d{12})(?:&|$)"),
}


def index_archive(roots: list[Path], stage: str, prefix: str) -> dict[str, list[Path]]:
    """race_id → archived copies (newest first) of the stage's page, across every day folder."""
    found: dict[str, list[tuple[datetime.datetime, Path]]] = {}
    for root in roots:
        for marker in root.rglob("race.netkeiba.com/*/url.txt"):
            m = _URL_RE[stage].search(marker.read_text(encoding="utf-8").strip())
            if m is None or not m.group(1).startswith(prefix):
                continue
            for path in marker.parent.glob("*.html.gz"):
                stamp = archived_at(path)
                if stamp is not None:
                    found.setdefault(m.group(1), []).append((stamp, path))
    return {rid: [p for _, p in sorted(v, reverse=True)] for rid, v in sorted(found.items())}


def _pre(rid: str, paths: list[Path], day: datetime.date) -> dict | None:
    """The newest copy fetched BEFORE the post time (the pre-result state); if every copy is
    later, the oldest one, flagged ``post_race_copy``."""
    parsed = []
    for path in paths:  # newest first
        html = _decode(gzip.decompress(path.read_bytes()))
        try:
            e = parse_entries(html)
        except ParseError:
            continue
        if e.race.race_date != day:
            return None
        parsed.append((archived_at(path), html, e))
    if not parsed:
        return None
    post = parsed[0][2].race.post_time
    before = [x for x in parsed if post is None or x[0] < post]
    at, html, e = before[0] if before else parsed[-1]
    return {
        "race_id": rid,
        "archived_at": at.isoformat(),
        "post_race_copy": not before,
        "page_sha256": hashlib.sha256(html.encode()).hexdigest(),
        "race": {
            "race_date": e.race.race_date.isoformat(),
            "distance": e.race.distance,
            "track_type": e.race.track_type,
            "race_class": e.race.race_class,
            "post_time": e.race.post_time.isoformat() if e.race.post_time else None,
        },
        "horses": [
            {
                "netkeiba_horse_id": h.netkeiba_horse_id,
                "horse_name": h.horse_name,
                "frame": h.frame,
                "horse_number": h.horse_number,
                "netkeiba_jockey_id": h.netkeiba_jockey_id,
                "jockey_name": h.jockey_name,
                "netkeiba_trainer_id": h.netkeiba_trainer_id,
                "trainer_name": h.trainer_name,
                "jockey_weight": h.jockey_weight,
                "weight": h.weight,
                "weight_diff": h.weight_diff,
                "sex": h.sex,
                "age": h.age,
                "entry_status": h.entry_status,
            }
            for h in e.horses
        ],
    }


def _post(rid: str, paths: list[Path]) -> dict | None:
    for path in paths:
        html = _decode(gzip.decompress(path.read_bytes()))
        try:
            r = parse_results(html)
        except ParseError:
            continue  # a pre-race copy of the result URL
        return {
            "race_id": rid,
            "archived_at": archived_at(path).isoformat(),
            "page_sha256": hashlib.sha256(html.encode()).hexdigest(),
            "rows": [
                {
                    "netkeiba_horse_id": x.netkeiba_horse_id,
                    "finish_order": x.finish_order,
                    "result_status": x.result_status,
                    "finish_time": x.finish_time,
                    "last_3f": x.last_3f,
                    "corner_orders": list(x.corner_orders) if x.corner_orders else None,
                    "win_odds": x.win_odds,
                    "popularity": x.popularity,
                }
                for x in r.rows
            ],
            "win_payouts": [
                {"horse_number": p.horse_number, "payout_yen": p.payout_yen}
                for p in parse_win_payouts(html)
            ],
        }
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--day", required=True)
    ap.add_argument("--venue", required=True, help="2-digit venue code")
    ap.add_argument("--stage", required=True, choices=sorted(_URL_RE))
    ap.add_argument(
        "--source-archive", action="append", required=True, help="archive root (repeatable)"
    )
    ap.add_argument(
        "--race-id-prefix",
        default=None,
        help="YYYYVVKKDD — defaults to every race of the venue whose page says --day",
    )
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    day = datetime.date.fromisoformat(a.day)
    if a.stage == "post_result" and a.race_id_prefix is None:
        print(
            "post_result needs --race-id-prefix (the result parser does not carry the date)",
            file=sys.stderr,
        )
        return 2
    prefix = a.race_id_prefix or f"{day.year}{a.venue}"
    roots = [Path(p) for p in a.source_archive]
    index = index_archive(roots, a.stage, prefix)
    races = []
    for rid, paths in index.items():
        rec = _pre(rid, paths, day) if a.stage == "pre_result" else _post(rid, paths)
        if rec is not None:
            races.append(rec)
    out = {
        "stage": a.stage,
        "day": day.isoformat(),
        "venue": a.venue,
        "source": "netkeiba archive (no DB read)",
        "id_resolution": "netkeiba_id_as_is",
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "races": races,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"races": len(races), "out": a.out}))
    return 0 if races else 1


if __name__ == "__main__":
    raise SystemExit(main())
