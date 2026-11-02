"""Build a replay event list from the netkeiba scrape ARCHIVE (176 pre-merge parity check).

Reads only archived gzip copies (``artifacts/scrape_archive/**/race.netkeiba.com/<key>/``):
every 出馬表 (shutuba) and 結果 (result) page with the UTC time it was fetched. Win/place odds are
not archived (constitution V), so one odds payload is SYNTHESISED per archived 出馬表 copy, at that
copy's time: the race's final win odds (read from its result page) perturbed deterministically,
in netkeiba's JSON shape, for the horses that copy lists as started.

Output: JSON lines ``{"t": iso_utc, "kind": "entries|odds|results", "race_id", "url", "body"}``
sorted by time. Nothing here touches a database or the network.
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import math
import re
from pathlib import Path

from horseracing_scrape.final_odds_repair import _decode, archived_at
from horseracing_scrape.models import ParseError
from horseracing_scrape.parse.entries import parse_entries
from horseracing_scrape.parse.results import parse_results
from horseracing_scrape.urls import win_odds_url

JST = datetime.timezone(datetime.timedelta(hours=9))
_KIND = {
    "entries": re.compile(r"/race/shutuba\.html\?(?:.*&)?race_id=(\d{12})"),
    "results": re.compile(r"/race/result\.html\?(?:.*&)?race_id=(\d{12})"),
}


def _scan(root: Path, prefix: str | None):
    for marker in root.rglob("race.netkeiba.com/*/url.txt"):
        url = marker.read_text(encoding="utf-8").splitlines()[0].strip()
        for kind, rx in _KIND.items():
            m = rx.search(url)
            if not m or (prefix and not m.group(1).startswith(prefix)):
                continue
            for path in marker.parent.glob("*.html.gz"):
                t = archived_at(path)
                if t is not None:
                    yield t, kind, m.group(1), url, path


def _wobble(race_id: str, number: int, t: datetime.datetime) -> float:
    h = int(hashlib.sha256(f"{race_id}:{number}:{t.isoformat()}".encode()).hexdigest()[:8], 16)
    return 1.0 + 0.25 * math.sin(h)


def _odds_payload(
    race_id: str,
    t: datetime.datetime,
    started: list[int],
    cancelled: list[int],
    final: dict[int, float],
) -> str:
    win = {}
    for n in started:
        base = final.get(n) or 10.0
        win[n] = max(1.0, round(base * _wobble(race_id, n, t), 1))
    ranks = {
        n: i + 1 for i, (n, _) in enumerate(sorted(win.items(), key=lambda kv: (kv[1], kv[0])))
    }
    g1 = {f"{n:02d}": [f"{o:.1f}", "0.0", str(ranks[n])] for n, o in win.items()}
    g2 = {}
    for n, o in win.items():
        lo = max(1.0, round(o / 4, 1))
        g2[f"{n:02d}"] = [f"{lo:.1f}", f"{round(lo * 1.6, 1):.1f}", str(ranks[n])]
    for n in cancelled:
        g1[f"{n:02d}"] = ["---.-", "0.0", "0"]
    official = (t.astimezone(JST) - datetime.timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    return json.dumps(
        {"status": "middle", "data": {"official_datetime": official, "odds": {"1": g1, "2": g2}}}
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True, action="append")
    ap.add_argument("--race-id-prefix", default=None, help="e.g. 2026 or 202605 to limit races")
    ap.add_argument("--since", default=None, help="UTC date YYYY-MM-DD: drop copies fetched before")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    since = (
        datetime.datetime.fromisoformat(a.since).replace(tzinfo=datetime.UTC) if a.since else None
    )
    raw = []
    for root in a.archive:
        raw.extend(_scan(Path(root), a.race_id_prefix))
    raw = [r for r in raw if since is None or r[0] >= since]
    raw.sort(key=lambda r: (r[0], r[1], r[2]))
    # final win odds per race (netkeiba horse id -> odds) from its newest settled result page
    final_by_id: dict[str, dict[str, float | None]] = {}
    for _t, kind, rid, _url, path in sorted(raw, key=lambda r: r[0], reverse=True):
        if kind != "results" or rid in final_by_id:
            continue
        try:
            res = parse_results(_decode(gzip.decompress(path.read_bytes())))
        except ParseError:
            continue
        final_by_id[rid] = {r.netkeiba_horse_id: r.win_odds for r in res.rows}
    events = []
    for t, kind, rid, url, path in raw:
        html = _decode(gzip.decompress(path.read_bytes()))
        events.append({"t": t.isoformat(), "kind": kind, "race_id": rid, "url": url, "body": html})
        if kind != "entries":
            continue
        try:
            entry = parse_entries(html)
        except ParseError:
            continue  # e.g. before the draw: the pipeline fails closed on the same page
        by_id = final_by_id.get(rid, {})
        started = [
            h.horse_number
            for h in entry.horses
            if h.horse_number is not None and h.entry_status == "started"
        ]
        cancelled = [
            h.horse_number
            for h in entry.horses
            if h.horse_number is not None and h.entry_status != "started"
        ]
        fin = {
            h.horse_number: by_id.get(h.netkeiba_horse_id)
            for h in entry.horses
            if h.horse_number is not None
        }
        if not started:
            continue
        t_odds = t + datetime.timedelta(seconds=1)
        events.append(
            {
                "t": t_odds.isoformat(),
                "kind": "odds",
                "race_id": rid,
                "url": win_odds_url(rid),
                "body": _odds_payload(rid, t_odds, started, cancelled, fin),
            }
        )
    events.sort(key=lambda e: (e["t"], e["kind"], e["race_id"]))
    with gzip.open(a.out, "wt", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    kinds = {}
    for e in events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(
        json.dumps(
            {"events": len(events), "races": len({e["race_id"] for e in events}), "by_kind": kinds}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
