"""Bundle assembly reads frozen coefficients and certifies compatibility from saved artifacts only."""
import datetime as dt
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate_mixture_bundle as b  # noqa: E402


def test_bundle_identity_and_profile_are_fixed():
    assert b.BUNDLE_ID == '129-125_new_joint_mixed6_v1'
    assert b.mixture_model.PROFILE == '125_new_joint_mixed6_v1'
    assert [m['id'] for m in b.build.MEMBERS] == [m['id'] for m in b.mixture_model.IDENTITIES]


def test_assemble_never_overwrites(tmp_path):
    out = tmp_path / 'bundle.json'
    out.write_text('{}')
    with pytest.raises(FileExistsError):
        b.assemble(out)


def _record(race_id, day, bundle_sha, anchor_sha, *, same=True, post='none'):
    return {'artifact_kind': 'mixture_shadow_record', 'classification': 'rehearsal', 'race_id': race_id,
            'race_day': day, 'primary_regime': 'preweight', 'bundle_manifest_sha256': bundle_sha,
            'anchor_model_sha256': anchor_sha, 'candidate_started_ids': ['a', 'b'],
            'anchor_started_ids': ['a', 'b'], 'candidate_input_sha256': 'x' * 64,
            'anchor_input_sha256': 'x' * 64 if same else 'y' * 64,
            'candidate_audit': {'postprocess': post, 'head_aggregation': 'arithmetic_mean'},
            'seconds': {'candidate': 1., 'anchor': .5, 'feature_build_day': 10.}}


@pytest.mark.parametrize('fault', [None, 'inputs', 'postprocess', 'identity'])
def test_compatibility_booleans_come_from_records(tmp_path, monkeypatch, fault):
    bundle = type('B', (), {'sha256': 'b' * 64, 'members': tuple(range(6))})()
    monkeypatch.setattr(b.mixture_model, 'load_mixture_bundle', lambda path: bundle)
    anchor = tmp_path / 'anchor.json'
    anchor.write_text('{}')
    anchor_sha = b.mixture_shadow.sha256(anchor)
    receipts = tmp_path / 'receipts'
    receipts.mkdir()
    for m in b.build.MEMBERS:
        (receipts / (m['id'] + '.json')).write_text(json.dumps(
            {'parity': {'status': 'PASS', 'max_abs_diff': 0., 'n_head_values': 9}}))
    monkeypatch.setattr(b.build, 'receipt_path', lambda name, smoke=False: receipts / (name + '.json'))
    records = tmp_path / 'records'
    records.mkdir()
    kwargs = {'same': fault != 'inputs', 'post': 'harville' if fault == 'postprocess' else 'none'}
    sha = 'c' * 64 if fault == 'identity' else bundle.sha256
    for i, day in enumerate(['2026-08-29', '2026-08-30']):
        (records / f'r{i}.json').write_text(json.dumps(_record(f'r{i}', day, sha, anchor_sha, **kwargs)))
    out = tmp_path / 'compat.json'
    if fault == 'identity':
        with pytest.raises(ValueError):
            b.compatibility(bundle_path='ignored', anchor_path=anchor, record_dir=records,
                            regime='preweight', output=out)
        assert not out.exists()
        return
    doc = b.compatibility(bundle_path='ignored', anchor_path=anchor, record_dir=records,
                          regime='preweight', output=out)
    assert doc['all_six_members'] and doc['all_heads_roundtrip'] and doc['pre_result_capture_supported']
    assert doc['same_asof_inputs'] is (fault != 'inputs')
    assert doc['no_postaverage_transform'] is (fault != 'postprocess')
    assert doc['can_adopt'] is False and doc['eligible_for_verdict'] is False
    assert json.loads(out.read_text())['record_days'] == ['2026-08-29', '2026-08-30']
    assert dt.datetime.fromisoformat(doc['created_at']).utcoffset() == dt.timedelta(0)
    with pytest.raises(FileExistsError):
        b.compatibility(bundle_path='ignored', anchor_path=anchor, record_dir=records,
                        regime='preweight', output=out)
