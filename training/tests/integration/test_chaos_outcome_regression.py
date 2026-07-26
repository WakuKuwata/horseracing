"""Outcome-facing regression for the Feature 084 top-3 chaos readout.

Unlike the arithmetic invariant tests, this frozen OOS fixture verifies that the
readout tracks the outcomes it names.  Feature 066 would have failed this test.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from horseracing_eval.stage_discount import StageDiscount
from horseracing_probability.chaos_artifact import load_chaos_artifact
from horseracing_probability.chaos_distribution import band_of, chaos_distribution
from horseracing_probability.chaos_events import CHAOS_EVENTS_V1
from horseracing_probability.market_odds import market_implied_win_probs

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_METADATA_PATH = (
    _REPO_ROOT / "training" / "tests" / "fixtures" / "chaos_outcome_fixture.json"
)
_ARTIFACT_DIR = _REPO_ROOT / "artifacts" / "chaos_bands"
_APPROVED_MANIFEST_PATH = _REPO_ROOT / "config" / "chaos_bands_approved.json"
_PLACEHOLDER_SHA256 = "0" * 64
_BANDS = ("t3_calm", "t3_mild", "t3_mid", "t3_rough", "t3_wild")
_EVENT_KEYS = ("s_ge_20", "himo_are", "total_collapse")

# The recorded discovery rates are rounded to three decimal places.  An absolute
# 0.002 tolerance covers that rounding and minor parquet/engine float serialization.
_RATE_ABS_TOLERANCE = 0.002
_EXPECTED_RATES = {
    "t3_calm": {"s_ge_20": 0.007, "himo_are": 0.011, "total_collapse": 0.003},
    "t3_mild": {"s_ge_20": 0.036, "himo_are": 0.076, "total_collapse": 0.016},
    "t3_mid": {"s_ge_20": 0.070, "himo_are": 0.122, "total_collapse": 0.029},
    "t3_rough": {"s_ge_20": 0.132, "himo_are": 0.154, "total_collapse": 0.065},
    "t3_wild": {"s_ge_20": 0.244, "himo_are": 0.175, "total_collapse": 0.092},
}
_REFERENCE_CHAOS_BRIER = 0.0766
_REFERENCE_BASE_RATE_BRIER = 0.0841
_BRIER_ABS_TOLERANCE = 0.002
_MINIMUM_BRIER_IMPROVEMENT = 0.004


def _load_fixture_metadata() -> dict[str, str]:
    return json.loads(_FIXTURE_METADATA_PATH.read_text(encoding="utf-8"))


def _fixture_skip_reason() -> str | None:
    try:
        metadata = _load_fixture_metadata()
        digest = str(metadata["sha256"])
        parquet_path = _FIXTURE_METADATA_PATH.parent / str(metadata["parquet"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return f"SC-008 frozen fixture metadata is unavailable: {exc}"
    if digest == _PLACEHOLDER_SHA256:
        return "SC-008 frozen fixture SHA-256 is the ORCHESTRATOR placeholder"
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return "SC-008 frozen fixture SHA-256 is not a lowercase 64-hex digest"
    if not parquet_path.is_file():
        return f"SC-008 frozen parquet fixture is absent: {parquet_path}"
    return None


_FIXTURE_SKIP_REASON = _fixture_skip_reason()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _FIXTURE_SKIP_REASON is not None,
        reason=_FIXTURE_SKIP_REASON or "SC-008 frozen fixture is ready",
    ),
]


def _as_list(value) -> list:
    if isinstance(value, np.ndarray):
        return value.tolist()
    return list(value)


def _verified_fixture() -> tuple[pd.DataFrame, dict[str, str]]:
    metadata = _load_fixture_metadata()
    fixture_path = _FIXTURE_METADATA_PATH.parent / metadata["parquet"]
    actual_digest = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    assert actual_digest == metadata["sha256"], (
        "frozen fixture digest drifted; regenerate intentionally and update "
        "chaos_outcome_fixture.json"
    )
    return pd.read_parquet(fixture_path), metadata


def test_band_tracks_named_outcomes_on_frozen_fixture() -> None:
    frame, metadata = _verified_fixture()
    assert not frame.empty
    assert set(frame.columns) == {
        "race_id",
        "race_date",
        "horse_ids",
        "popularity",
        "odds",
        "first_horse_id",
        "second_horse_id",
        "third_horse_id",
    }

    artifact_digest = metadata["artifact_digest"]
    approved_manifest = json.loads(_APPROVED_MANIFEST_PATH.read_text(encoding="utf-8"))
    approved_digests = tuple(
        str(row["digest"]) for row in approved_manifest["approved"]
    )
    assert artifact_digest in approved_digests
    artifact = load_chaos_artifact(
        _ARTIFACT_DIR / f"{artifact_digest}.json",
        approved_digests=approved_digests,
        target_date=datetime.date.fromisoformat(str(frame["race_date"].min())),
    )
    discount = StageDiscount(lambda2=artifact.lambda2, lambda3=artifact.lambda3)
    events = {event.key: event for event in CHAOS_EVENTS_V1}
    outcomes_by_band: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    s_ge_20_probabilities: list[float] = []
    s_ge_20_outcomes: list[int] = []

    for row in frame.itertuples(index=False):
        horse_ids = [str(value) for value in _as_list(row.horse_ids)]
        popularity = [int(value) for value in _as_list(row.popularity)]
        odds = [float(value) for value in _as_list(row.odds)]
        assert len(horse_ids) == len(popularity) == len(odds)

        q = market_implied_win_probs(dict(zip(horse_ids, odds, strict=True)))
        ranks = dict(zip(horse_ids, popularity, strict=True))
        adjusted = chaos_distribution(
            q,
            ranks,
            CHAOS_EVENTS_V1,
            stage_discount=discount,
        )
        band = band_of(adjusted.event_mass["s_ge_20"], artifact.quintile_edges)
        top3_ranks = (
            ranks[str(row.first_horse_id)],
            ranks[str(row.second_horse_id)],
            ranks[str(row.third_horse_id)],
        )

        row_outcomes = {
            key: int(events[key].predicate(*top3_ranks, len(horse_ids)))
            for key in _EVENT_KEYS
        }
        for key, outcome in row_outcomes.items():
            outcomes_by_band[band][key].append(outcome)
        s_ge_20_probabilities.append(adjusted.event_mass["s_ge_20"])
        s_ge_20_outcomes.append(row_outcomes["s_ge_20"])

    realized_rates: dict[str, dict[str, float]] = {}
    for band in _BANDS:
        assert band in outcomes_by_band, f"frozen fixture contains no rows for {band}"
        realized_rates[band] = {}
        for key in _EVENT_KEYS:
            values = outcomes_by_band[band][key]
            assert values, f"frozen fixture contains no {key} outcomes for {band}"
            realized_rates[band][key] = float(np.mean(values))
            assert realized_rates[band][key] == pytest.approx(
                _EXPECTED_RATES[band][key],
                abs=_RATE_ABS_TOLERANCE,
            )

    s_ge_20_rates = [realized_rates[band]["s_ge_20"] for band in _BANDS]
    assert all(
        lower < upper for lower, upper in pairwise(s_ge_20_rates)
    ), f"s_ge_20 realized rates are not strictly monotone: {s_ge_20_rates}"

    probabilities = np.asarray(s_ge_20_probabilities, dtype=float)
    outcomes = np.asarray(s_ge_20_outcomes, dtype=float)
    chaos_brier = float(np.mean((probabilities - outcomes) ** 2))
    base_rate = float(outcomes.mean())
    base_rate_brier = float(np.mean((base_rate - outcomes) ** 2))

    assert chaos_brier == pytest.approx(
        _REFERENCE_CHAOS_BRIER,
        abs=_BRIER_ABS_TOLERANCE,
    )
    assert base_rate_brier == pytest.approx(
        _REFERENCE_BASE_RATE_BRIER,
        abs=_BRIER_ABS_TOLERANCE,
    )
    assert chaos_brier <= base_rate_brier - _MINIMUM_BRIER_IMPROVEMENT
