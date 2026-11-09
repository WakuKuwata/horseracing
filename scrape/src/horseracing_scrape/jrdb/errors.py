"""Typed errors for the JRDB route (Feature 178, T004).

Every error that stops a run is typed so the CLI can report a reason without printing a value
from a member-only file (FR-022) or a credential.
"""

from __future__ import annotations


class JrdbError(RuntimeError):
    """Base class."""


class JrdbDecodeError(JrdbError):
    """A record decoded to something the layout forbids (bad number, inconsistent key, …).

    The message carries a *position* (line number, field name), never the raw bytes of a
    member-only file.
    """

    def __init__(self, message: str, *, line_no: int | None = None, field: str | None = None):
        self.line_no = line_no
        self.field = field
        where = []
        if line_no is not None:
            where.append(f"line {line_no}")
        if field is not None:
            where.append(f"field {field}")
        suffix = f" ({', '.join(where)})" if where else ""
        super().__init__(f"{message}{suffix}")


class JrdbLayoutMismatch(JrdbError):
    """The file's record length does not match the layout (桁ずれ — fail-closed, FR-021a)."""

    def __init__(self, kind: str, expected: int, actual: int, *, line_no: int | None = None):
        self.kind = kind
        self.expected = expected
        self.actual = actual
        self.line_no = line_no
        at = f" at line {line_no}" if line_no is not None else ""
        super().__init__(f"{kind}: record length {actual} != layout {expected}{at}")


class JrdbUnsupportedVersion(JrdbError):
    """No layout table for this (kind, version) — the decoder refuses rather than guessing."""

    def __init__(self, kind: str, version: str | None):
        self.kind = kind
        self.version = version
        super().__init__(f"no JRDB layout for kind={kind!r} version={version!r}")


class JrdbArchiveBlackout(JrdbError):
    """Archive (back-number) download requested inside the member rule's blackout
    (開催日 08:00〜19:00 JST, FR-018). There is no override flag by design."""


class JrdbCredentialsMissing(JrdbError):
    """`JRDB_MEMBER_USER` / `JRDB_MEMBER_PASSWORD` absent. The message never echoes values."""


class JrdbHeld(JrdbError):
    """A differing-content file whose order cannot be decided automatically (D9): the file is
    recorded as ``held`` and nothing is written to product tables."""


class JrdbLzhUnsupported(JrdbError):
    """An ``.lzh`` archive was given but the optional dependency ``lhafile`` is not installed
    (research D4: zip back-numbers cover every year, so LZH is only for the legacy formats)."""


class ProfileViolation(JrdbError):
    """A write targeted a column the active profile does not allow (D7a second guard)."""

    def __init__(self, profile: str, table: str, column: str):
        self.profile = profile
        self.table = table
        self.column = column
        super().__init__(f"profile {profile!r} does not allow writing {table}.{column}")


class JrdbNotFound(JrdbError):
    """The member site answered 404 for a file (not published yet, or a wrong name)."""


class JrdbStartupRefused(JrdbError):
    """A startup check failed: nothing was sent. ``code`` is the summary key (contracts/cli.md)."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
