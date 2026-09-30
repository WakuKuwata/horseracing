"""ORM models — importing this module registers every table on ``Base.metadata``."""

from __future__ import annotations

from .chaos import ChaosReadout, ChaosSnapshot, FetchThrottleState
from .core import Horse, Jockey, Race, RaceHorse, RaceResult, Trainer
from .ingestion import IdMapping, IngestionJob
from .market import ExoticOdds, ExoticQuote, RaceLaps
from .market_ev import MarketEvPrediction
from .prediction import (
    DiagnosticRun,
    FeatureSnapshot,
    ModelVersion,
    PredictionRun,
    RacePrediction,
    Recommendation,
)
from .purchase import PURCHASE_KINDS, PurchaseRecord

__all__ = [
    "ChaosReadout",
    "PURCHASE_KINDS",
    "PurchaseRecord",
    "ChaosSnapshot",
    "FetchThrottleState",
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
    "ExoticQuote",
    "MarketEvPrediction",
    "RaceLaps",
    "ModelVersion",
    "PredictionRun",
    "RacePrediction",
    "FeatureSnapshot",
    "Recommendation",
]
