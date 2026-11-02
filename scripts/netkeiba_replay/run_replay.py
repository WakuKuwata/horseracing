"""Replay archived netkeiba events through ONE version of the scrape code and dump the DB state.

    python run_replay.py --code-root <repo or extracted tree> --db-url URL --events E.jsonl.gz \
        --out state.json [--order chrono|dup|shuffle] [--seed N]

``--code-root`` is put FIRST on sys.path (``scrape/src``, ``db/src``) so the same interpreter runs
either the old main tree (extracted with ``git archive``) or the new branch. The database is
migrated with THAT tree's own migrations to its own head (or ``--revision`` / ``--migrate-root``).
Each event goes through the real pipeline entry point the ops refresh uses (``scrape_entries`` /
``scrape_odds`` / ``scrape_results``) with a fixture fetcher serving the archived body, and with the
clock frozen at the event's time (``_has_started`` and every observation time read it).

``replay`` / ``dump`` / ``order_events`` are also imported by the scrape regression test.
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import json
import random
import sys
import types
from pathlib import Path

TABLES = {
    # table: (key columns, columns never compared)
    "races": (("race_id",), set()),
    "race_horses": (("race_id", "horse_id"), set()),
    "race_results": (("race_id", "horse_id"), set()),
    "horses": (("horse_id",), set()),
    "jockeys": (("jockey_id",), set()),
    "trainers": (("trainer_id",), set()),
    "official_win_payouts": (("race_id", "horse_number"), set()),
    "exotic_odds": (("race_id", "bet_type", "selection"), {"exotic_odds_id"}),
    "race_laps": (("race_id",), set()),
    "id_mappings": (("entity_type", "source", "source_id"), {"id_mapping_id", "conflict_group_id"}),
}
ALWAYS_SKIP = {"created_at", "updated_at"}


def load_events(path: str | Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def order_events(events: list[dict], order: str, seed: int = 1) -> list[dict]:
    """The delivery orders the parity check runs."""
    if order == "chrono":
        return list(events)
    if order == "dup":
        # every fetch happens twice (a re-pressed refresh), and each race gets one more full
        # refresh an hour after its last result page (entries + odds + results again)
        out = []
        for e in events:
            out.append(e)
            t2 = datetime.datetime.fromisoformat(e["t"]) + datetime.timedelta(seconds=2)
            out.append({**e, "t": t2.isoformat()})
        last: dict[str, dict] = {}
        for e in events:
            last.setdefault(e["race_id"], {})[e["kind"]] = e
        for _rid, by_kind in last.items():
            if "results" not in by_kind:
                continue
            t_late = datetime.datetime.fromisoformat(by_kind["results"]["t"]) + datetime.timedelta(
                hours=1
            )
            for k in ("entries", "odds", "results"):
                if k in by_kind:
                    out.append({**by_kind[k], "t": t_late.isoformat()})
                    t_late += datetime.timedelta(seconds=1)
        out.sort(key=lambda e: e["t"])
        return out
    if order == "shuffle":
        # the same pages, each race's fetches in a random order (two refreshes racing each other,
        # a stale page applied after a newer one); a page keeps the time it was fetched
        rng = random.Random(seed)
        by_race: dict[str, list[dict]] = {}
        for e in events:
            by_race.setdefault(e["race_id"], []).append(e)
        out = []
        for rid in sorted(by_race):
            evs = list(by_race[rid])
            rng.shuffle(evs)
            out.extend(evs)
        return out
    raise ValueError(order)


def _migrate(code_root: Path, db_url: str, revision: str = "head") -> str:
    import os

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(code_root / "db" / "alembic.ini"))
    cfg.set_main_option("script_location", str(code_root / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    os.environ["DATABASE_URL"] = db_url
    command.upgrade(cfg, revision)
    return revision if revision != "head" else ScriptDirectory.from_config(cfg).get_current_head()


def freeze_clock(pipeline, up) -> dict:
    """Make every clock the pipeline/writers read return ``clock["now"]``. The old tree stamps some
    observation times with ``datetime.datetime.now()`` directly, so those modules get a datetime
    whose now() is the same frozen clock (like for like between old and new)."""
    clock: dict = {"now": None}
    saved = [
        (m, n, getattr(m, n)) for m in (pipeline, up) for n in ("_now", "datetime") if hasattr(m, n)
    ]

    def restore() -> None:
        for m, n, v in saved:
            setattr(m, n, v)

    clock["restore"] = restore
    pipeline._now = lambda: clock["now"]
    if hasattr(up, "_now"):
        up._now = lambda: clock["now"]

    class _FrozenDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            t = clock["now"]
            return t if tz is None else t.astimezone(tz)

    shim = types.SimpleNamespace(
        **{k: getattr(datetime, k) for k in dir(datetime) if not k.startswith("__")}
    )
    shim.datetime = _FrozenDateTime
    for mod in (up, pipeline):
        if getattr(mod, "datetime", None) is datetime:
            mod.datetime = shim
    return clock


def replay(
    factory, events: list[dict], *, pipeline, fetcher_cls, clock: dict, log=sys.stderr
) -> int:
    """Feed every event through the real pipeline entry point. Returns the number of crashes."""
    errors = 0
    with factory() as session:
        for e in events:
            clock["now"] = datetime.datetime.fromisoformat(e["t"])
            fetcher = fetcher_cls({e["url"]: e["body"]})
            try:
                if e["kind"] == "entries":
                    pipeline.scrape_entries(
                        session, urls=[e["url"]], fetcher=fetcher, complete_profiles_after=False
                    )
                elif e["kind"] == "odds":
                    pipeline.scrape_odds(session, urls=[e["url"]], fetcher=fetcher)
                else:
                    pipeline.scrape_results(session, urls=[e["url"]], fetcher=fetcher)
            except Exception as exc:  # noqa: BLE001 — a crash is a finding, keep replaying
                errors += 1
                session.rollback()
                print(
                    f"crash {e['kind']} {e['race_id']} {e['t']}: {type(exc).__name__}: {exc}",
                    file=log,
                )
    return errors


def dump(session) -> dict:
    from sqlalchemy import text

    out = {}
    for table, (key, skip) in TABLES.items():
        cols = [
            r[0]
            for r in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"
                ),
                {"t": table},
            )
        ]
        cols = [c for c in cols if c not in ALWAYS_SKIP and c not in skip]
        rows = session.execute(text(f"SELECT {', '.join(cols)} FROM {table}")).mappings().all()
        out[table] = {
            "columns": cols,
            "key": list(key),
            "rows": [{c: _jsonable(r[c]) for c in cols} for r in rows],
        }
    jobs = session.execute(
        text("SELECT job_type, status, count(*) FROM ingestion_jobs GROUP BY 1, 2 ORDER BY 1, 2")
    ).all()
    out["ingestion_jobs"] = [[j, s, n] for j, s, n in jobs]
    return out


def _jsonable(v):
    if isinstance(v, datetime.timedelta):
        return v.total_seconds()
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if hasattr(v, "is_finite"):  # Decimal
        return str(v)
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", required=True)
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--order", default="chrono", choices=("chrono", "dup", "shuffle"))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument(
        "--revision",
        default="head",
        help="migrate only to this revision (e.g. the OLD head, to see what new code "
        "does against an un-migrated database)",
    )
    ap.add_argument("--limit", type=int, default=None, help="replay only the first N events")
    ap.add_argument(
        "--migrate-root",
        default=None,
        help="migrate with THIS tree's migrations (default: --code-root), e.g. old code "
        "on a database the new tree migrated",
    )
    a = ap.parse_args(argv)
    root = Path(a.code_root).resolve()
    mroot = Path(a.migrate_root).resolve() if a.migrate_root else root
    sys.path[:0] = [str(root / "scrape" / "src"), str(root / "db" / "src")]

    head = _migrate(mroot, a.db_url, a.revision)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import horseracing_scrape
    from horseracing_scrape import pipeline
    from horseracing_scrape import upsert as up
    from horseracing_scrape.fetch import FixtureFetcher

    assert str(root) in str(Path(horseracing_scrape.__file__).resolve()), (
        horseracing_scrape.__file__
    )
    factory = sessionmaker(bind=create_engine(a.db_url), expire_on_commit=False)
    events = order_events(load_events(a.events), a.order, a.seed)[: a.limit]
    clock = freeze_clock(pipeline, up)
    errors = replay(factory, events, pipeline=pipeline, fetcher_cls=FixtureFetcher, clock=clock)
    with factory() as session:
        state = dump(session)
    state["_meta"] = {
        "code_root": str(root),
        "head": head,
        "order": a.order,
        "seed": a.seed,
        "events": len(events),
        "crashes": errors,
    }
    Path(a.out).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(state["_meta"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
