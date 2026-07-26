"""ORM models — importing this module registers every table on ``Base.metadata``."""

from __future__ import annotations

from .chaos import ChaosReadout, ChaosSnapshot
from .core import Horse, Jockey, Race, RaceHorse, RaceResult, Trainer
from .ingestion import IdMapping, IngestionJob
from .market import ExoticOdds, RaceLaps
from .prediction import (
    DiagnosticRun,
    FeatureSnapshot,
    ModelVersion,
    PredictionRun,
    RacePrediction,
    Recommendation,
)

__all__ = [
    "ChaosReadout",
    "ChaosSnapshot",
    "DiagnosticRun",
    "Race",
    "Horse",
    "Jockey",
    "Trainer",
    "RaceHorse",
    "RaceResult",
    "IdMapping",
    "IngestionJob",
    "ExoticOdds",
    "RaceLaps",
    "ModelVersion",
    "PredictionRun",
    "RacePrediction",
    "FeatureSnapshot",
    "Recommendation",
]
