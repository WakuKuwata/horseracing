"""Private credential publication without a database or real credential reads."""

from __future__ import annotations

import json
import os
import stat

import pytest

from scripts.roi_explore.provision_observer_170 import private_profile as private

SECRET = b"DATABASE_URL=synthetic-secret-value-170\n"
OLD = b"old-private-placeholder\n"


@pytest.fixture
def folder(tmp_path):
    return tmp_path.resolve()


def test_staging_is_exclusive_private_and_durable(folder, monkeypatch):
    calls = []
    actual = private.os.fsync

    def sync(fd):
        calls.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return actual(fd)

    monkeypatch.setattr(private.os, "fsync", sync)
    path = private.stage_profile(folder / "stage.env", SECRET)
    assert path == folder / "stage.env" and path.read_bytes() == SECRET
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert calls == ["file", "directory"]
    with pytest.raises(
        private.PrivateProfileError, match="private_profile_staging_exists"
    ):
        private.stage_profile(path, b"replacement")
    assert path.read_bytes() == SECRET


def test_publish_replaces_only_expected_existing_profile(folder, monkeypatch):
    final = private.stage_profile(folder / "final.env", OLD)
    staged = private.stage_profile(folder / "stage.env", SECRET)
    inode = staged.stat().st_ino
    events = []
    replace = private.os.replace

    def observed(*args, **kwargs):
        events.append("atomic_replace")
        assert final.read_bytes() == OLD
        return replace(*args, **kwargs)

    monkeypatch.setattr(private.os, "replace", observed)
    result = private.publish_profile(staged, final, expected_previous=OLD)
    assert events == ["atomic_replace"]
    assert final.read_bytes() == SECRET and final.stat().st_ino == inode
    assert not staged.exists()
    assert result == dict(profile_path=str(final), mode="0o600")
    assert SECRET.decode().strip() not in json.dumps(result)
    assert "sha" not in json.dumps(result)


def test_publish_new_file_does_not_use_overwriting_rename(folder, monkeypatch):
    staged = private.stage_profile(folder / "stage.env", SECRET)

    def forbidden(*args, **kwargs):
        raise AssertionError("replace must not be used for absent target")

    monkeypatch.setattr(private.os, "replace", forbidden)
    result = private.publish_profile(staged, folder / "new.env", expected_previous=None)
    assert (folder / "new.env").read_bytes() == SECRET and not staged.exists()
    assert result["mode"] == "0o600"


@pytest.mark.parametrize("expectation", [None, b"another-private-value"])
def test_existing_profile_is_preserved_when_expectation_does_not_match(
    folder, expectation
):
    staged = private.stage_profile(folder / "stage.env", SECRET)
    final = private.stage_profile(folder / "final.env", OLD)
    with pytest.raises(private.PrivateProfileError):
        private.publish_profile(staged, final, expected_previous=expectation)
    assert staged.read_bytes() == SECRET and final.read_bytes() == OLD


def test_missing_previous_is_not_created_under_replace_expectation(folder):
    staged = private.stage_profile(folder / "stage.env", SECRET)
    with pytest.raises(
        private.PrivateProfileError, match="private_profile_previous_missing"
    ):
        private.publish_profile(staged, folder / "missing.env", expected_previous=OLD)
    assert staged.exists() and not (folder / "missing.env").exists()


def test_stage_refuses_file_symlink_without_touching_target(folder):
    target = private.stage_profile(folder / "target.env", OLD)
    link = folder / "link.env"
    link.symlink_to(target)
    with pytest.raises(private.PrivateProfileError):
        private.stage_profile(link, SECRET)
    assert target.read_bytes() == OLD


@pytest.mark.parametrize("which", ["stage", "final"])
def test_publish_refuses_file_symlink(folder, which):
    stage = private.stage_profile(folder / "stage.env", SECRET)
    final = private.stage_profile(folder / "final.env", OLD)
    link = folder / "link.env"
    link.symlink_to(stage if which == "stage" else final)
    with pytest.raises(private.PrivateProfileError):
        private.publish_profile(
            link if which == "stage" else stage,
            link if which == "final" else final,
            expected_previous=OLD,
        )
    assert stage.read_bytes() == SECRET and final.read_bytes() == OLD


def test_symlinked_parent_is_not_followed(folder):
    actual = folder / "real"
    actual.mkdir()
    link = folder / "alias"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(
        private.PrivateProfileError, match="private_profile_parent_unsafe"
    ):
        private.stage_profile(link / "stage.env", SECRET)
    assert list(actual.iterdir()) == []


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o400, 0o660])
def test_publish_refuses_insecure_or_nonexact_staging_mode(folder, mode):
    staged = private.stage_profile(folder / "stage.env", SECRET)
    staged.chmod(mode)
    with pytest.raises(
        private.PrivateProfileError, match="private_profile_file_unsafe"
    ):
        private.publish_profile(staged, folder / "final.env", expected_previous=None)
    assert staged.exists() and not (folder / "final.env").exists()


def test_publish_refuses_insecure_existing_target(folder):
    stage = private.stage_profile(folder / "stage.env", SECRET)
    final = private.stage_profile(folder / "final.env", OLD)
    final.chmod(0o644)
    with pytest.raises(
        private.PrivateProfileError, match="private_profile_file_unsafe"
    ):
        private.publish_profile(stage, final, expected_previous=OLD)
    assert final.read_bytes() == OLD and stage.exists()


def test_publish_rejects_cross_directory_and_same_path(folder):
    stage = private.stage_profile(folder / "stage.env", SECRET)
    other = folder / "other"
    other.mkdir()
    for target in (stage, other / "new.env"):
        with pytest.raises(
            private.PrivateProfileError, match="private_profile_publish_paths_invalid"
        ):
            private.publish_profile(stage, target, expected_previous=None)
    assert stage.exists() and not (other / "new.env").exists()


def test_hardlinked_staging_is_rejected(folder):
    stage = private.stage_profile(folder / "stage.env", SECRET)
    os.link(stage, folder / "alias.env")
    with pytest.raises(
        private.PrivateProfileError, match="private_profile_file_unsafe"
    ):
        private.publish_profile(stage, folder / "new.env", expected_previous=None)
    assert stage.exists() and not (folder / "new.env").exists()


def test_permission_failure_is_sanitized(folder, monkeypatch):
    real = private.os.open

    def denied(path, *args, **kwargs):
        if path == "stage.env":
            raise PermissionError(SECRET.decode())
        return real(path, *args, **kwargs)

    monkeypatch.setattr(private.os, "open", denied)
    with pytest.raises(private.PrivateProfileError) as error:
        private.stage_profile(folder / "stage.env", SECRET)
    assert str(error.value) == "private_profile_staging_io_failed"
    assert error.value.__cause__ is None
    assert not (folder / "stage.env").exists()


def test_file_fsync_failure_cleans_only_own_partial_stage(folder, monkeypatch):
    actual = private.os.fsync

    def failure(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(SECRET.decode())
        return actual(fd)

    monkeypatch.setattr(private.os, "fsync", failure)
    unrelated = folder / "unrelated"
    unrelated.write_bytes(b"unchanged")
    with pytest.raises(private.PrivateProfileError) as error:
        private.stage_profile(folder / "stage.env", SECRET)
    assert str(error.value) == "private_profile_staging_io_failed"
    assert (
        not (folder / "stage.env").exists() and unrelated.read_bytes() == b"unchanged"
    )


def test_failed_publish_keeps_staging_and_old_profile(folder, monkeypatch):
    stage = private.stage_profile(folder / "stage.env", SECRET)
    final = private.stage_profile(folder / "final.env", OLD)

    def denied(*args, **kwargs):
        raise PermissionError(SECRET.decode())

    monkeypatch.setattr(private.os, "replace", denied)
    with pytest.raises(private.PrivateProfileError) as error:
        private.publish_profile(stage, final, expected_previous=OLD)
    assert str(error.value) == "private_profile_publish_io_failed"
    assert stage.read_bytes() == SECRET and final.read_bytes() == OLD


def test_directory_fsync_after_replace_is_recovery_failure_not_secret_leak(
    folder, monkeypatch
):
    stage = private.stage_profile(folder / "stage.env", SECRET)
    final = private.stage_profile(folder / "final.env", OLD)

    def failure(fd):
        raise OSError(SECRET.decode())

    monkeypatch.setattr(private.os, "fsync", failure)
    with pytest.raises(private.PrivateProfileError) as error:
        private.publish_profile(stage, final, expected_previous=OLD)
    assert str(error.value) == "private_profile_publish_io_failed"
    assert final.read_bytes() == SECRET and not stage.exists()


def test_racing_new_target_is_never_overwritten(folder, monkeypatch):
    stage = private.stage_profile(folder / "stage.env", SECRET)
    actual = private.os.link

    def race(*args, **kwargs):
        final = folder / "new.env"
        final.write_bytes(OLD)
        final.chmod(0o600)
        return actual(*args, **kwargs)

    monkeypatch.setattr(private.os, "link", race)
    with pytest.raises(
        private.PrivateProfileError, match="private_profile_publish_io_failed"
    ):
        private.publish_profile(stage, folder / "new.env", expected_previous=None)
    assert (folder / "new.env").read_bytes() == OLD and stage.read_bytes() == SECRET


@pytest.mark.parametrize("body", [b"", None, "secret string"])
def test_invalid_body_exposes_only_fixed_reason(folder, body):
    with pytest.raises(
        private.PrivateProfileError, match="private_profile_body_invalid"
    ):
        private.stage_profile(folder / "stage.env", body)
    assert not (folder / "stage.env").exists()
