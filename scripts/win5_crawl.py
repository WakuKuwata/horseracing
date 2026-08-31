"""WIN5 全履歴の取得(netkeiba・礼儀優先・再開可能)。

会計的 kill-test(扉A)のためのデータ収集。各回の 発売票数/金額・払戻金・的中票数・
的中馬番・対象 5 レース(会場/R)・明示キャリーオーバー(あれば)を取る。

礼儀: 5 秒 + ジッタ間隔・ブラウザ様 UA・ディスクキャッシュ(再実行は未取得分のみ)・
連続失敗 5 回で停止(ブロックは HTTP 400 でも来る)。総量 ~820 ページ = ブロック閾値
(1 日 1 万)の 8%。

    python3 scripts/win5_crawl.py --out <dir>
"""

from __future__ import annotations

import argparse
import pathlib
import random
import re
import sys
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
BASE = "https://race.netkeiba.com/top"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../out/win5_cache")
    ap.add_argument("--from-year", type=int, default=2011)
    ap.add_argument("--to-year", type=int, default=2026)
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    dates: list[str] = []
    for y in range(args.from_year, args.to_year + 1):
        p = out / f"index_{y}.html"
        if not p.exists():
            time.sleep(5 + random.random() * 2)
            p.write_bytes(fetch(f"{BASE}/win5_results.html?year={y}"))
        ds = sorted(set(re.findall(rb'win5\.html\?date=(\d{8})', p.read_bytes())))
        dates += [d.decode() for d in ds]
        print(f"{y}: {len(ds)} events (累計 {len(dates)})", flush=True)

    dates = sorted(set(dates))
    print(f"total events: {len(dates)}", flush=True)
    fails = 0
    for i, d in enumerate(dates):
        p = out / f"event_{d}.html"
        if p.exists() and p.stat().st_size > 5000:
            continue
        time.sleep(5 + random.random() * 2)
        try:
            b = fetch(f"{BASE}/win5.html?date={d}")
        except Exception as e:
            fails += 1
            print(f"FAIL {d}: {e} ({fails}連続)", flush=True)
            if fails >= 5:
                sys.exit("連続失敗 5 回 — ブロックの可能性。停止(再実行で再開可能)")
            continue
        if len(b) < 5000:
            fails += 1
            print(f"SHORT {d}: {len(b)}B ({fails}連続)", flush=True)
            if fails >= 5:
                sys.exit("短応答連続 — ブロックの可能性。停止")
            continue
        fails = 0
        p.write_bytes(b)
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(dates)} 取得済", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
