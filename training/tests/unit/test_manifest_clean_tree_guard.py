"""Feature 108 T012a: production manifests require a provably clean git tree."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from horseracing_probability import oof_bundle

from horseracing_training import cli, legacy_attest, oof_manifest


def test_dirty_tree_cannot_build_production_manifest(monkeypatch, capsys):
    """A valid HEAD alone is insufficient when porcelain reports uncommitted changes."""

    def fake_git(command, **_kwargs):
        if command == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n")
        if command == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, stdout=" M training/example.py\n")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_git)
    args = SimpleNamespace(allow_dirty=False)

    assert cli._generate_manifest(object(), args) == 2
    assert "refusing to build a production manifest" in capsys.readouterr().out


def test_allow_dirty_builds_fixture_scope(monkeypatch):
    """The explicit escape hatch remains usable but can never mint production scope."""

    def fake_git(command, **_kwargs):
        if command == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="b" * 40 + "\n")
        if command == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, stdout="?? scratch.txt\n")
        raise AssertionError(f"unexpected command: {command}")

    captured = {}

    def fake_build(_session, _bundle, **kwargs):
        captured["artifact_scope"] = kwargs["artifact_scope"]
        return Path("fixture/manifest.json"), {
            "schema_version": 3,
            "artifact_scope": kwargs["artifact_scope"],
            "stages_evaluation": {
                "two_gamma_win": {"verdict": "NO_DECISION", "identity": "fixture"},
                "stage_discount_topk": {"verdict": "NO_DECISION", "identity": "fixture"},
            },
            "activation_eligible": False,
            "fit_through": None,
            "manifest_digest": "fixture-digest",
        }

    monkeypatch.setattr(subprocess, "run", fake_git)
    monkeypatch.setattr(oof_bundle, "read_bundle", lambda _path: {})
    monkeypatch.setattr(legacy_attest, "attestation_from_model_dir", lambda *_a, **_kw: {})
    monkeypatch.setattr(oof_manifest, "build_oof_manifest", fake_build)
    args = SimpleNamespace(
        allow_dirty=True,
        bundle="bundle.json",
        model_dir="model",
        out_root="out",
        gate_config=None,
        seed=0,
        num_threads=1,
    )

    assert cli._generate_manifest(object(), args) == 0
    assert captured["artifact_scope"] == "fixture"


def test_oof_generate_requires_a_caller_supplied_feature_expectation():
    """Feature 108: the feature-version expectation must come from OUTSIDE the attestation.

    An earlier draft passed `attestation["feature_version"]` as the expectation, which makes
    `general_factory_from_attestation`'s comparison compare a value against itself — the guard
    could never fire. 074 added that check precisely so a stale artifact cannot silently drive
    an OOF run, so a tautological version of it is a fail-open.
    """
    import inspect

    from horseracing_training import cli

    src = inspect.getsource(cli._oof_generate)
    assert "args.expect_feature_version" in src
    assert 'attestation["feature_version"]' not in src, (
        "expectation must not be derived from the attestation being validated"
    )
