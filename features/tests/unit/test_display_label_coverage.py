"""Every model-input feature column must have a Japanese display label in the front.

Feature 040 introduced ``front/src/components/featureLabels.ts`` as the single
source of truth for feature-name → display label (fail-open: unknown names render
as-is). Fail-open means a forgotten label degrades silently — by 2026-08 the
table had drifted 46 columns behind the registry (041/056/058/059/061/069), so
raw names like ``asof_pm_support_last`` leaked into the UI. This test closes the
loop: bumping FEATURE_VERSION with new columns fails here until the label lands.

The check is a light regex over the committed TS file (keys are plain
identifiers on their own line) — no JS tooling involved.
"""

from __future__ import annotations

import re
from pathlib import Path

from horseracing_features.registry import model_input_features

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LABELS_TS = _REPO_ROOT / "front" / "src" / "components" / "featureLabels.ts"


def _label_keys() -> set[str]:
    text = _LABELS_TS.read_text(encoding="utf-8")
    return set(re.findall(r"^  (\w+): \{", text, re.MULTILINE))


def test_every_model_input_column_has_a_display_label() -> None:
    missing = sorted(set(model_input_features()) - _label_keys())
    assert not missing, (
        "model-input feature columns without a display label in "
        "front/src/components/featureLabels.ts (add labels there AND mirror the "
        f"file to admin/src/lib/featureLabels.ts): {missing}"
    )
