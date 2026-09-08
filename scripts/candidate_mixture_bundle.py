"""129: assemble the six-member shadow bundle and its serving-compatibility evidence.

Separate from ``candidate_mixture_build`` on purpose: the build freeze pins that file's
digest, so bundle assembly must not edit it. Assembly only reads completed member
receipts and the frozen 125/118 coefficient files; it fits nothing and writes no DB row.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys

import candidate_mixture_build as build
import joint_residual_stack as research

ROOT = build.ROOT
WORK = build.WORK
SPEC = build.SPEC
BUNDLE_ID = '129-125_new_joint_mixed6_v1'
sys.path.insert(0, str(ROOT / 'serving/src'))
from horseracing_serving import mixture_correction, mixture_model, mixture_shadow  # noqa: E402

digest = build.digest
read_json = build.read_json


def frozen_coefficients():
    """2026 coefficient vectors exactly as the retained research saved them."""
    frozen125 = read_json(research.WORK / 'run-freeze.json')
    frozen118 = read_json(research.a.WORK / 'run-freeze.json')
    out = {}
    for seed in (42, 43, 44):
        joint = research.verified_coefficients(seed, frozen125)
        path = research.seed_area(seed) / 'coefficients.json'
        out[f'joint-{seed}'] = {'terms': list(mixture_model.JOINT_TERMS),
                                'coefficients': [float(v) for v in joint['gammas']['2026']],
                                'source_study': 125, 'source_path': str(path), 'source_sha256': digest(path),
                                'valid_year': 2026, 'fit_through': '2025-12-31'}
        anchor = research.a.verified_coefficients(seed, frozen118)
        path = research.a.seed_area(seed) / 'coefficients.json'
        out[f'anchor-{seed}'] = {'terms': ['gap_log'], 'coefficients': [float(anchor['gammas']['2026'])],
                                 'source_study': 118, 'source_path': str(path), 'source_sha256': digest(path),
                                 'valid_year': 2026, 'fit_through': '2025-12-31'}
    for item in out.values():
        if len(item['coefficients']) != len(item['terms']):
            raise ValueError('Coefficient/term length differs')
    return out


def assemble(output=None):
    output = Path(output or WORK / 'bundle.json')
    if output.exists():
        raise FileExistsError('Bundle already assembled; never overwrite')
    members = build.verify_members(False)
    coefficients = frozen_coefficients()
    frozen = read_json(build.freeze_path())
    parity = SPEC / 'evidence/annual-serving-parity.json'
    manifest = {
        'schema_version': 1, 'artifact_kind': 'candidate_mixture_bundle',
        'profile': mixture_model.PROFILE, 'mode': 'shadow', 'can_adopt': False,
        'eligible_for_verdict': False, 'bundle_id': BUNDLE_ID,
        'train_through': str(build.CUTOFF), 'coefficient_train_through': '2025-12-31',
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'members': [{'id': m['member']['id'], 'branch': m['member']['branch'], 'seed': m['member']['seed'],
                     'weight': 1 / 6, 'artifact_dir': 'members/' + m['member']['id'], 'files': m['files'],
                     'receipt_sha256': m['receipt_sha256'], 'correction': coefficients[m['member']['id']]}
                    for m in members],
        'feature_profile': mixture_model.feature_profile(), 'inference': dict(mixture_model.INFERENCE),
        'sources': {'research125_run_freeze_sha256': digest(research.WORK / 'run-freeze.json'),
                    'research118_run_freeze_sha256': digest(research.a.WORK / 'run-freeze.json'),
                    'build_run_freeze_sha256': digest(build.freeze_path()),
                    'build_source_hash': frozen['source_hash'], 'build_config_hash': frozen['config_hash'],
                    'annual_serving_parity_sha256': digest(parity) if parity.exists() else None,
                    'training_population': {k: frozen['population'][k] for k in
                                            ('n_train_races', 'n_train_rows', 'train_from', 'train_through', 'train_hash')},
                    'serving_code': {name: digest(Path(module.__file__)) for name, module in
                                     (('mixture_model', mixture_model), ('mixture_shadow', mixture_shadow),
                                      ('mixture_correction', mixture_correction))},
                    'runtime': build.runtime()},
    }
    for path in ('members/' + m['member']['id'] for m in members):
        if not (WORK / path).is_dir():
            raise ValueError('Member directory outside bundle root: ' + path)
    mixture_model.validate_manifest(manifest)
    staged = output.with_name(output.name + '.partial')
    if staged.exists():
        raise FileExistsError('Stale partial bundle; inspect before retry')
    build.write_json(staged, manifest)
    try:
        mixture_model.load_mixture_bundle(staged)  # full member validation before the name is claimed
    except Exception:
        staged.unlink()
        raise
    staged.rename(output)
    bundle = mixture_model.load_mixture_bundle(output)
    print(json.dumps({'bundle': str(output), 'sha256': bundle.sha256, 'members': [m.id for m in bundle.members]}))
    return bundle


def compatibility(*, bundle_path, anchor_path, record_dir, regime, output):
    """Operational compatibility evidence for the confirmation preflight.

    Every boolean is derived from saved artifacts: member completeness and roundtrip
    parity from the build receipts, shared as-of inputs and no post-average transform
    from the rehearsal records, and pre-result capture support from the provenance
    validator itself.
    """
    output = Path(output)
    if output.exists():
        raise FileExistsError('Compatibility evidence exists; never overwrite')
    bundle = mixture_model.load_mixture_bundle(bundle_path)
    anchor_sha = mixture_shadow.sha256(anchor_path)
    receipts = {m['id']: read_json(build.receipt_path(m['id'])) for m in build.MEMBERS}
    all_heads_roundtrip = all(r['parity']['status'] == 'PASS' and r['parity']['max_abs_diff'] == 0.
                              and r['parity']['n_head_values'] > 0 for r in receipts.values())
    records = [mixture_shadow.read_json(p) for p in sorted(Path(record_dir).glob('*.json'))]
    if not records:
        raise ValueError('No rehearsal records to certify against')
    for r in records:
        if (r.get('bundle_manifest_sha256') != bundle.sha256 or r.get('anchor_model_sha256') != anchor_sha
                or r.get('primary_regime') != regime or r.get('classification') != 'rehearsal'):
            raise ValueError('Record identity/regime differs from the certified pair')
    same_inputs = all(r['candidate_input_sha256'] == r['anchor_input_sha256']
                      and r['candidate_started_ids'] == r['anchor_started_ids'] for r in records)
    no_post = all(r['candidate_audit'].get('postprocess') == 'none'
                  and r['candidate_audit'].get('head_aggregation') == 'arithmetic_mean' for r in records)
    now = mixture_shadow.now_utc()
    start = now + dt.timedelta(hours=1)
    try:
        mixture_shadow.validate_capture('prospective', start.astimezone(mixture_shadow.JST).date(),
                                        start, 0, now, now)
        pre_result = True
    except ValueError:
        pre_result = False
    doc = {'artifact_kind': 'mixture_serving_compatibility', 'bundle_manifest_sha256': bundle.sha256,
           'anchor_model_sha256': anchor_sha, 'primary_regime': regime,
           'all_six_members': len(bundle.members) == 6, 'all_heads_roundtrip': all_heads_roundtrip,
           'same_asof_inputs': same_inputs, 'no_postaverage_transform': no_post,
           'pre_result_capture_supported': pre_result,
           'n_rehearsal_records': len(records), 'record_days': sorted({r['race_day'] for r in records}),
           'record_sha256': {r['race_id']: mixture_shadow.sha256(p) for r, p in
                             zip(records, sorted(Path(record_dir).glob('*.json')), strict=True)},
           'receipt_sha256': {k: digest(build.receipt_path(k)) for k in receipts},
           'latency_seconds': {'candidate_median': sorted(r['seconds']['candidate'] for r in records)[len(records) // 2],
                               'anchor_median': sorted(r['seconds']['anchor'] for r in records)[len(records) // 2],
                               'feature_build_day_median': sorted(r['seconds']['feature_build_day'] for r in records)[len(records) // 2]},
           'created_at': now.isoformat(), 'can_adopt': False, 'eligible_for_verdict': False}
    build.write_json(output, doc)
    print(json.dumps({k: doc[k] for k in ('all_six_members', 'all_heads_roundtrip', 'same_asof_inputs',
                                          'no_postaverage_transform', 'pre_result_capture_supported', 'n_rehearsal_records')}))
    return doc


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('assemble')
    a.add_argument('--output', type=Path, default=None)
    c = sub.add_parser('compatibility')
    c.add_argument('--bundle', type=Path, required=True)
    c.add_argument('--anchor', type=Path, required=True)
    c.add_argument('--record-dir', type=Path, required=True)
    c.add_argument('--regime', default='preweight')
    c.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    if args.command == 'assemble':
        assemble(args.output)
    else:
        compatibility(bundle_path=args.bundle, anchor_path=args.anchor, record_dir=args.record_dir,
                      regime=args.regime, output=args.output)


if __name__ == '__main__':
    main()
