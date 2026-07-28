"""Feature 084 FR-037: the pure derivation cannot accept outcome/model inputs."""

from __future__ import annotations

import ast
import importlib
import inspect


def _module_tree(module_name: str) -> ast.Module:
    module = importlib.import_module(module_name)
    return ast.parse(inspect.getsource(module))


def test_chaos_modules_do_not_import_outcomes_or_models():
    forbidden_fragments = (
        "race_results",
        "finish_order",
        "horseracing_model",
        "model_probability",
        "model_probabilities",
    )
    for module_name in (
        "horseracing_probability.chaos_distribution",
        "horseracing_probability.chaos_events",
    ):
        tree = _module_tree(module_name)
        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_names.append(node.module or "")
        lowered = "\n".join(imported_names).lower()
        assert all(fragment not in lowered for fragment in forbidden_fragments)


def test_derivation_signatures_accept_only_market_snapshot_inputs():
    module = importlib.import_module("horseracing_probability.chaos_distribution")
    expected_parameters = {
        "chaos_distribution": (
            "q",
            "ranks",
            "events",
            "stage_discount",
            "eps",
            "invariant_tol",
        ),
        "chaos_readout": ("q", "ranks", "events", "stage_discount", "edges"),
        "band_of": ("p_primary", "edges"),
    }
    forbidden_parameters = {
        "race_results",
        "finish_order",
        "model_p",
        "model_probs",
        "model_probabilities",
        "win_probs",
    }

    for function_name, expected in expected_parameters.items():
        signature = inspect.signature(getattr(module, function_name))
        assert tuple(signature.parameters) == expected
        assert not (set(signature.parameters) & forbidden_parameters)
        assert all(
            parameter.kind
            not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            for parameter in signature.parameters.values()
        )


def test_event_predicates_are_typed_executable_callables():
    events_module = importlib.import_module("horseracing_probability.chaos_events")
    for event in events_module.CHAOS_EVENTS_V1:
        assert callable(event.predicate)
        signature = inspect.signature(event.predicate)
        assert tuple(signature.parameters) == ("ra", "rb", "rc", "n")
        assert signature.return_annotation in (bool, "bool")
