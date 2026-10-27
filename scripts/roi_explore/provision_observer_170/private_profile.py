"""Private durable staging and atomic publication; no DB, password generation or collection.

Secret bodies and their hashes never appear in returns, logs or exceptions. The provisioning
caller serializes its transaction: existing-file replacement uses an optimistic expected-byte
comparison, not a portable cross-process content-CAS primitive.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class PrivateProfileError(ValueError):
    """Only fixed, non-secret error codes are exposed."""


def _fail(code):
    raise PrivateProfileError(code)


def _path(path):
    try:
        result = Path(os.path.abspath(os.fspath(path)))
    except (TypeError, ValueError, OSError):
        _fail("private_profile_path_invalid")
    # Do not follow a symlink in the file or any ancestor. Callers using system aliases such
    # as /tmp should pass the already resolved actual directory instead.
    for ancestor in reversed(result.parents):
        try:
            mode = ancestor.lstat().st_mode
        except OSError:
            _fail("private_profile_parent_missing")
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            _fail("private_profile_parent_unsafe")
    return result


def _parent_fd(path):
    return os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )


def _stat(parent_fd, name):
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _private(st):
    if (
        st is None
        or not stat.S_ISREG(st.st_mode)
        or stat.S_IMODE(st.st_mode) != 0o600
        or st.st_nlink != 1
        or st.st_uid != os.geteuid()
    ):
        _fail("private_profile_file_unsafe")


def _read_private(parent_fd, name):
    st = _stat(parent_fd, name)
    if st is None:
        _fail("private_profile_staging_missing")
    _private(st)
    fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        _private(opened)
        if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
            _fail("private_profile_file_changed")
        body = handle.read()
    return st, body


def stage_profile(path, body: bytes) -> Path:
    """Create one new 0600 staging file, fsync bytes and its existing parent directory."""
    if not isinstance(body, bytes) or not body:
        _fail("private_profile_body_invalid")
    path = _path(path)
    parent = None
    created = None
    try:
        parent = _parent_fd(path)
        if _stat(parent, path.name) is not None:
            _fail("private_profile_staging_exists")
        fd = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            created = os.fstat(handle.fileno())
            _private(created)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        _, saved = _read_private(parent, path.name)
        if saved != body:
            _fail("private_profile_readback_failed")
        os.fsync(parent)
        return path
    except PrivateProfileError:
        raise
    except OSError:
        # On an incomplete staging write, remove only the inode this call created.
        if created is not None and parent is not None:
            try:
                current = _stat(parent, path.name)
                if current is not None and (current.st_dev, current.st_ino) == (
                    created.st_dev,
                    created.st_ino,
                ):
                    os.unlink(path.name, dir_fd=parent)
                    os.fsync(parent)
            except OSError:
                pass
        raise PrivateProfileError("private_profile_staging_io_failed") from None
    finally:
        if parent is not None:
            os.close(parent)


def publish_profile(staged, final, *, expected_previous: bytes | None) -> dict:
    """Publish staged bytes only when the existing private profile matches the expectation.

    No DB rollback is attempted here. If publication or fsync fails after DB commit, retain the
    private staging/profile files and let the caller record a non-secret recovery receipt.
    """
    if expected_previous is not None and not isinstance(expected_previous, bytes):
        _fail("private_profile_expected_body_invalid")
    staged, final = _path(staged), _path(final)
    if staged.parent != final.parent or staged == final:
        _fail("private_profile_publish_paths_invalid")
    parent = None
    try:
        parent = _parent_fd(staged)
        source_st, _ = _read_private(parent, staged.name)
        target_st = _stat(parent, final.name)
        if expected_previous is None:
            if target_st is not None:
                _fail("private_profile_target_exists")
        else:
            if target_st is None:
                _fail("private_profile_previous_missing")
            target_st, previous = _read_private(parent, final.name)
            if previous != expected_previous:
                _fail("private_profile_previous_mismatch")
        # Recheck the known inodes immediately before the publication. The parent descriptor
        # pins the directory while the provisioning caller owns serialization.
        current_source = _stat(parent, staged.name)
        if current_source is None or (current_source.st_dev, current_source.st_ino) != (
            source_st.st_dev,
            source_st.st_ino,
        ):
            _fail("private_profile_file_changed")
        if target_st is None:
            # link is exclusive: a concurrently created target cannot be overwritten.
            os.link(
                staged.name,
                final.name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
            os.fsync(parent)
            os.unlink(staged.name, dir_fd=parent)
        else:
            current_target = _stat(parent, final.name)
            if current_target is None or (
                current_target.st_dev,
                current_target.st_ino,
            ) != (target_st.st_dev, target_st.st_ino):
                _fail("private_profile_file_changed")
            os.replace(staged.name, final.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        published = _stat(parent, final.name)
        _private(published)
        if (published.st_dev, published.st_ino) != (source_st.st_dev, source_st.st_ino):
            _fail("private_profile_file_changed")
        return dict(profile_path=str(final), mode=oct(stat.S_IMODE(published.st_mode)))
    except PrivateProfileError:
        raise
    except OSError:
        raise PrivateProfileError("private_profile_publish_io_failed") from None
    finally:
        if parent is not None:
            os.close(parent)
