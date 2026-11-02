"""Judge every old/new difference of a shuffled replay against what really happened.

    uv run --project scrape python scripts/netkeiba_replay/judge_shuffle.py EVENTS STATE_OLD STATE_NEW

Truth per race, from the events themselves: the NEWEST 出馬表 (entry status, jockey, number of
starters) and the odds payload with the NEWEST official time. Each differing cell is classified:
``new_is_truth`` (the new code holds the newest information, the old one a stale value),
``old_is_truth`` (a regression) or ``neither``. Exit 1 if anything is not ``new_is_truth``.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from compare import compare  # noqa: E402
from run_replay import load_events  # noqa: E402

from horseracing_scrape.parse.entries import parse_entries  # noqa: E402
from horseracing_scrape.upsert import _derive_running_style  # noqa: E402

PRICE = {
    "odds": ("1", 0),
    "popularity": ("1", 2),
    "place_odds_low": ("2", 0),
    "place_odds_high": ("2", 1),
    "place_popularity": ("2", 2),
}


def _settled(e) -> bool:
    from horseracing_scrape.models import NotYetPublished, ParseError
    from horseracing_scrape.parse.results import parse_results

    try:
        parse_results(e["body"])
        return True
    except (NotYetPublished, ParseError):
        return False


def _truth(events, delivered):
    """``delivered`` is the order the replay used. A 複勝 quote is a PRE-RACE fact (FR-013): its
    truth is the newest payload delivered before the race's first settled result page; a race whose
    result arrived before any payload has no pre-race quote at all."""
    newest_e, newest_o = {}, {}
    for e in events:
        if e["kind"] == "entries":
            if e["race_id"] not in newest_e or e["t"] > newest_e[e["race_id"]]["t"]:
                newest_e[e["race_id"]] = e
        elif e["kind"] == "odds":
            d = json.loads(e["body"])["data"]
            if (
                e["race_id"] not in newest_o
                or d["official_datetime"] > newest_o[e["race_id"]]["official_datetime"]
            ):
                newest_o[e["race_id"]] = d
    pre_place: dict[str, dict | None] = {}
    settled_seen: set[str] = set()
    for e in delivered:
        rid = e["race_id"]
        if e["kind"] == "results" and rid not in settled_seen and _settled(e):
            settled_seen.add(rid)
        elif e["kind"] == "odds" and rid not in settled_seen:
            d = json.loads(e["body"])["data"]
            cur = pre_place.get(rid)
            if cur is None or d["official_datetime"] > cur["official_datetime"]:
                pre_place[rid] = d
    horses, started = {}, {}
    for rid, e in newest_e.items():
        try:
            hs = parse_entries(e["body"]).horses
        except Exception:  # noqa: BLE001 — a pre-draw page: no truth from it
            continue
        started[rid] = sum(1 for h in hs if h.entry_status == "started")
        for h in hs:
            horses[(rid, f"nk:{h.netkeiba_horse_id}")] = h
    return horses, started, newest_o, pre_place


def _num(v):
    return None if v is None else float(v)


def main() -> int:
    events = load_events(sys.argv[1])
    old = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    new = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    rep = compare(old, new, max_examples=10**9)
    from run_replay import order_events

    delivered = order_events(events, "shuffle", 1)
    horses, started, newest_o, pre_place = _truth(events, delivered)
    numbers = {
        (r["race_id"], r["horse_id"]): r["horse_number"]
        for r in new["race_horses"]["rows"]
    }
    corners = {
        (r["race_id"], r["horse_id"]): r["corner_orders"]
        for r in new["race_results"]["rows"]
    }
    verdict = collections.Counter()
    bad = []
    for table, v in rep["tables"].items():
        if v["only_old"] or v["only_new"]:
            bad.append(
                (
                    table,
                    "row set differs",
                    v["only_old_examples"],
                    v["only_new_examples"],
                )
            )
        for col, d in v["columns"].items():
            for ex in d["examples"]:
                key = tuple(json.loads(k) for k in ex["key"])
                if table == "races":
                    rid = key[0]
                    if col.startswith("place_odds"):
                        pp = pre_place.get(rid)
                        if col.endswith("observed_at"):
                            ok = (pp is None) == (ex["new"] is None)
                        elif pp is None:
                            ok = ex["new"] is None
                        else:  # stored UTC vs payload JST wall clock: compare as instants
                            import datetime as _dt

                            jst = _dt.timezone(_dt.timedelta(hours=9))
                            want = _dt.datetime.fromisoformat(
                                pp["official_datetime"]
                            ).replace(tzinfo=jst)
                            ok = (
                                ex["new"] is not None
                                and _dt.datetime.fromisoformat(ex["new"]) == want
                            )
                    else:
                        ok = (
                            col.endswith("observed_at")
                            or (ex["new"] or "")[:16] >= (ex["old"] or "")[:16]
                        )
                    kind = "new_is_truth" if ok else "neither"
                elif table != "race_horses":
                    kind = "neither"
                else:
                    h = horses.get(key)
                    if col == "entry_status":
                        kind = (
                            "new_is_truth"
                            if h and ex["new"] == h.entry_status
                            else "old_is_truth"
                            if h and ex["old"] == h.entry_status
                            else "neither"
                        )
                    elif col == "jockey_id":
                        truth = (
                            f"nk:{h.netkeiba_jockey_id}"
                            if h and h.netkeiba_jockey_id
                            else None
                        )
                        kind = (
                            "new_is_truth"
                            if ex["new"] == truth
                            else "old_is_truth"
                            if ex["old"] == truth
                            else "neither"
                        )
                    elif col == "running_style":
                        c = corners.get(key)
                        exp = _derive_running_style(
                            tuple(c) if c else None, started.get(key[0], 0)
                        )
                        kind = (
                            "new_is_truth"
                            if ex["new"] == exp
                            else ("old_is_truth" if ex["old"] == exp else "neither")
                        )
                    elif col in PRICE:
                        if h is not None and h.entry_status != "started":
                            kind = "new_is_truth" if ex["new"] is None else "neither"
                        else:
                            group, idx = PRICE[col]
                            src = (
                                pre_place.get(key[0])
                                if col.startswith("place")
                                else newest_o.get(key[0])
                            )
                            cell = (
                                (src or {})
                                .get("odds", {})
                                .get(group, {})
                                .get(f"{numbers.get(key, 0):02d}")
                            )
                            tv = (
                                None
                                if cell is None or cell[idx] in ("---.-", "")
                                else float(cell[idx])
                            )
                            kind = (
                                "new_is_truth"
                                if _num(ex["new"]) == tv
                                else "old_is_truth"
                                if _num(ex["old"]) == tv
                                else "neither"
                            )
                    else:
                        kind = "neither"
                verdict[(table, col, kind)] += 1
                if kind != "new_is_truth":
                    bad.append((table, col, kind, ex))
    for k, n in sorted(verdict.items()):
        print(f"{n:5d}  {k[0]}.{k[1]}: {k[2]}")
    print("jobs", rep["ingestion_jobs"])
    for b in bad[:30]:
        print("NOT-TRUTH", json.dumps(b, ensure_ascii=False)[:400])
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
