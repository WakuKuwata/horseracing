"""Direct local URL/card inventory only. No database, network, cache API or model imports."""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[3]
ARCHIVE = ROOT / "artifacts/scrape_archive"
OUT = Path(__file__).parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def original_stamp(path):
    match = re.fullmatch(r"(\d{8}T\d{6}\d{6}Z)\.html\.gz", path.name)
    if not match:
        return None
    return dt.datetime.strptime(match[1], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=dt.UTC)


def main():
    now = dt.datetime.now(dt.UTC)
    today = now.astimezone(dt.timezone(dt.timedelta(hours=9))).date()
    markers, cards, lists, unrecognized = [], [], [], []
    for marker in sorted(ARCHIVE.rglob("url.txt")):
        data = marker.read_bytes()
        url = data.decode("utf-8").strip()
        parts = urlsplit(url)
        record = dict(path=str(marker.relative_to(ROOT)), sha256=sha(data), url=url)
        markers.append(record)
        if parts.hostname != "race.netkeiba.com":
            continue
        if parts.path == "/race/shutuba.html":
            ids = parse_qs(parts.query).get("race_id", [])
            if len(ids) != 1 or re.fullmatch(r"\d{12}", ids[0]) is None:
                unrecognized.append(record)
                continue
            for archive in sorted(marker.parent.glob("*.gz")):
                stamp = original_stamp(archive)
                row = dict(
                    record,
                    race_id=ids[0],
                    archive_path=str(archive.relative_to(ROOT)),
                    original_captured_at=stamp.isoformat() if stamp else None,
                    timestamp_source="archive_filename" if stamp else "missing",
                )
                cards.append(row)
        elif parts.path == "/top/race_list.html":
            lists.append(
                dict(
                    record,
                    archives=[
                        str(p.relative_to(ROOT))
                        for p in sorted(marker.parent.glob("*.gz"))
                    ],
                )
            )
    selected = {}
    for row in cards:
        rid = row["race_id"]
        if row["original_captured_at"] is None:
            unrecognized.append(row)
            continue
        order = (row["original_captured_at"], row["archive_path"])
        if rid not in selected or order > (
            selected[rid]["original_captured_at"],
            selected[rid]["archive_path"],
        ):
            selected[rid] = row
    ledger = []
    for rid, row in sorted(selected.items()):
        compressed = (ROOT / row["archive_path"]).read_bytes()
        raw = gzip.decompress(compressed)
        charset = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.I)
        encoding = charset[1].decode("ascii") if charset else "utf-8"
        try:
            text = raw.decode(encoding, errors="replace")
        except LookupError:
            encoding = "utf-8_fallback_unknown_charset"
            text = raw.decode("utf-8", errors="replace")
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
        header = title[1] if title else text[:8192]
        found = re.findall(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", header)
        dates = sorted(
            {dt.date(*(int(x) for x in match)).isoformat() for match in found}
        )
        date = dates[0] if len(dates) == 1 else None
        ledger.append(
            dict(
                row,
                archive_gz_sha256=sha(compressed),
                raw_bytes_sha256=sha(raw),
                decode_encoding=encoding,
                own_race_date=date,
                date_source="page_title" if title else "header_fallback",
                own_race_date_ambiguous=(len(dates) != 1),
                at_or_after_today=bool(date and date >= today.isoformat()),
                original_capture_in_future=row["original_captured_at"]
                > now.isoformat(),
            )
        )
    upcoming = [r for r in ledger if r["at_or_after_today"]]
    missing = [r for r in ledger if r["own_race_date_ambiguous"]]
    path = OUT / "local-card-ledger.json"
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    index = (
        ROOT
        / "specs/160-saved-timepoint-expansion/evidence/saved_shutuba_inventory.parquet"
    )
    summary = dict(
        scope="existing local URL markers and latest saved shutuba page per race_id",
        inspected_at=now.isoformat(),
        today_jst=today.isoformat(),
        new_collection=False,
        database_access=False,
        model_inference=False,
        file_mtime_used=False,
        cache_api_called=False,
        url_marker_count=len(markers),
        shutuba_archived_versions=len(cards),
        latest_shutuba_races_inspected=len(ledger),
        independent_race_list_url_markers=len(lists),
        independent_race_list_sources=lists,
        unknown_source_rows=len(unrecognized),
        unparseable_own_date_count=len(missing),
        unparseable_own_date_rows=missing,
        page_year_counts=dict(
            Counter(r["own_race_date"][:4] for r in ledger if r["own_race_date"])
        ),
        latest_own_race_date=max(
            (r["own_race_date"] for r in ledger if r["own_race_date"]), default=None
        ),
        latest_original_capture_at=max(
            (r["original_captured_at"] for r in cards if r["original_captured_at"]),
            default=None,
        ),
        stored_card_races_at_or_after_today=len(upcoming),
        stored_card_rows_at_or_after_today=upcoming,
        navigation_links_used_as_cards=False,
        limitation="Coverage is this archive tree/latest saved source page per race, not all computer files or the real-world meeting calendar. Navigation links to future dates are not saved future race cards.",
        url_markers_sha256=sha(
            json.dumps(
                markers, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ),
        legacy160_inventory_ref=str(index.relative_to(ROOT)),
        legacy160_inventory_sha256=sha(index.read_bytes()),
        local_ledger_ref=str(path.relative_to(ROOT)),
        local_ledger_sha256=sha(path.read_bytes()),
        script_sha256=sha(Path(__file__).read_bytes()),
    )
    (OUT / "local-inventory.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k
                not in [
                    "stored_card_rows_at_or_after_today",
                    "unparseable_own_date_rows",
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
