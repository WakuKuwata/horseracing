"""131: register the 129 six-member bundle as a model_versions row (candidate, never active here).

The bundle is copied into ``artifacts/model_versions/<name>/`` so the row's artifacts are
self-contained (a row must not outlive its files). Every member file is SHA-verified after the
copy and the copy is loaded through the SAME serving loader production will use before the row
is written. Promotion is a separate, recorded step (``training promote-model``).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from horseracing_db.models import ModelVersion
from horseracing_db.session import create_db_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'serving/src'))
from horseracing_serving import mixture_model, mixture_serving  # noqa: E402
from horseracing_serving.model_loader import load_serving_model  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def git_sha():
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def copy_bundle(source_manifest: Path, dest: Path):
    """Copy manifest + member dirs; verify every file hash against the manifest after copying."""
    bundle = mixture_model.load_mixture_bundle(source_manifest)
    if dest.exists():
        raise FileExistsError(f'{dest} exists; never overwrite a model_versions artifact')
    staged = dest.with_name(dest.name + '.partial')
    if staged.exists():
        raise FileExistsError(f'{staged} exists; inspect before retry')
    staged.mkdir(parents=True)
    try:
        shutil.copy2(source_manifest, staged / 'bundle.json')
        for item in bundle.manifest['members']:
            src_dir = source_manifest.parent / item['artifact_dir']
            dst_dir = staged / item['artifact_dir']
            dst_dir.mkdir(parents=True)
            for name, expected in item['files'].items():
                shutil.copy2(src_dir / name, dst_dir / name)
                if sha256(dst_dir / name) != expected:
                    raise ValueError(f'Copied member file hash differs: {dst_dir / name}')
        if sha256(staged / 'bundle.json') != bundle.sha256:
            raise ValueError('Copied manifest hash differs')
        mixture_model.load_mixture_bundle(staged / 'bundle.json', expected_sha256=bundle.sha256)
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    staged.rename(dest)
    return bundle


def write_metadata(dest: Path, bundle, *, model_version, evidence):
    members = bundle.manifest['members']
    coef_years = {int(m['correction']['valid_year']) for m in members}
    if len(coef_years) != 1:
        raise ValueError('Members disagree on coefficient year')
    meta = {
        'artifact_kind': mixture_serving.ARTIFACT_KIND, 'model_version': model_version,
        'model_family': 'lightgbm_mixture', 'bundle_id': bundle.manifest['bundle_id'],
        'bundle_sha256': bundle.sha256, 'profile': bundle.manifest['profile'],
        'feature_version': 'features-021', 'feature_hash': mixture_model.FULL_HASH,
        'objective': 'mixture', 'postprocess': 'six_member_corrected_mean_win;harville_stage_discount_display',
        'members': [{'id': m['id'], 'branch': m['branch'], 'seed': m['seed'], 'weight': m['weight'],
                     'n_columns': 125 if m['branch'] == 'pruning' else 138,
                     'correction_terms': m['correction']['terms']} for m in members],
        'coefficient_valid_year': coef_years.pop(),
        'coefficient_train_through': bundle.manifest['coefficient_train_through'],
        'coefficient_grace_years': mixture_serving.COEFFICIENT_GRACE_YEARS,
        'train_through': bundle.manifest['train_through'],
        'training_population': bundle.manifest['sources'].get('training_population'),
        'model_degenerate': False, 'calibrator_degenerate': False,
        'calibration': 'per_member_isotonic_strict_past_oof;mean_of_corrected_members',
        'race_class_representation': 'raw', 'weight_mask': {'rate': .5, 'seed': 20260810, 'unit': 'race'},
        'evidence': evidence, 'source_bundle_sources': bundle.manifest['sources'],
        'registered_by': 'scripts/register_mixture_model_version.py', 'git_sha': git_sha(),
        'registered_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    (dest / 'metadata.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    return meta


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-version', required=True)
    p.add_argument('--bundle', type=Path, default=ROOT / 'artifacts/129-candidate-mixture-serving/bundle.json')
    p.add_argument('--display-name', required=True)
    p.add_argument('--purpose', required=True)
    p.add_argument('--evidence', type=Path, action='append', default=[],
                   help='evidence files whose SHA is recorded (130 summary, 129 result review, ...)')
    p.add_argument('--apply', action='store_true', help='write the DB row (default: artifacts only, no row)')
    p.add_argument('--database-url', default=None)
    args = p.parse_args(argv)
    dest = (ROOT / 'artifacts/model_versions' / args.model_version).resolve()
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        if session.get(ModelVersion, args.model_version) is not None:
            raise SystemExit(f'model_versions row {args.model_version!r} already exists')
    evidence = {str(path.resolve()): sha256(path) for path in args.evidence}
    bundle = copy_bundle(args.bundle.resolve(), dest)
    meta = write_metadata(dest, bundle, model_version=args.model_version, evidence=evidence)
    weights = str(dest / 'bundle.json')
    # Load through production's loader BEFORE the row exists: a row that cannot be served is a defect.
    from types import SimpleNamespace

    probe = SimpleNamespace(model_version=args.model_version, weights_uri=weights, calibrator_uri=weights)
    model = load_serving_model(SimpleNamespace(get=lambda cls, key: probe if key == args.model_version else None),
                               args.model_version)
    if not mixture_serving.is_mixture(model) or model.bundle.sha256 != bundle.sha256:
        raise SystemExit('Serving loader did not return the registered mixture')
    print(json.dumps({'artifact_dir': str(dest), 'bundle_sha256': bundle.sha256,
                      'feature_cols': len(model.feature_cols), 'coefficient_year': model.coefficient_year}))
    if not args.apply:
        print('DRY RUN: artifacts written, no DB row (pass --apply)')
        return 0
    summary = {
        'registration': {'cause': 'feature_131_mixture_candidate', 'bundle_sha256': bundle.sha256,
                         'evidence': evidence, 'registered_at': meta['registered_at'], 'git_sha': meta['git_sha']},
        'eval': {
            'contract': 'research_125_and_130_walkforward_vs_production_recipe',
            'winner_nll_diff_full_information_2020_2026': -0.009245463,
            'winner_nll_diff_preweight_2020_2026': -0.008969092,
            'preweight_total_ci_98_75': [-0.013973, -0.003969],
            'n_races': 22990, 'n_days': 715,
            'rehearsal_4days_vs_lgbm_094_cap900_preweight': 0.001507,
            'note': 'Retrospective walk-forward (130); no prospective confirmation at registration.',
        },
        'training': {'train_through': meta['train_through'], 'n_model_rows': (meta.get('training_population') or {}).get('n_train_rows'),
                     'members': 6, 'coefficient_valid_year': meta['coefficient_valid_year']},
    }
    with Session(engine) as session:
        row = ModelVersion(model_version=args.model_version, model_family=meta['model_family'],
                           feature_version='features-021', adoption_status='candidate',
                           metrics_summary=summary, display_name=args.display_name, purpose=args.purpose,
                           weights_uri=weights, calibrator_uri=weights)
        session.add(row)
        session.commit()
    print(f'REGISTERED {args.model_version} as candidate')
    return 0


if __name__ == '__main__':
    os.environ.setdefault('PYTHONHASHSEED', '0')
    raise SystemExit(main())
