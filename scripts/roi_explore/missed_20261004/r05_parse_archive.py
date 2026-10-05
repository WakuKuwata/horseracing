"""R05_settlement step 0: parse archived netkeiba result pages (no network, no DB).

Reads every artifacts/scrape_archive/race.netkeiba.com/*/url.txt whose URL is a result.html page,
parses EVERY archived .gz version (tansho/wakuren payout table + per-horse 単勝オッズ column) and
writes two JSONL files under artifacts/roi_explore/missed_20261004/R05_settlement/:

  archive_payouts.jsonl  one row per (race_id, archive version): tansho [(horse_number, yen)],
                         wakuren [(frames, yen)], parse status
  archive_horses.jsonl   one row per (race_id, horse_number) from the LATEST version:
                         finish text, popularity, result-page odds (float or None)

Run:  cd scrape && uv run python ../scripts/roi_explore/missed_20261004/r05_parse_archive.py
"""
from __future__ import annotations

import gzip
import hashlib
import json
import pathlib
import re

from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parents[3]
ARCH = ROOT / "artifacts/scrape_archive/race.netkeiba.com"
OUT = ROOT / "artifacts/roi_explore/missed_20261004/R05_settlement"


def _yen(text: str) -> list[int]:
    return [int(x.replace(",", "")) for x in re.findall(r"([\d,]+)円", text)]


def parse_payout_row(tr) -> tuple[list[list[int]], list[int]] | None:
    """Return (selections, payouts). Result cell: <ul><li><span>n</span> per selection or spans."""
    if tr is None:
        return None
    res = tr.select_one("td.Result")
    pay = tr.select_one("td.Payout")
    if res is None or pay is None:
        return None
    payouts = _yen(pay.get_text(" ", strip=True))
    uls = res.select("ul")
    sels: list[list[int]] = []
    if uls:
        for ul in uls:
            nums = [int(s.get_text(strip=True)) for s in ul.select("span") if s.get_text(strip=True).isdigit()]
            if nums:
                sels.append(nums)
    else:
        nums = [int(s.get_text(strip=True)) for s in res.select("span") if s.get_text(strip=True).isdigit()]
        sels = [[n] for n in nums]
    return sels, payouts


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pay_rows, horse_rows = [], []
    n_dirs = 0
    for u in sorted(ARCH.glob("*/url.txt")):
        url = u.read_text().strip()
        if "/race/result.html" not in url:
            continue
        m = re.search(r"race_id=(\d{12})", url)
        if not m:
            continue
        rid = m.group(1)
        n_dirs += 1
        files = sorted(u.parent.glob("*.gz"))
        for k, f in enumerate(files):
            raw = f.read_bytes()
            body = gzip.decompress(raw).decode("utf8", errors="replace")
            s = BeautifulSoup(body, "lxml")
            tan = parse_payout_row(s.select_one("table.Payout_Detail_Table tr.Tansho"))
            waku = parse_payout_row(s.select_one("table.Payout_Detail_Table tr.Wakuren"))
            status = "ok"
            tansho = []
            if tan is None:
                status = "no_tansho_row"
            else:
                sels, pays = tan
                flat = [x[0] for x in sels if len(x) == 1]
                if len(flat) != len(pays) or not flat:
                    status = "tansho_shape"
                else:
                    tansho = [[a, b] for a, b in zip(flat, pays, strict=True)]
            wakuren = []
            if waku is not None:
                sels, pays = waku
                if len(sels) == len(pays):
                    wakuren = [[x, p] for x, p in zip(sels, pays, strict=True)]
            pay_rows.append({
                "race_id": rid, "version": k, "n_versions": len(files),
                "is_latest": k == len(files) - 1,
                "archive_path": str(f.relative_to(ROOT)),
                "archive_sha256": hashlib.sha256(raw).hexdigest(),
                "fetched_at_utc": f.name.split(".")[0],
                "tansho": tansho, "wakuren": wakuren, "status": status,
            })
            if k == len(files) - 1:
                table = s.select_one("table.RaceTable01")
                if table is None:
                    continue
                for tr in table.select("tr"):
                    cells = tr.find_all("td")
                    if len(cells) < 11:
                        continue
                    num = cells[2].get_text(strip=True)
                    if not num.isdigit():
                        continue
                    price = cells[10].get_text(strip=True)
                    try:
                        price_f = float(price)
                    except ValueError:
                        price_f = None
                    pop = cells[9].get_text(strip=True)
                    horse_rows.append({
                        "race_id": rid, "horse_number": int(num),
                        "finish_text": cells[0].get_text(strip=True),
                        "popularity_page": int(pop) if pop.isdigit() else None,
                        "result_page_odds": price_f, "odds_text": price,
                    })
    (OUT / "archive_payouts.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in pay_rows))
    (OUT / "archive_horses.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in horse_rows))
    print(json.dumps({"result_dirs": n_dirs, "payout_versions": len(pay_rows),
                      "status": {s: sum(r["status"] == s for r in pay_rows) for s in {r["status"] for r in pay_rows}},
                      "horse_rows_latest": len(horse_rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
