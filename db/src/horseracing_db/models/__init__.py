"""ORM models — importing this module registers every table on ``Base.metadata``."""

from __future__ import annotations

from .core import Horse, Jockey, Race, RaceHorse, RaceResult, Trainer
from .ingestion import IdMapping, IngestionJob
from .market import ExoticOdds
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
    "ExoticOdds",
    "ModelVersion",
    "PredictionRun",
    "RacePrediction",
    "FeatureSnapshot",
    "Recommendation",
]
