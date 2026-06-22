"""Train-only / out-of-fold target encoding (US3, research R8).

Encodings are fit ONLY on races strictly before ``train_cutoff`` so valid/test
outcomes never leak. Unknown categories map to the train-period overall mean
(never 0-filled or error).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

import pandas as pd
from horseracing_db.enums import ResultStatus

from .loader import Frames

_CATEGORY_SOURCES = {"jockey_id", "trainer_id", "venue_code"}


@dataclass(frozen=True)
class TargetEncoding:
    category: str
    mapping: dict
    default: float

    def transform(self, values: pd.Series) -> pd.Series:
        return values.map(self.mapping).fillna(self.default)


def fit_target_encoding(
    frames: Frames, *, train_cutoff: datetime.date, category: str
) -> TargetEncoding:
    if category not in _CATEGORY_SOURCES:
        raise ValueError(f"unsupported category: {category}")

    races = frames.races[["race_id", "race_date", "venue_code"]].copy()
    races["race_date"] = pd.to_datetime(races["race_date"])
    rh = frames.race_horses[["race_id", "horse_id", "jockey_id", "trainer_id"]]
    rr = frames.race_results[["race_id", "horse_id", "finish_order", "result_status"]]
    df = rh.merge(races, on="race_id", how="left").merge(rr, on=["race_id", "horse_id"], how="left")

    # train-only: strictly before the cutoff; finished only for the win label
    df = df[df["race_date"] < pd.Timestamp(train_cutoff)]
    df = df[df["result_status"] == ResultStatus.FINISHED]
    df["win"] = (df["finish_order"] == 1).astype(int)

    default = float(df["win"].mean()) if len(df) else 0.0
    mapping = df.groupby(category)["win"].mean().to_dict() if len(df) else {}
    return TargetEncoding(category=category, mapping=mapping, default=default)
