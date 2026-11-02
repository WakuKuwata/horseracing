"""176 pre-merge guard: the netkeiba route still ingests what the pre-176 code ingested.

Real netkeiba pages of four Tokyo races (archived by the live fetcher, 2026-10-03〜11-02, two
with a 出走取消 announced after the first 出馬表; scripts/styles stripped) are fed through the
real pipeline entry points the ops refresh uses, with the clock frozen at each page's fetch time.
The goldens are the OLD code's final state for the same events (``scripts/netkeiba_replay/
make_golden.py`` on ``git archive 8e373520``).

* ``chrono`` — the pages in the order they were fetched;
* ``dup`` — every fetch twice plus one more full refresh an hour after the result;
  both must match the old code on every column the old schema has.
* ``shuffle`` — each race's pages in a random order (stale pages after newer ones). Every
  difference must be the new code being closer to what happened (the 176 fix: a scratched horse
  stays scratched — the old code revived it from a stale page — and what follows from that).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from horseracing_scrape import pipeline
from horseracing_scrape import upsert as up
from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.parse.entries import parse_entries

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[3]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "netkeiba_replay"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / "netkeiba_replay" / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rr = _load("run_replay")
cmp = _load("compare")


def _replay(engine, order: str) -> dict:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    events = rr.order_events(rr.load_events(FIX / "events.jsonl.gz"), order, 1)
    clock = rr.freeze_clock(pipeline, up)
    try:
        crashes = rr.replay(
            factory, events, pipeline=pipeline, fetcher_cls=FixtureFetcher, clock=clock
        )
    finally:
        clock["restore"]()
    with factory() as s:
        state = rr.dump(s)
    state["_meta"] = {"crashes": crashes, "order": order}
    return state


def _golden(order: str) -> dict:
    return json.loads((FIX / f"golden-old-{order}.json").read_text(encoding="utf-8"))


def _no_differences(rep: dict) -> list[str]:
    bad = [
        f"{t}: {v}"
        for t, v in rep["tables"].items()
        if v["only_old"] or v["only_new"] or v["rows_differing"]
    ]
    if rep["ingestion_jobs"]:
        bad.append(f"jobs: {rep['ingestion_jobs']}")
    return bad


@pytest.mark.parametrize("order", ["chrono", "dup"])
def test_same_final_state_as_the_pre_176_code(engine, order):
    new = _replay(engine, order)
    assert new["_meta"]["crashes"] == 0
    rep = cmp.compare(_golden(order), new)
    assert _no_differences(rep) == [], json.dumps(rep["tables"], ensure_ascii=False)[:2000]
    # the old schema's columns are all still there (nothing silently dropped)
    for t, v in rep["tables"].items():
        assert v["rows_old"] == v["rows_new"] > 0 or t == "race_laps", t


def _truth() -> tuple[set[tuple[str, str]], dict[str, dict], dict[str, int]]:
    """What really happened, from the fixture itself:
    * (race_id, horse_id) the NEWEST 出馬表 of each race shows as not started;
    * per race, the odds payload with the newest official time (number -> values);
    * per race, the number of horses that really started (newest 出馬表)."""
    newest_entries: dict[str, dict] = {}
    newest_odds: dict[str, dict] = {}
    for e in rr.load_events(FIX / "events.jsonl.gz"):
        if e["kind"] == "entries" and (
            e["race_id"] not in newest_entries or e["t"] > newest_entries[e["race_id"]]["t"]
        ):
            newest_entries[e["race_id"]] = e
        if e["kind"] == "odds":
            off = json.loads(e["body"])["data"]["official_datetime"]
            if e["race_id"] not in newest_odds or off > newest_odds[e["race_id"]]["off"]:
                newest_odds[e["race_id"]] = {
                    "off": off,
                    "odds": json.loads(e["body"])["data"]["odds"],
                }
    cancelled, started_n = set(), {}
    for rid, e in newest_entries.items():
        horses = parse_entries(e["body"]).horses
        started_n[rid] = sum(1 for h in horses if h.entry_status == "started")
        for h in horses:
            if h.entry_status != "started":
                cancelled.add((rid, f"nk:{h.netkeiba_horse_id}"))
    return cancelled, newest_odds, started_n


_PRICE_COLS = {
    "odds": ("1", 0),
    "popularity": ("1", 2),
    "place_odds_low": ("2", 0),
    "place_odds_high": ("2", 1),
    "place_popularity": ("2", 2),
}


def _as_payload_value(col: str, v):
    if v is None:
        return None
    return int(v) if col.endswith("popularity") else f"{float(v):.1f}"


def test_out_of_order_pages_only_differ_towards_the_truth(engine):
    """Every difference from the old code must be the new code being closer to what happened:
    a scratched horse stays scratched with no stale price, the newest quote wins (the old code
    dropped it as a 'partial field' after reviving a scratched horse, or let an older quote
    overwrite a newer one), and 脚質 is derived from the real number of starters."""
    new = _replay(engine, "shuffle")
    assert new["_meta"]["crashes"] == 0
    rep = cmp.compare(_golden("shuffle"), new, max_examples=10_000)
    cancelled, newest_odds, started_n = _truth()
    assert cancelled, "fixture must contain cancellations"
    races_with_cancel = {rid for rid, _ in cancelled}
    numbers = {(r["race_id"], r["horse_id"]): r["horse_number"] for r in new["race_horses"]["rows"]}
    corners = {
        (r["race_id"], r["horse_id"]): r["corner_orders"] for r in new["race_results"]["rows"]
    }
    for table, v in rep["tables"].items():
        assert v["only_old"] == v["only_new"] == 0, table
        if table == "races":
            assert set(v["columns"]) <= {
                "place_odds_official_at",
                "place_odds_observed_at",
                "win_odds_official_at",
                "win_odds_observed_at",
            }, v["columns"]
            for col, d in v["columns"].items():
                for ex in d["examples"]:
                    rid = json.loads(ex["key"][0])
                    assert rid in races_with_cancel, ex
                    if col.endswith("official_at"):
                        newest = newest_odds[rid]["off"].replace(" ", "T")
                        assert (
                            ex["new"].startswith(newest[:16]) or ex["new"][:16] > ex["old"][:16]
                        ), ex
            continue
        if table != "race_horses":
            assert v["rows_differing"] == 0, (table, v["columns"])
            continue
        allowed = {"entry_status", "running_style", *_PRICE_COLS}
        assert set(v["columns"]) <= allowed, v["columns"]
        for col, d in v["columns"].items():
            for ex in d["examples"]:
                key = tuple(json.loads(k) for k in ex["key"])
                rid = key[0]
                assert rid in races_with_cancel, (
                    col,
                    ex,
                )  # differences only where a horse was scratched
                if col == "entry_status":
                    assert (
                        key in cancelled and ex["new"] == "cancelled" and ex["old"] == "started"
                    ), ex
                elif col == "running_style":
                    expected = up._derive_running_style(
                        tuple(corners[key]) if corners.get(key) else None, started_n[rid]
                    )
                    assert ex["new"] == expected, (ex, started_n[rid])
                elif key in cancelled:
                    assert ex["new"] is None, ex  # no stale price on a scratched horse
                else:
                    group, idx = _PRICE_COLS[col]
                    cell = newest_odds[rid]["odds"][group].get(f"{numbers[key]:02d}")
                    assert cell is not None and _as_payload_value(col, ex["new"]) == (
                        int(cell[idx]) if col.endswith("popularity") else cell[idx]
                    ), (col, ex, cell)
    states = {(r["race_id"], r["horse_id"]): r["entry_status"] for r in new["race_horses"]["rows"]}
    assert all(states[k] != "started" for k in cancelled if k in states)
