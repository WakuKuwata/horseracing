"""Preregistered top-3 chaos events (Feature 084, FR-010a..FR-010e).

Predicates receive the frozen popularity ranks of the first, second, and third
finishers plus the canonical field size.  They are executable callables rather
than audit strings so event mass can be accumulated while ordered triples are
scanned.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

EventPredicate = Callable[[int, int, int, int], bool]
PromotionRole = Literal["controls", "secondary", "not_eligible", "diagnostic_only"]


@dataclass(frozen=True)
class EventDefinition:
    """One frozen event definition and its product/calibration metadata."""

    key: str
    label_ja: str
    predicate: EventPredicate
    infeasible_when_n_le: int
    nested_under: str | None
    lambda_sensitive: bool
    promotion_role: PromotionRole
    min_positives_for_decision: int | None


def _s_ge_20(ra: int, rb: int, rc: int, n: int) -> bool:  # noqa: ARG001
    return ra + rb + rc >= 20


def _s_ge_30(ra: int, rb: int, rc: int, n: int) -> bool:  # noqa: ARG001
    return ra + rb + rc >= 30


def _himo_are(ra: int, rb: int, rc: int, n: int) -> bool:  # noqa: ARG001
    return ra <= 3 and (rb >= 10 or rc >= 10)


def _total_collapse(ra: int, rb: int, rc: int, n: int) -> bool:  # noqa: ARG001
    return ra >= 10


CHAOS_EVENTS_V1: tuple[EventDefinition, ...] = (
    EventDefinition(
        key="s_ge_20",
        label_ja="人気順合計が20以上",
        predicate=_s_ge_20,
        infeasible_when_n_le=7,
        nested_under=None,
        lambda_sensitive=True,
        promotion_role="controls",
        min_positives_for_decision=100,
    ),
    EventDefinition(
        key="himo_are",
        label_ja="1〜3番人気が勝ち、2着か3着に二桁人気",
        predicate=_himo_are,
        infeasible_when_n_le=9,
        nested_under=None,
        lambda_sensitive=True,
        promotion_role="secondary",
        min_positives_for_decision=100,
    ),
    EventDefinition(
        key="total_collapse",
        label_ja="二桁人気が勝つ",
        predicate=_total_collapse,
        infeasible_when_n_le=9,
        nested_under=None,
        lambda_sensitive=False,
        promotion_role="not_eligible",
        min_positives_for_decision=None,
    ),
    EventDefinition(
        key="s_ge_30",
        label_ja="人気順合計が30以上",
        predicate=_s_ge_30,
        infeasible_when_n_le=10,
        nested_under="s_ge_20",
        lambda_sensitive=True,
        promotion_role="diagnostic_only",
        min_positives_for_decision=None,
    ),
)

DEFAULT_EVENTS = CHAOS_EVENTS_V1

__all__ = [
    "CHAOS_EVENTS_V1",
    "DEFAULT_EVENTS",
    "EventDefinition",
    "EventPredicate",
    "PromotionRole",
]
