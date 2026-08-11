"""Golden vectors for process-stable race-level weight masking."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pandas as pd
import pytest

from horseracing_features.weight_mask import MaskSpec, apply_weight_mask

GOLDEN_RACE_IDS = (
    "202601010101",
    "202601010102",
    "202601010103",
    "202602020201",
    "202602020202",
    "202603030301",
    "202603030302",
    "202604040401",
    "202605050501",
    "202606060601",
    "202607070701",
    "202608080801",
)
GOLDEN_SEED = 20260810
GOLDEN_RATE = 0.5
GOLDEN_MASKED_RACE_IDS = (
    "202601010101",
    "202601010102",
    "202601010103",
    "202603030302",
    "202605050501",
    "202606060601",
    "202607070701",
)


def _golden_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "race_id": GOLDEN_RACE_IDS,
            "weight": [480.0] * len(GOLDEN_RACE_IDS),
            "weight_diff": [0.0] * len(GOLDEN_RACE_IDS),
            "carried_weight_ratio": [0.115] * len(GOLDEN_RACE_IDS),
        }
    )


def _selected_races(frame: pd.DataFrame) -> tuple[str, ...]:
    return tuple(sorted(frame.loc[frame["weight"].isna(), "race_id"].unique()))


def test_golden_masked_race_ids() -> None:
    result = apply_weight_mask(
        _golden_frame(),
        spec=MaskSpec(rate=GOLDEN_RATE, seed=GOLDEN_SEED),
    )

    assert _selected_races(result) == GOLDEN_MASKED_RACE_IDS


@pytest.mark.parametrize("python_hash_seed", ["0", "8675309"])
def test_golden_vector_is_stable_across_processes(python_hash_seed: str) -> None:
    script = f"""
import json
import pandas as pd
from horseracing_features.weight_mask import MaskSpec, apply_weight_mask

race_ids = {GOLDEN_RACE_IDS!r}
frame = pd.DataFrame({{
    "race_id": race_ids,
    "weight": [480.0] * len(race_ids),
    "weight_diff": [0.0] * len(race_ids),
    "carried_weight_ratio": [0.115] * len(race_ids),
}})
result = apply_weight_mask(
    frame,
    spec=MaskSpec(rate={GOLDEN_RATE!r}, seed={GOLDEN_SEED!r}),
)
selected = sorted(result.loc[result["weight"].isna(), "race_id"].unique())
print(json.dumps(selected))
"""
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = python_hash_seed

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert tuple(json.loads(completed.stdout)) == GOLDEN_MASKED_RACE_IDS
