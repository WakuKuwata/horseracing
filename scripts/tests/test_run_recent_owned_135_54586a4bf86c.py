"""Owned namespace dispatch proof; fake phase modules and temporary files only."""
from pathlib import Path
import sys
from types import ModuleType

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_recent_owned_135_54586a4bf86c as wrapper


@pytest.mark.parametrize('phase', ['capture', 'predict', 'score', 'audit-inputs', 'audit-predictions', 'audit-scores'])
def test_every_phase_routes_reads_writes_and_evidence_away_from_legacy(tmp_path, monkeypatch, phase):
    owned, evidence = tmp_path/'owned', tmp_path/'evidence'
    legacy = wrapper.ROOT/'artifacts/135-mixture-reproducibility/recent'
    monkeypatch.setattr(wrapper, 'WORK', owned)
    monkeypatch.setattr(wrapper, 'EVIDENCE', evidence)
    touched, calls = [], []
    native_open = Path.open
    def guarded_open(path, *args, **kwargs):
        assert path != legacy and legacy not in path.parents, 'Legacy recent accessed'
        touched.append(path)
        return native_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_open)
    core, inputs, scores = (ModuleType(x) for x in ('mixture_recent_owned_135_54586a4bf86c',
        'audit_mixture_recent_inputs_135', 'audit_mixture_recent_135'))
    for module in (core, inputs, scores):
        module.WORK = legacy
        monkeypatch.setitem(sys.modules, module.__name__, module)
    core.source_files = lambda: {'original.py': 'original-sha'}
    core.digest = lambda p: 'synthetic-sha-' + Path(p).name
    def phase_core():
        assert core.WORK == owned
        core.WORK.mkdir()
        path = core.WORK/'synthetic.json'; path.write_text('{}'); assert path.read_text() == '{}'
        sources = core.source_files()
        assert len(sources) == 5 and sources['original.py'] == 'original-sha'
        assert str(Path(wrapper.__file__).resolve()) in sources
        calls.append(phase)
        return {'synthetic': True}
    core.capture = core.predict = core.score = phase_core
    def phase_inputs(name):
        assert inputs.WORK == owned
        assert name == phase.removeprefix('audit-')
        # The predictions audit's prerequisite inputs review follows the same routing.
        for value in ('inputs', 'predictions'):
            assert inputs.output_path(value) == evidence/('recent-'+value+'-independent-review.json')
        path = inputs.output_path(name); path.write_text('{}'); assert path.read_text() == '{}'
        calls.append(phase)
    inputs.run = phase_inputs
    def phase_scores():
        assert scores.WORK == owned and scores.OUT == evidence/'recent-numeric-review.json'
        scores.OUT.write_text('{}'); assert scores.OUT.read_text() == '{}'
        calls.append(phase)
    scores.run = phase_scores
    monkeypatch.setattr(sys, 'argv', ['owned-wrapper', phase])
    wrapper.main()
    assert calls == [phase] and touched
    assert all(p.is_relative_to(tmp_path) for p in touched)
