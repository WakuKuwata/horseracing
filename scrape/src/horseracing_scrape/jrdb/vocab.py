"""JRDB running style / bloodline line → the current DB's vocabulary (2026-11-10).

The displayed market-ev model (``mev-ens15-jrdb-v5``) was trained on JRDB rows whose
``running_style`` and ``sire_line`` were translated into the current DB's vocabulary:

* running style: a frozen decision tree (depth 8, min leaf 200, fitted on 2007-2009 rows of the
  same race in both DBs) from the JRDB SED style code, corner orders, finish order and the started
  count → one of the JRA-VAN labels (2010-2024 holdout agreement 97.8%);
* bloodline line: JRDB 4-digit line code → the modal current family over the same horses (99.9%).

Both tables are vendor-derived and live under ``artifacts/jrdb/vocab/`` (gitignored, membership
terms); their sha256 is pinned here, so a changed file is refused rather than used. The tree is
evaluated without scikit-learn or numpy: inputs and thresholds are compared as float32, as sklearn
does
(parity on all 972,296 JRDB rows: 0 mismatches, 2026-11-10).

Nothing here touches the database. Raw JRDB codes are never returned — only labels of the current
vocabulary (the write boundary re-checks the vocabulary, ``profile.check_payload``).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import math
import os
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

LOG = logging.getLogger(__name__)

#: JRA-VAN running-style labels (the current DB's vocabulary).
RUNNING_STYLE_LABELS: frozenset[str] = frozenset(
    {"逃げ", "先行", "中団", "後方", "差し", "追込", "ﾏｸﾘ"}
)
#: the current DB's bloodline families (sire_line / damsire_line).
LINE_FAMILIES: frozenset[str] = frozenset(
    {
        "ロイヤルチャージャー系",
        "ニアークティック系",
        "ネイティヴダンサー系",
        "ナスルーラ系",
        "その他のエクリプス系",
        "セントサイモン系",
        "トゥルビヨン系",
        "マンノウォー系",
    }
)
#: From this race date the current DB's running_style was written only by netkeiba's first-corner
#: heuristic (a different definition: no 後方/ﾏｸﾘ). The JRDB label replaces it from here on;
#: earlier (JRA-VAN) values are never changed.
STYLE_OVERWRITE_FROM = datetime.date(2025, 10, 11)

TREE_FILE = "running_style_tree.json"
LINE_FILE = "line_map.json"
TREE_SHA256 = "a78c8e30195eb861ae05cc08f70f76e82d35baccec9b2fc2acfe651e48be2ee9"
LINE_SHA256 = "190ff22819a79bf89d9b736aea4c25e7f77a2b214441f9790b36db275c9877a9"
TREE_FEATURES = ("code", "first", "pen", "last", "fin", "fs", "ncorner", "gain", "gain2")
MISSING = -1.0


def _f32(v: float) -> float:
    """Round to IEEE float32 (sklearn compares float32 inputs with float32 thresholds)."""
    return struct.unpack("f", struct.pack("f", v))[0]


def default_dir() -> Path:
    env = os.environ.get("HORSERACING_JRDB_VOCAB_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[4] / "artifacts" / "jrdb" / "vocab"


class VocabUnavailable(RuntimeError):
    """The translation tables are missing or are not the pinned files."""


def _read_pinned(path: Path, sha: str) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise VocabUnavailable(f"{path.name}: {exc}") from exc
    got = hashlib.sha256(raw).hexdigest()
    if got != sha:
        raise VocabUnavailable(f"{path.name}: sha256 {got[:12]} != pinned {sha[:12]}")
    return json.loads(raw.decode("utf-8"))


def style_features(
    code: str | int | None,
    corners: Sequence[int | str | None] | None,
    finish_order: int | None,
    field_size: int,
) -> list[float]:
    """The tree's inputs, exactly as at fit time: corners = the stored non-empty positions in
    order (JRDB 4-slot packed), first / second-last / last ÷ field size, finish ÷ field size,
    field size (started count), number of corners, the two gains; NaN → −1."""

    fs = float(field_size)
    pos = [float(c) for c in (corners or ()) if c not in (None, "", 0, "0")]

    def at(i: int) -> float:
        try:
            return pos[i] / fs if pos else math.nan
        except IndexError:
            return math.nan

    try:
        c = float(code) if code is not None else math.nan
    except (TypeError, ValueError):
        c = math.nan
    first, pen, last = at(0), at(-2), at(-1)
    fin = float(finish_order) / fs if finish_order is not None else math.nan
    vals = [c, first, pen, last, fin, fs, float(len(pos)), first - last, pen - last]
    return [MISSING if math.isnan(v) else v for v in vals]


@dataclass(frozen=True)
class Translator:
    tree: dict
    lines: dict[str, str]

    def style_label(
        self,
        code: str | int | None,
        corners: Sequence[int | str | None] | None,
        finish_order: int | None,
        field_size: int,
    ) -> str | None:
        """The JRA-VAN label of one finished/stopped/disqualified run, or None when JRDB gives no
        style code (nothing is invented) or the field size is unknown."""

        if code is None or field_size <= 0:
            return None
        x = [_f32(v) for v in style_features(code, corners, finish_order, field_size)]
        nodes = self.tree["nodes"]
        index = {name: i for i, name in enumerate(self.tree["features"])}
        i = 0
        while "leaf" not in nodes[i]:
            n = nodes[i]
            i = n["l"] if x[index[n["f"]]] <= _f32(n["t"]) else n["r"]
        label = nodes[i]["leaf"]
        if label not in RUNNING_STYLE_LABELS:
            raise VocabUnavailable(f"tree leaf {label!r} is outside the current vocabulary")
        return label

    def line_family(self, code: str | None) -> str | None:
        if not code:
            return None
        family = self.lines.get(str(code).strip())
        if family is not None and family not in LINE_FAMILIES:
            raise VocabUnavailable(f"line map value {family!r} is outside the current vocabulary")
        return family


def load_translator(directory: Path | None = None) -> Translator:
    d = Path(directory) if directory is not None else default_dir()
    tree = _read_pinned(d / TREE_FILE, TREE_SHA256)
    lines = _read_pinned(d / LINE_FILE, LINE_SHA256)
    if tuple(tree.get("features", ())) != TREE_FEATURES:
        raise VocabUnavailable(f"tree features {tree.get('features')} != {TREE_FEATURES}")
    return Translator(tree=tree, lines=dict(lines["map"]))


@lru_cache(maxsize=1)
def _cached() -> Translator | None:
    try:
        return load_translator()
    except VocabUnavailable as exc:
        LOG.warning("JRDB vocabulary translator unavailable: %s", exc)
        return None


def translator_or_none() -> Translator | None:
    """The default translator (loaded once), or None when its files are missing/changed — the
    caller then writes nothing translated and counts it (``style_translation_unavailable``)."""

    return _cached()


__all__ = [
    "LINE_FAMILIES",
    "RUNNING_STYLE_LABELS",
    "STYLE_OVERWRITE_FROM",
    "Translator",
    "VocabUnavailable",
    "default_dir",
    "load_translator",
    "style_features",
    "translator_or_none",
]
