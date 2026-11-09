"""ORM models — importing this module registers every table on ``Base.metadata``."""

from __future__ import annotations

from .attention import AttentionCheckpoint, AttentionPick, AttentionRaceScan
from .chaos import ChaosReadout, ChaosSnapshot, FetchThrottleState
from .core import Horse, Jockey, Race, RaceHorse, RaceResult, Trainer
from .decision_quotes import DecisionQuote
from .ingestion import IdMapping, IngestionJob
from .jrdb import JrdbFile
from .market import ExoticOdds, ExoticQuote, OfficialWinPayout, RaceLaps
from .market_ev import MarketEvPrediction
from .prediction import (
    DiagnosticRun,
    FeatureSnapshot,
    ModelVersion,
    PredictionRun,
    RacePrediction,
    Recommendation,
)
from .provenance import RaceFieldProvenance, RaceHorseFieldProvenance
from .purchase import PURCHASE_KINDS, PurchaseRecord
from .track_conditions import JraTrackCondition

__all__ = [
    "AttentionCheckpoint",
    "AttentionPick",
    "AttentionRaceScan",
    "ChaosReadout",
    "PURCHASE_KINDS",
    "PurchaseRecord",
    "ChaosSnapshot",
    "DecisionQuote",
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
    "JrdbFile",
    "ExoticOdds",
    "ExoticQuote",
    "MarketEvPrediction",
    "OfficialWinPayout",
    "RaceLaps",
    "ModelVersion",
    "PredictionRun",
    "RacePrediction",
    "FeatureSnapshot",
    "Recommendation",
    "RaceFieldProvenance",
    "RaceHorseFieldProvenance",
    "JraTrackCondition",
]
