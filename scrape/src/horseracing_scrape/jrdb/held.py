"""Operator approval of ``held`` JRDB files (Feature 178, T073; data-model INV-F2; manual.md §3).

A file whose content differs from the version already applied, and whose ordering the rules
cannot decide (only 速報→正式 and 日次→バックナンバー are automatic), is recorded ``held`` by
``jrdb-fetch`` and is never written by the writer commands. This module is the two halves of the
manual path:

* :func:`describe` — what the operator reads before deciding: the held row, the version it
  would replace, and a row-by-row diff of the two decoded files (key = race / race×馬番 /
  血統番号). Read only; ``jrdb-fetch --show-held`` prints it for every held row. The diff is a
  diff of the INPUTS, not a forecast of product changes: first-writer items never move, and the
  transition items move only where ``precedence.py`` lets any later arrival move them
  (codex D26 #7).
* :func:`approve` — the decision: the current version becomes ``superseded`` and the held row
  becomes ``fetched`` (with an empty ``race_status``) so the ordinary writer command re-applies
  the day through the SAME rules. Nothing here writes a product table. Guards (codex D26
  #2/#4/#5): the operator names the version being replaced (``replaces``), another approved-but-
  not-yet-applied version of the same key blocks a second approval, and two active versions
  (an INV-F1 violation) stop the path instead of being hidden by an ordering.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from horseracing_db.models.jrdb import JrdbFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import files as jfiles
from .decode import DecodedFile, decode_file
from .errors import JrdbError

#: the writer command that re-applies a day once a file of this kind is approved
COMMAND_FOR_KIND = {
    "BAC": "jrdb-entries",
    "KYI": "jrdb-entries",
    "UKC": "jrdb-entries",
    "TYB": "jrdb-prerace",
    "SED": "jrdb-results",
    "SRB": "jrdb-results",
    "HJC": "jrdb-payouts",
}
#: kinds whose rows are inputs to another kind's races: approving one re-opens those races
DRIVER_KIND_FOR = {"BAC": "KYI", "UKC": "KYI", "SRB": "SED"}
MAX_LISTED = 50
ACTIVE = ("applied", "partially_applied")
DIFF_NOTE = (
    "入力ファイルの差分であって製品表の変更予定ではない: 先着保持の項目(距離・馬名・クラス等)は"
    "承認しても変わらず、遷移規則の項目(取消・騎手・体重・払戻)だけが precedence の規則どおり動く。"
    "実際に何が変わったかは承認後の writer job の summary で確認する"
)


class HeldNotApprovable(ValueError):
    """The row cannot be approved (not found / not ``held`` / inconsistent siblings / no text)."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def list_held(session: Session, data_date: datetime.date | None = None) -> list[JrdbFile]:
    q = select(JrdbFile).where(JrdbFile.status == "held")
    if data_date is not None:
        q = q.where(JrdbFile.data_date == data_date)
    return list(session.scalars(q.order_by(JrdbFile.data_date, JrdbFile.kind, JrdbFile.fetched_at)))


def load_row(session: Session, file_id: str) -> JrdbFile:
    try:
        key = uuid.UUID(str(file_id))
    except ValueError:
        raise HeldNotApprovable("held_not_found", f"not a file id: {file_id!r}") from None
    row = session.get(JrdbFile, key)
    if row is None:
        raise HeldNotApprovable("held_not_found", f"no jrdb_files row {file_id}")
    return row


def _siblings(session: Session, row: JrdbFile, statuses: tuple[str, ...]) -> list[JrdbFile]:
    return list(
        session.scalars(
            select(JrdbFile)
            .where(
                JrdbFile.kind == row.kind,
                JrdbFile.data_date == row.data_date,
                JrdbFile.variant == row.variant,
                JrdbFile.status.in_(statuses),
                JrdbFile.jrdb_file_id != row.jrdb_file_id,
            )
            .order_by(JrdbFile.fetched_at)
        )
    )


def current_version(session: Session, row: JrdbFile) -> JrdbFile | None:
    """The applied / partially applied version of the same (kind, data_date, variant). INV-F1
    says there is at most one; two is an inconsistency to surface, not to order away
    (codex D26 #4)."""

    active = _siblings(session, row, ACTIVE)
    if len(active) > 1:
        raise HeldNotApprovable(
            "inv_f1_violation",
            f"{len(active)} active versions of {row.kind} {row.data_date} {row.variant}: "
            + ", ".join(str(a.jrdb_file_id) for a in active),
        )
    return active[0] if active else None


def pending_version(session: Session, row: JrdbFile) -> JrdbFile | None:
    """An approved-but-not-yet-applied (``fetched``) sibling: the writer must run first, or two
    versions would be read in one pass (codex D26 #2)."""

    pending = _siblings(session, row, ("fetched",))
    return pending[0] if pending else None


def find_text(artifacts: Path, row: JrdbFile) -> Path | None:
    folder = Path(artifacts) / row.kind / row.data_date.strftime("%Y%m%d")
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.txt")):
        if hashlib.sha256(path.read_bytes()).hexdigest() == row.sha256:
            return path
    return None


def _decode(path: Path, row: JrdbFile, *, profile: str) -> DecodedFile:
    raw = path.read_bytes()
    meta = jfiles.build_meta(
        path.name.split(".")[0] + ".txt",  # ``KYI260912.<sha12>.txt`` → the plain name
        raw,
        variant=row.variant,  # type: ignore[arg-type]
        fetched_at=row.fetched_at,
        data_date=row.data_date,
    )
    return decode_file(raw, meta, profile=profile)


def _key_text(key) -> str:
    return f"{key.venue}{key.yy:02d}{key.kai}{key.day_hex}{key.r:02d}"


def row_key(kind: str, row: Any) -> str:
    """The identity of one decoded row inside its file (what a diff lines up on)."""

    if kind in ("KYI", "TYB", "SED"):
        return f"{_key_text(row.key)}#{row.horse_number:02d}"
    if kind in ("BAC", "SRB", "HJC"):
        return _key_text(row.key)
    if kind == "UKC":
        return row.blood_reg_no
    if kind in ("KZA", "CZA"):
        return row.code
    raise ValueError(f"no row key for kind {kind}")


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime.date, datetime.time, datetime.timedelta)):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return str(value)
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value if isinstance(value, (int, float, str, bool)) or value is None else str(value)


def _index(kind: str, decoded: DecodedFile | None) -> tuple[dict, int]:
    """rows by key + the number of duplicate keys (a duplicate hides a row from the diff)."""

    if decoded is None:
        return {}, 0
    keys = [row_key(kind, r) for r in decoded.rows]
    dups = sum(n - 1 for n in Counter(keys).values() if n > 1)
    return dict(zip(keys, decoded.rows, strict=True)), dups


def diff_decoded(kind: str, current: DecodedFile | None, held: DecodedFile) -> dict:
    """Rows only in one file and rows whose fields differ. The TOTALS are over every row; only
    the listings are capped at ``MAX_LISTED`` (``truncated`` says so), and the per-field counts
    cover all changed rows so a 取消 beyond the cap is still visible (codex D26 #10)."""

    cur, cur_dups = _index(kind, current)
    new, new_dups = _index(kind, held)
    only_held = sorted(set(new) - set(cur))
    only_cur = sorted(set(cur) - set(new))
    changed: list[dict] = []
    field_counts: Counter[str] = Counter()
    for k in sorted(set(cur) & set(new)):
        a, b = dataclasses.asdict(cur[k]), dataclasses.asdict(new[k])
        fields = {
            name: [_plain(a[name]), _plain(b[name])]
            for name in a
            if name != "key" and a[name] != b.get(name)
        }
        if fields:
            changed.append({"key": k, "fields": fields})
            field_counts.update(fields.keys())
    return {
        "rows_current": len(cur),
        "rows_held": len(new),
        "rejected_rows_current": len(current.rejected_rows) if current is not None else None,
        "rejected_rows_held": len(held.rejected_rows),
        "duplicate_keys_current": cur_dups,
        "duplicate_keys_held": new_dups,
        "n_only_in_held": len(only_held),
        "n_only_in_current": len(only_cur),
        "n_changed": len(changed),
        "changed_fields_count": dict(sorted(field_counts.items())),
        "only_in_held": only_held[:MAX_LISTED],
        "only_in_current": only_cur[:MAX_LISTED],
        "changed": changed[:MAX_LISTED],
        "truncated": max(len(only_held), len(only_cur), len(changed)) > MAX_LISTED,
    }


def _row_info(row: JrdbFile | None) -> dict | None:
    if row is None:
        return None
    return {
        "jrdb_file_id": str(row.jrdb_file_id),
        "kind": row.kind,
        "data_date": row.data_date.isoformat(),
        "variant": row.variant,
        "status": row.status,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "races": len(row.race_status or {}),
    }


def describe(session: Session, artifacts: Path, row: JrdbFile, *, profile: str = "current") -> dict:
    """The operator's material for one held row: both versions and their diff (read only).
    ``error`` is set whenever the comparison is incomplete; the approval refuses then."""

    out: dict = {
        "held": _row_info(row),
        "current": None,
        "pending": None,
        "reapply_command": COMMAND_FOR_KIND.get(row.kind),
        "decode_profile": profile,
        "note": DIFF_NOTE,
        "diff": None,
        "error": None,
    }
    try:
        cur = current_version(session, row)
    except HeldNotApprovable as exc:
        out["error"] = str(exc)
        return out
    out["current"] = _row_info(cur)
    pend = pending_version(session, row)
    out["pending"] = _row_info(pend)
    held_path = find_text(artifacts, row)
    if held_path is None:
        out["error"] = f"held text not under {artifacts}/{row.kind}/{row.data_date:%Y%m%d}"
        return out
    try:
        held_dec = _decode(held_path, row, profile=profile)
        cur_dec = None
        if cur is not None:
            cur_path = find_text(artifacts, cur)
            if cur_path is None:
                out["error"] = "current version's text is not on disk (INV-F3); diff is one-sided"
            else:
                cur_dec = _decode(cur_path, cur, profile=profile)
        out["diff"] = diff_decoded(row.kind, cur_dec, held_dec)
    except JrdbError as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def approve(
    session: Session,
    row: JrdbFile,
    *,
    now: datetime.datetime,
    replaces: str | None = None,
) -> dict:
    """Make the held row the version to apply. ``replaces`` names the current version the
    operator saw (its file id, or ``"none"`` when there is none); a different current version
    at approval time stops the approval (codex D26 #5). The current version becomes
    ``superseded``; the held row becomes ``fetched`` with an empty ``race_status`` (every race
    is 'not yet applied' for the writer); when the kind is an input of another kind's races
    (BAC/UKC → KYI, SRB → SED) the day's applied driver rows OF THE SAME VARIANT are re-opened
    the same way (their earlier per-race record is kept by the ingestion job rows). No product
    table is touched here: the caller runs the writer command, whose rules decide what may
    change."""

    if row.status != "held":
        raise HeldNotApprovable("not_held", f"{row.jrdb_file_id} is {row.status}")
    cur = current_version(session, row)
    pend = pending_version(session, row)
    if pend is not None:
        raise HeldNotApprovable(
            "pending_version",
            f"{pend.jrdb_file_id} is approved but not yet applied; run "
            f"{COMMAND_FOR_KIND.get(row.kind)} --date {row.data_date} first",
        )
    expected = str(cur.jrdb_file_id) if cur is not None else "none"
    if replaces is None:
        raise HeldNotApprovable(
            "replaces_required",
            f"name the version being replaced: --replaces {expected}",
        )
    if str(replaces).strip().lower() != expected.lower():
        raise HeldNotApprovable(
            "current_changed",
            f"the current version is {expected}, not {replaces}; re-run --show-held",
        )
    if cur is not None:
        jfiles.mark_superseded(session, cur)
    row.status = "fetched"
    row.race_status = {}
    row.applied_at = None
    reopened: list[str] = []
    driver = DRIVER_KIND_FOR.get(row.kind)
    if driver is not None:
        for d in session.scalars(
            select(JrdbFile).where(
                JrdbFile.kind == driver,
                JrdbFile.data_date == row.data_date,
                JrdbFile.variant == row.variant,
                JrdbFile.status.in_(ACTIVE),
            )
        ):
            d.race_status = {}
            d.status = "fetched"
            reopened.append(str(d.jrdb_file_id))
    session.flush()
    return {
        "approved": str(row.jrdb_file_id),
        "superseded": str(cur.jrdb_file_id) if cur is not None else None,
        "reopened_driver_rows": reopened,
        "approved_at": now.isoformat(),
        "reapply_command": COMMAND_FOR_KIND.get(row.kind),
        "recovery": "if the writer fails, re-run reapply_command for the day; do not approve again",
    }


__all__ = [
    "COMMAND_FOR_KIND",
    "DIFF_NOTE",
    "DRIVER_KIND_FOR",
    "HeldNotApprovable",
    "approve",
    "current_version",
    "describe",
    "diff_decoded",
    "find_text",
    "list_held",
    "load_row",
    "pending_version",
    "row_key",
]
