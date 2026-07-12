"""Feature 067 leak boundary: id_mappings resolution is a data-repair concern, never a model
feature. The features package must not import IdMapping nor reference id_mappings, so the physical
canonical id (post-repair) flows in purely as the horse_id/jockey_id/trainer_id key — the mapping
table itself never touches the feature path (constitution II)."""

from __future__ import annotations

import pathlib

import horseracing_features

SRC = pathlib.Path(horseracing_features.__file__).resolve().parent
_FORBIDDEN = ("IdMapping", "id_mappings", "mapping_status", "resolve_entity", "classify_identity")


def test_features_package_does_not_reference_id_mappings():
    offenders: list[str] = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in _FORBIDDEN:
            if token in text:
                offenders.append(f"{py.name}: {token}")
    assert not offenders, f"id_mappings leaked into the feature path: {offenders}"
