"""Preserve historical source identities while hashing isolated source bytes.

Only feature114 includes absolute filenames in its source-hash dictionary.
The context wraps that one source_hash call, verifies all six actual byte
digests, and changes only the isolated prefix of its dictionary keys. It never
returns a registered hash constant and does not change model/data calculations.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
from pathlib import Path
from threading import RLock
from unittest.mock import patch

_ACTIVE = ContextVar('owned135_source_identity', default=False)
_LOCK = RLock()


def byte_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_keys(values, isolated_root, original_root):
    isolated, original = str(Path(isolated_root).resolve()), str(Path(original_root).resolve())
    assert isolated != original
    out = {}
    for key, value in values.items():
        assert isinstance(key, str) and key.startswith(isolated + '/')
        translated = original + key[len(isolated):]
        assert translated not in out, 'Canonical source identities collided'
        out[translated] = value
    return out


@contextmanager
def source_identity_context(isolated_root, original_root):
    """Install source-only routing; safe to surround any existing CLI phase."""
    import gap_season_recheck as screen
    isolated, original = Path(isolated_root).resolve(), Path(original_root).resolve()
    assert isolated != original
    expected_paths = (Path(screen.__file__), Path(screen.stack.__file__),
                      Path(screen.probe.__file__), Path(screen.bootstrap.__file__),
                      Path(screen.dataset.__file__),
                      Path(screen.ROOT)/'training/src/horseracing_training/folklore_candidates.sql')
    assert all(p.resolve().is_relative_to(isolated) for p in expected_paths)
    assert Path(screen.stack.p.__file__).resolve().is_relative_to(isolated)
    real_source_hash = screen.source_hash
    real_stable_hash = screen.stack.p.stable_hash

    @wraps(real_source_hash)
    def source_hash_with_canonical_identity():
        # The temporary stable_hash replacement affects this thread/context
        # only, and solely during the original feature114 source_hash call.
        with _LOCK:
            calls = 0
            def canonical_stable_hash(values):
                nonlocal calls
                if not _ACTIVE.get():
                    return real_stable_hash(values)
                actual = {str(p): byte_sha(p) for p in expected_paths}
                assert values == actual, 'Feature114 source dictionary or actual bytes differ'
                calls += 1
                return real_stable_hash(canonical_keys(values, isolated, original))
            token = _ACTIVE.set(True)
            try:
                with patch.object(screen.stack.p, 'stable_hash', canonical_stable_hash):
                    result = real_source_hash()
                assert calls == 1, 'Original source hash must hash its actual source dictionary exactly once'
                return result
            finally:
                _ACTIVE.reset(token)

    with patch.object(screen, 'source_hash', source_hash_with_canonical_identity):
        yield
