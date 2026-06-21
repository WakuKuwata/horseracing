"""Shift_JIS (cp932) streaming parser for JRA-VAN year CSVs.

Reads line-by-line in binary so a single decode error or wrong column count is
reported as a RowError (with line number) instead of aborting the whole file.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from . import layout


@dataclass
class ParsedRow:
    line_no: int
    fields: list[str]


@dataclass
class RowError:
    line_no: int
    reason: str


def parse_rows(path: str | Path) -> Iterator[ParsedRow | RowError]:
    with open(path, "rb") as f:
        for line_no, raw in enumerate(f, start=1):
            stripped = raw.rstrip(b"\r\n")
            if not stripped:
                continue
            try:
                text_line = stripped.decode("cp932")
            except UnicodeDecodeError as exc:
                yield RowError(line_no, f"cp932 decode error: {exc}")
                continue
            fields = next(csv.reader([text_line]))
            if len(fields) != layout.EXPECTED_COLUMNS:
                yield RowError(
                    line_no,
                    f"expected {layout.EXPECTED_COLUMNS} columns, got {len(fields)}",
                )
                continue
            yield ParsedRow(line_no, fields)
