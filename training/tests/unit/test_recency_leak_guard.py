"""Feature 101: 時間重みを結果・オッズから隔離する leak-guard。"""

from __future__ import annotations

import ast
import datetime
import inspect
from pathlib import Path

import horseracing_training.recency as recency_mod
import numpy as np
from horseracing_training.recency import RecencyWeightSpec, build_recency_weights

_FORBIDDEN_IMPORT_MARKERS = (
    "race_result",
    "odds",
    "outcome",
    "finish",
    "ranking",
    "payoff",
    "payout",
    "label",
    "target",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _inputs() -> tuple[np.ndarray, np.ndarray, datetime.date, RecencyWeightSpec]:
    cutoff = datetime.date(2026, 3, 1)
    race_ids = np.array(["A", "A", "B", "C", "C", "C"])
    race_dates = np.array(
        [cutoff] * 2
        + [cutoff - datetime.timedelta(days=180)]
        + [cutoff - datetime.timedelta(days=900)] * 3
    )
    return race_ids, race_dates, cutoff, RecencyWeightSpec(half_life_days=365)


def test_builder_signature_accepts_only_date_inputs() -> None:
    """着順・オッズを渡す入口自体をなくし、結果情報による重みリークを構造的に防ぐ。"""
    signature = inspect.signature(build_recency_weights)

    assert tuple(signature.parameters) == ("race_ids", "race_dates", "cutoff", "spec")
    assert signature.parameters["cutoff"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["spec"].kind is inspect.Parameter.KEYWORD_ONLY


def test_recency_module_does_not_import_result_or_odds_modules() -> None:
    """結果・オッズ系モジュールへの依存を AST で検出し、純粋な日付関数の境界を守る。"""
    source_path = Path(recency_mod.__file__)
    forbidden = sorted(
        module
        for module in _imported_modules(source_path)
        if any(marker in module.lower() for marker in _FORBIDDEN_IMPORT_MARKERS)
    )

    assert not forbidden, f"recency.py imports result/odds modules: {forbidden}"


def test_builder_is_deterministic_for_identical_inputs() -> None:
    """同じ日付契約から毎回同一値を返し、再学習結果が呼出し時の状態に依存するのを防ぐ。"""
    race_ids, race_dates, cutoff, spec = _inputs()

    first = build_recency_weights(race_ids, race_dates, cutoff=cutoff, spec=spec)
    second = build_recency_weights(race_ids, race_dates, cutoff=cutoff, spec=spec)

    np.testing.assert_array_equal(first, second)


def test_row_permutation_preserves_row_aligned_weights() -> None:
    """入力行の並び替えで対応行の重みが変わらず、データ読み込み順への依存を防ぐ。"""
    race_ids, race_dates, cutoff, spec = _inputs()
    weights = build_recency_weights(race_ids, race_dates, cutoff=cutoff, spec=spec)
    permutation = np.array([5, 1, 3, 0, 4, 2])

    permuted = build_recency_weights(
        race_ids[permutation],
        race_dates[permutation],
        cutoff=cutoff,
        spec=spec,
    )

    np.testing.assert_allclose(permuted, weights[permutation], rtol=1e-12, atol=0.0)
