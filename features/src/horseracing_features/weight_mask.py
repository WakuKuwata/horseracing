"""Deterministic race-level masking for same-day weight features."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pandas as pd

WEIGHT_MASK_COLUMNS = ("weight", "weight_diff", "carried_weight_ratio")

_RACE_ID_COLUMN = "race_id"
_HASH_MANTISSA_BITS = 53


@dataclass(frozen=True)
class MaskSpec:
    """Configuration for deterministic race-level weight masking."""

    rate: float
    seed: int
    unit: str = "race"

    def __post_init__(self) -> None:
        if not 0.0 <= self.rate <= 1.0:
            raise ValueError("rate must be between 0.0 and 1.0")
        if self.unit != "race":
            raise ValueError("unit must be 'race'")


def _race_unit_interval(race_id: object, seed: int) -> float:
    """Map a race/seed pair to a stable value in the half-open interval [0, 1)."""
    payload = json.dumps(
        [str(race_id), seed],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    mantissa = int.from_bytes(digest[:8], "big") >> (64 - _HASH_MANTISSA_BITS)
    return mantissa / (1 << _HASH_MANTISSA_BITS)


def apply_weight_mask(frame: pd.DataFrame, *, spec: MaskSpec | None) -> pd.DataFrame:
    """Return a copy with selected races' same-day weight features set to NaN."""
    if spec is None:
        return frame.copy(deep=True)

    missing_columns = [column for column in WEIGHT_MASK_COLUMNS if column not in frame.columns]
    if missing_columns:
        raise KeyError(f"missing weight mask columns: {missing_columns}")
    if _RACE_ID_COLUMN not in frame.columns:
        raise KeyError(f"missing race identifier column: {_RACE_ID_COLUMN!r}")
    if frame[_RACE_ID_COLUMN].isna().any():
        raise ValueError("race_id must not contain missing values")

    result = frame.copy(deep=True)
    masked_race_ids = {
        race_id
        for race_id in frame[_RACE_ID_COLUMN].unique()
        if _race_unit_interval(race_id, spec.seed) < spec.rate
    }
    if not masked_race_ids:
        return result

    masked_rows = result[_RACE_ID_COLUMN].isin(masked_race_ids)
    result.loc[masked_rows, list(WEIGHT_MASK_COLUMNS)] = float("nan")
    return result
