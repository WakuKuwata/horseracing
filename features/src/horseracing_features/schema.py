"""Fixed-schema column order for the FeatureMatrix (derived from the registry)."""

from __future__ import annotations

from .registry import IDENTIFIER_COLUMNS, REGISTRY

#: feature columns in registry order
FEATURE_COLUMNS: tuple[str, ...] = tuple(REGISTRY.keys())

#: full matrix column order: identifiers first, then features
ALL_COLUMNS: tuple[str, ...] = IDENTIFIER_COLUMNS + FEATURE_COLUMNS

#: default cutoff: low_history if 1..LOW_HISTORY_MAX actual starts (0 = debut)
DEFAULT_LOW_HISTORY_MAX = 2
