"""Source-identity adapter tests: temporary source bytes, no models or data."""
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import owned_source_identity_135_54586a4bf86c as identity


@pytest.fixture
def source_tree(tmp_path, monkeypatch):
    isolated, original = tmp_path/'isolated', tmp_path/'original'
    files = [isolated/'scripts'/f'{name}.py' for name in ('screen','stack','probe','bootstrap','dataset')]
    files += [isolated/'training/src/horseracing_training/folklore_candidates.sql']
    for i,p in enumerate(files):
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text('synthetic source '+str(i))
    stable = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()
    utility = SimpleNamespace(__file__=str(isolated/'scripts/utility.py'), stable_hash=stable)
    screen = ModuleType('gap_season_recheck'); screen.__file__=str(files[0]); screen.ROOT=isolated
    screen.stack=SimpleNamespace(__file__=str(files[1]), p=utility)
    for name,p in zip(('probe','bootstrap','dataset'),files[2:5]):
        setattr(screen,name,SimpleNamespace(__file__=str(p)))
    screen.source_hash=lambda:utility.stable_hash({str(p):identity.byte_sha(p) for p in files})
    monkeypatch.setitem(sys.modules,'gap_season_recheck',screen)
    return isolated,original,files,screen,utility,stable


def test_actual_bytes_with_original_source_names_and_no_ordinary_hash_change(source_tree):
    isolated,original,files,screen,utility,stable=source_tree
    ordinary={'ordinary':str(isolated/'unrelated')}; old_source=screen.source_hash
    expected=stable({str(original/p.relative_to(isolated)):identity.byte_sha(p) for p in files})
    with identity.source_identity_context(isolated,original):
        assert utility.stable_hash(ordinary)==stable(ordinary)
        assert screen.source_hash()==expected
        assert utility.stable_hash is stable
        files[2].write_text('changed synthetic source')
        assert screen.source_hash()!=expected  # no constant acceptance of stored hash
    assert screen.source_hash is old_source and utility.stable_hash is stable


def test_source_exception_restores_original_functions(source_tree):
    isolated,original,files,screen,utility,stable=source_tree
    original_call=screen.source_hash
    with pytest.raises(FileNotFoundError):
        with identity.source_identity_context(isolated,original):
            files[0].unlink();screen.source_hash()
    assert screen.source_hash is original_call and utility.stable_hash is stable


def test_constant_source_hash_is_rejected(source_tree):
    isolated,original,_,screen,_,_=source_tree
    screen.source_hash=lambda:'pretend-registered-hash'
    with identity.source_identity_context(isolated,original),pytest.raises(AssertionError,match='exactly once'):
        screen.source_hash()


def test_source_from_original_mutable_tree_is_rejected(source_tree):
    isolated,original,_,screen,_,_=source_tree
    screen.dataset.__file__=str(original/'eval/dataset.py')
    with pytest.raises(AssertionError),identity.source_identity_context(isolated,original):
        pass
