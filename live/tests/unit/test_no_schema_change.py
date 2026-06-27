"""T014 (019): live introduces NO schema change (SC-009). Static checks (no DB)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def test_no_live_migrations_and_head_unchanged():
    # live/ must not ship any alembic migration
    assert not (_ROOT / "live" / "migrations").exists()
    # the migration head remains 0006 (no new versions added by 019)
    versions = sorted(p.name for p in (_ROOT / "db" / "migrations" / "versions").glob("0*.py"))
    assert versions[-1].startswith("0006_"), f"unexpected migration head: {versions[-1]}"


def test_live_package_has_no_orm_models():
    # live reuses existing tables only — it defines no ORM models / tables of its own
    src = _ROOT / "live" / "src" / "horseracing_live"
    for f in src.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "__tablename__" not in text, f"{f} defines a table (schema change)"
