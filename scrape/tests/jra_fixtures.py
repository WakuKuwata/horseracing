"""Loader for the JRA official-site fixtures (Feature 176). Raw bytes; decoding is the parser's job."""

from __future__ import annotations

import datetime
import json
from functools import lru_cache
from pathlib import Path

JRA_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jra"
JST = datetime.timezone(datetime.timedelta(hours=9))


@lru_cache(maxsize=1)
def manifest() -> dict:
    return json.loads((JRA_FIXTURES / "manifest.json").read_text(encoding="utf-8"))


def entry(name: str) -> dict:
    for e in manifest()["fixtures"]:
        if e["file"] == name:
            return e
    for e in manifest().get("derived", []):
        if e["file"] == name:
            return e
    raise KeyError(name)


def jra_raw(name: str) -> bytes:
    return (JRA_FIXTURES / name).read_bytes()


def jra_text(name: str) -> str:
    """cp932 strict decode — the same decode the fetcher applies (contracts/fetch-and-parse.md)."""
    charset = entry(name).get("charset", "shift_jis")
    enc = "cp932" if charset in ("shift_jis", "sjis", "windows-31j") else charset
    return jra_raw(name).decode(enc)


def fetched_at(name: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(entry(name)["fetched_at"])
