"""Read existing result HTML archives; never fetch pages or import database writers.

All versions remain in ``pages``. The latest version with a result table is selected
before parsing or comparison, including an unreadable latest table. File modification
time is an explicitly labelled ordering proxy only when the archive name has no time.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
for _src in (ROOT / "scrape/src", ROOT / "db/src"):
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

# These modules contain pure parsing functions, not pipeline/upsert/repair imports.
from horseracing_scrape.parse._common import (  # noqa: E402
    id_from_href,
    race_id_from_html,
    soup_of,
)
from horseracing_scrape.parse.exotic_odds import parse_win_payouts  # noqa: E402
from horseracing_scrape.parse.results import (  # noqa: E402
    _market_columns,
    parse_results,
)

_STAMP_RE = re.compile(r"^(\d{8}T\d{12}Z)(?:-(\d+))?\.html\.gz$")
_CHARSET_RE = re.compile(rb'<meta[^>]+charset\s*=\s*["\']?\s*([^\s"\'/>;]+)', re.I)
_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
_FIRST_DATE = dt.date(2007, 1, 1)
_LAST_DATE = dt.date(2026, 10, 26)

PAGE_COLUMNS = [
    "race_id", "archive_path", "archive_url", "archive_gz_sha256", "archive_raw_sha256",
    "html_utf8_sha256", "archive_filename_fetched_at", "archive_file_mtime",
    "fetched_at", "fetched_at_source", "filename_time_parse_error", "archive_suffix", "decode_encoding",
    "decode_replacement_count", "body_race_id", "body_race_id_matches",
    "body_race_id_error", "body_race_id_method", "race_date", "html_date_in_scope", "has_result_table",
    "has_payout_table", "has_win_payout_row", "result_parse_status", "result_parse_error",
    "payout_parse_status", "payout_parse_error", "archive_read_error", "special_refund_text",
    "has_refund_text", "has_special_payout_text", "result_row_count", "started_count",
    "nonstarter_count", "payout_count", "selected", "selection_time_proxy",
]
HORSE_COLUMNS = [
    "race_id", "archive_path", "horse_id", "horse_number", "result_status",
    "finish_order", "is_started", "entry_status", "status_text", "final_odds_text",
    "final_odds_cell_text", "popularity", "row_parse_error",
]
PAYOUT_COLUMNS = ["race_id", "archive_path", "horse_number", "payout_yen"]


def source_files() -> list[Path]:
    """Parser dependencies for the parent's reproducibility protocol hash."""
    return [
        Path(__file__).resolve(),
        ROOT / "scrape/src/horseracing_scrape/parse/_common.py",
        ROOT / "scrape/src/horseracing_scrape/parse/results.py",
        ROOT / "scrape/src/horseracing_scrape/parse/exotic_odds.py",
        ROOT / "scrape/src/horseracing_scrape/models.py",
        ROOT / "db/src/horseracing_db/enums.py",
    ]


def _marker_race(marker: Path) -> tuple[str, str] | None:
    url = marker.read_text(encoding="utf-8").strip()
    parts = urlsplit(url)
    ids = parse_qs(parts.query).get("race_id", [])
    if (parts.hostname != "race.netkeiba.com" or parts.path != "/race/result.html"
            or len(ids) != 1 or re.fullmatch(r"\d{12}", ids[0]) is None):
        return None
    return ids[0], url


def input_paths() -> list[Path]:
    """Enumerate only stored result-page URL markers and their gzip observations."""
    found: list[Path] = []
    for marker in sorted((ROOT / "artifacts/scrape_archive/race.netkeiba.com").glob("*/url.txt")):
        item = _marker_race(marker)
        if item is not None and 2007 <= int(item[0][:4]) <= 2026:
            found.append(marker)
            found.extend(sorted(marker.parent.glob("*.gz")))
    return sorted(found)


def _decode(raw: bytes) -> tuple[str, str]:
    match = _CHARSET_RE.search(raw[:2048])
    encoding = match.group(1).decode("ascii", "ignore").lower() if match else "utf-8"
    try:
        return raw.decode(encoding, errors="replace"), encoding
    except LookupError:
        return raw.decode("utf-8", errors="replace"), "utf-8_fallback_unknown_charset"


def _text(cell) -> str:
    return " ".join(cell.get_text(" ", strip=True).split()) if cell else ""


def _int_or_none(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def _odds_text(text: str) -> str | None:
    value = text.replace(",", "").strip()
    if not _NUMBER_RE.fullmatch(value):
        return None
    try:
        # Preserve the source decimal, never round via a binary float.
        number = Decimal(value)
        return format(number, "f") if number >= 1 else None
    except InvalidOperation:
        return None


def _horse_rows(table, race_id: str, archive_path: str) -> list[dict]:
    """Retain all printed horses, including the non-starters parse_results skips."""
    rows = []
    pop_col, odds_col = _market_columns(table)
    for tr in table.select("tr"):
        link = tr.select_one('a[href*="/horse/"]')
        if link is None:
            continue
        horse_id = id_from_href(link.get("href"), "horse")
        cells = [_text(cell) for cell in tr.find_all("td")]
        status_text = cells[0] if cells else ""
        errors = []
        finish = _int_or_none(status_text)
        if finish is not None:
            status, entry, started = "finished", "started", True
        elif "中" in status_text:
            status, entry, started = "stopped", "started", True
        elif "失" in status_text:
            status, entry, started = "disqualified", "started", True
        elif "除" in status_text:
            status, entry, started = None, "excluded", False
        elif "取" in status_text:
            status, entry, started = None, "cancelled", False
        else:
            status, entry, started = None, None, None
            errors.append("unknown_status")
        if horse_id is None or not horse_id.isdigit():
            errors.append("missing_or_nonnumeric_raw_horse_id")
        number = _int_or_none(cells[2]) if len(cells) > 2 else None
        if number is None or number < 1:
            errors.append("missing_or_invalid_horse_number")
        if len(cells) < 8:
            errors.append("short_result_row")
        odds_cell = cells[odds_col] if odds_col is not None and len(cells) > odds_col else ""
        pop_text = cells[pop_col] if pop_col is not None and len(cells) > pop_col else ""
        popularity = _int_or_none(pop_text)
        if popularity is not None and not 1 <= popularity <= 99:
            popularity = None
        rows.append({
            "race_id": race_id, "archive_path": archive_path, "horse_id": horse_id,
            "horse_number": number, "result_status": status, "finish_order": finish,
            "is_started": started, "entry_status": entry, "status_text": status_text,
            "final_odds_text": _odds_text(odds_cell), "final_odds_cell_text": odds_cell,
            "popularity": popularity, "row_parse_error": ";".join(errors) or None,
        })
    return rows


def _load_page(path: Path, race_id: str, url: str) -> tuple[dict, list[dict], list[dict]]:
    rel = str(path.relative_to(ROOT))
    stamp_match = _STAMP_RE.fullmatch(path.name)
    filename_at, filename_error = None, None
    if stamp_match:
        try:
            filename_at = dt.datetime.strptime(stamp_match.group(1), "%Y%m%dT%H%M%S%fZ").replace(tzinfo=dt.UTC)
        except ValueError as exc:
            filename_error = str(exc)
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC)
    page = dict.fromkeys(PAGE_COLUMNS)
    page.update({
        "race_id": race_id, "archive_path": rel, "archive_url": url,
        "archive_filename_fetched_at": filename_at, "archive_file_mtime": mtime,
        "fetched_at": filename_at or mtime,
        "fetched_at_source": "filename" if filename_at else "mtime_proxy",
        "filename_time_parse_error": filename_error,
        "archive_suffix": int(stamp_match.group(2) or 0) if stamp_match else 0,
        "selected": False, "selection_time_proxy": filename_at is None,
        "has_result_table": False, "has_payout_table": False, "has_win_payout_row": False,
        "result_parse_status": "unreadable_archive", "payout_parse_status": "unreadable_archive",
        "result_row_count": 0, "started_count": 0, "nonstarter_count": 0, "payout_count": 0,
    })
    try:
        compressed = path.read_bytes()
        page["archive_gz_sha256"] = hashlib.sha256(compressed).hexdigest()
        raw = gzip.decompress(compressed)
        page["archive_raw_sha256"] = hashlib.sha256(raw).hexdigest()
        html, encoding = _decode(raw)
        page["decode_encoding"] = encoding
        page["decode_replacement_count"] = html.count("\ufffd")
        page["html_utf8_sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()
    except Exception as exc:
        page["archive_read_error"] = f"{type(exc).__name__}: {exc}"
        return page, [], []
    soup = soup_of(html)
    try:
        page["body_race_id"] = race_id_from_html(html)
        page["body_race_id_matches"] = page["body_race_id"] == race_id
        canonical = re.search(
            r'(?:canonical"\s+href|og:url"\s+content)="[^"]*race_id=(\d{12})', html
        )
        page["body_race_id_method"] = "canonical_or_og" if canonical else "frequency_fallback"
    except Exception as exc:
        page["body_race_id_error"] = f"{type(exc).__name__}: {exc}"
        page["body_race_id_matches"] = False
    date_match = _DATE_RE.search(_text(soup.select_one("title")))
    if date_match:
        try:
            date = dt.date(*(int(part) for part in date_match.groups()))
            page["race_date"] = date.isoformat()
            page["html_date_in_scope"] = _FIRST_DATE <= date <= _LAST_DATE
        except ValueError:
            pass
    table = soup.select_one("table.RaceTable01")
    page["has_result_table"] = table is not None
    page["has_payout_table"] = soup.select_one("table.Payout_Detail_Table") is not None
    page["has_win_payout_row"] = any(
        table.select("tr.Tansho") or any(_text(tr.select_one("th")) == "単勝"
                                         for tr in table.select("tr"))
        for table in soup.select("table.Payout_Detail_Table")
    )
    # A flag is evidence of printed text, not a conversion into a conventional payout.
    texts = [text for text in soup.stripped_strings
             if re.search(r"特別(?:払戻|払い戻し)|特払|返還|返戻|返金", text)]
    page["special_refund_text"] = " | ".join(dict.fromkeys(texts)) or None
    page["has_refund_text"] = any(re.search(r"返還|返戻|返金", text) for text in texts)
    page["has_special_payout_text"] = any(
        re.search(r"特別(?:払戻|払い戻し)|特払", text) for text in texts
    )
    try:
        parsed = parse_results(html)
        page["result_parse_status"] = "ok"
        page["started_count"] = len(parsed.rows)
    except Exception as exc:
        page["result_parse_status"] = "missing_table" if table is None else "parse_error"
        page["result_parse_error"] = f"{type(exc).__name__}: {exc}"
    horses = _horse_rows(table, race_id, rel) if table is not None else []
    page["result_row_count"] = len(horses)
    page["nonstarter_count"] = sum(row["is_started"] is False for row in horses)
    payouts = []
    try:
        parsed_payouts = parse_win_payouts(html)
        page["payout_parse_status"] = "ok" if parsed_payouts else "missing_win_payout"
        payouts = [{"race_id": race_id, "archive_path": rel, "horse_number": row.horse_number,
                    "payout_yen": row.payout_yen} for row in parsed_payouts]
        page["payout_count"] = len(payouts)
    except Exception as exc:
        page["payout_parse_status"] = "parse_error"
        page["payout_parse_error"] = f"{type(exc).__name__}: {exc}"
    return page, horses, payouts


def load_archives() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return all page metadata and horses/payouts of the selected version per race.

    Mismatching body race IDs and parser failures stay visible in metadata; callers
    must exclude them from evidence rather than treating missing values as zero.
    """
    pages, all_horses, all_payouts = [], {}, {}
    for path in input_paths():
        if path.name == "url.txt":
            continue
        item = _marker_race(path.parent / "url.txt")
        if item is None:
            continue
        page, horses, payouts = _load_page(path, *item)
        pages.append(page)
        all_horses[page["archive_path"]] = horses
        all_payouts[page["archive_path"]] = payouts
    selected: dict[str, dict] = {}
    for page in pages:
        if not page["has_result_table"]:
            continue
        previous = selected.get(page["race_id"])
        key = (page["fetched_at"], page["archive_suffix"], page["archive_path"])
        if previous is None or key > (
                previous["fetched_at"], previous["archive_suffix"], previous["archive_path"]):
            selected[page["race_id"]] = page
    horses, payouts = [], []
    for page in selected.values():
        page["selected"] = True
        horses.extend(all_horses[page["archive_path"]])
        payouts.extend(all_payouts[page["archive_path"]])
    return (pd.DataFrame(pages, columns=PAGE_COLUMNS),
            pd.DataFrame(horses, columns=HORSE_COLUMNS),
            pd.DataFrame(payouts, columns=PAYOUT_COLUMNS))
