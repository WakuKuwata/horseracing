"""A registered model must still be loadable tomorrow.

Two independent incidents, same shape: the DB row survived and the file did not.

  * `lgbm-091-wmask` was registered from a git worktree, so `calibrator_uri` pointed at
    `.claude/worktrees/090-.../artifacts/...`. Removing the worktree deleted the calibrator and
    **every production prediction failed** with a bare FileNotFoundError. The file existed at
    registration time, so an existence check would not have caught it — the location itself was
    the defect.
  * A later registration passed `--artifacts-dir .../artifacts/model_versions`, and since the
    writer appends `model_versions/<version>` the artifacts landed at
    `artifacts/model_versions/model_versions/<version>`. Self-consistent, so nothing complained.

Hence the guard is on the ROOT, before anything is written, not on the files afterwards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from horseracing_training.artifacts import DurableArtifactRoot, check_artifact_root


def _worktree(tmp_path: Path) -> Path:
    """A linked git worktree: its `.git` is a FILE (`gitdir: ...`), not a directory. That is the
    invariant we key on — it means "this checkout is disposable"."""
    wt = tmp_path / "repo" / ".claude" / "worktrees" / "some-feature"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /somewhere/.git/worktrees/some-feature\n")
    root = wt / "artifacts"
    root.mkdir()
    return root


def _main_checkout(tmp_path: Path) -> Path:
    main = tmp_path / "repo"
    main.mkdir(parents=True, exist_ok=True)
    (main / ".git").mkdir(exist_ok=True)  # a real checkout: .git is a DIRECTORY
    root = main / "artifacts"
    root.mkdir(exist_ok=True)
    return root


def test_the_main_checkout_is_accepted(tmp_path):
    check_artifact_root(_main_checkout(tmp_path))  # must not raise


def test_registering_from_a_worktree_is_refused(tmp_path):
    """THE production incident. The artifacts exist right now and vanish when the worktree goes."""
    with pytest.raises(DurableArtifactRoot, match="worktree"):
        check_artifact_root(_worktree(tmp_path))


def test_a_root_that_already_ends_in_model_versions_is_refused(tmp_path):
    """The writer appends `model_versions/<version>`; passing it in as well nests it twice and
    silently puts the artifacts off-convention."""
    root = _main_checkout(tmp_path) / "model_versions"
    root.mkdir()
    with pytest.raises(DurableArtifactRoot, match="model_versions"):
        check_artifact_root(root)


def test_the_error_says_what_to_pass_instead(tmp_path):
    """A guard that only says "no" gets worked around. It has to name the fix."""
    root = _main_checkout(tmp_path) / "model_versions"
    root.mkdir()
    with pytest.raises(DurableArtifactRoot) as exc:
        check_artifact_root(root)
    assert str(root.parent) in str(exc.value)


def test_a_relative_root_is_refused(tmp_path, monkeypatch):
    """Known trap: a bare relative URI resolves against the serving CLI's cwd and dies there."""
    monkeypatch.chdir(_main_checkout(tmp_path).parent)
    with pytest.raises(DurableArtifactRoot, match="absolute"):
        check_artifact_root(Path("artifacts"))


def test_the_guard_runs_before_anything_is_written(tmp_path):
    """A guard that fires after the files are on disk leaves debris in the bad location."""
    root = _worktree(tmp_path)
    with pytest.raises(DurableArtifactRoot):
        check_artifact_root(root)
    assert not (root / "model_versions").exists()
