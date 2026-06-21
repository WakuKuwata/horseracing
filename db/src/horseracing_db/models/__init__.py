"""ORM models — importing this module registers every table on ``Base.metadata``."""

from __future__ import annotations

from .core import Horse, Jockey, Race, RaceHorse, RaceResult, Trainer
from .ingestion import IdMapping, IngestionJob
from .prediction import (
    FeatureSnapshot,
    ModelVersion,
    PredictionRun,
    RacePrediction,
    Recommendation,
)

__all__ = [
    "Race",
    "Horse",
    "Jockey",
    "Trainer",
    "RaceHorse",
    "RaceResult",
    "IdMapping",
    "IngestionJob",
    "ModelVersion",
    "PredictionRun",
    "RacePrediction",
    "FeatureSnapshot",
    "Recommendation",
]
