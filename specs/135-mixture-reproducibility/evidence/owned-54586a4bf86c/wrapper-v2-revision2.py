"""Owned135 isolated-source execution; data routes remain explicit original paths.

Run the byte-identical copy in execution-environment-54586a4bf86c/scripts.
Only source/data routing changes. No model fit or statistical choice is added.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

DATA_ROOT = Path('/Users/kuwatawaku/workspace/horseracing')
TOKEN = '54586a4bf86c'
SOURCE_ROOT = DATA_ROOT/'artifacts/135-mixture-reproducibility'/('execution-environment-'+TOKEN)
WORK = DATA_ROOT/'artifacts/135-mixture-reproducibility'/('recent-owned-'+TOKEN)
EVIDENCE = DATA_ROOT/'specs/135-mixture-reproducibility/evidence'/('owned-'+TOKEN)
CORE_MODULE = 'mixture_recent_owned_135_'+TOKEN
CORE_SHA = '481777c4f5233972bbc27b54054af30e914bc66c8736eaf19d77ad5c58aa44ec'
PACKAGES = ('db','features','training','eval','probability','serving')
AUDITORS = ('audit_mixture_recent_inputs_135.py','audit_mixture_recent_135.py',
            'audit_mixture_reproducibility_135.py')
IDENTITY_HELPER = 'owned_source_identity_135_54586a4bf86c.py'


def configure_sources():
    roots = [SOURCE_ROOT/'scripts', *[SOURCE_ROOT/p/'src' for p in PACKAGES]]
    if not all(path.is_dir() for path in roots):
        raise ValueError('Isolated source tree missing')
    sys.path[:0] = [str(path) for path in roots]
    core_path = SOURCE_ROOT/'scripts'/(CORE_MODULE+'.py')
    if hashlib.sha256(core_path.read_bytes()).hexdigest() != CORE_SHA:
        raise ValueError('Known isolated core changed')


def verify_module_origins():
    for name, module in list(sys.modules.items()):
        path = getattr(module,'__file__',None)
        if not path:
            continue
        path = Path(path).resolve()
        local_package = name.startswith('horseracing_')
        local_script = path.parent == DATA_ROOT/'scripts'
        if (local_package or local_script) and not path.is_relative_to(SOURCE_ROOT):
            raise ValueError('Shared source entered isolated runtime: '+name)


def bind():
    configure_sources()
    import importlib
    core = importlib.import_module(CORE_MODULE)
    if core.ROOT != SOURCE_ROOT:
        raise ValueError('Core source ROOT must remain isolated')
    core.WORK = WORK
    core.SPEC = DATA_ROOT/'specs/135-mixture-reproducibility'
    core.REPRO = DATA_ROOT/'artifacts/135-mixture-reproducibility/repro'
    core.OLD = DATA_ROOT/'artifacts/model_versions/lgbm-094-cap900'
    core.BUNDLE = DATA_ROOT/'artifacts/129-candidate-mixture-serving/bundle.json'
    core.common.SNAPSHOT = DATA_ROOT/'artifacts/111-ability-observation/snapshot.pkl'
    core.common.SOURCE = DATA_ROOT/'artifacts/130-mixture-preweight-walkforward'
    core.common.WORK = DATA_ROOT/'artifacts/132-accuracy-first-batch'
    original = core.source_files
    def owned_sources():
        files = original()
        names = [Path(__file__).name,IDENTITY_HELPER,*AUDITORS]
        for name in names:
            path = SOURCE_ROOT/'scripts'/name
            files[str(path)] = core.digest(path)
        # Native126/125/114 drivers are imported transitively by model profile
        # verification; freeze those actual isolated script bytes as well.
        for module in list(sys.modules.values()):
            name = getattr(module,'__file__',None)
            if name:
                path = Path(name).resolve()
                if path.parent == SOURCE_ROOT/'scripts' and path.suffix == '.py':
                    files[str(path)] = core.digest(path)
        return files
    core.source_files = owned_sources
    verify_module_origins()
    return core


def bind_audit(phase):
    configure_sources()
    if phase == 'audit-scores':
        import audit_mixture_recent_135 as audit
        audit.WORK, audit.OUT = WORK, EVIDENCE/'recent-numeric-review.json'
        # ROOT in this numeric auditor is used only for the experiment JSON.
        audit.ROOT = DATA_ROOT
        audit.audit.WORK = DATA_ROOT/'artifacts/135-mixture-reproducibility/repro'
    else:
        import audit_mixture_recent_inputs_135 as audit
        audit.WORK = WORK
        audit.SPEC = DATA_ROOT/'specs/135-mixture-reproducibility'
        audit.REPRO = DATA_ROOT/'artifacts/135-mixture-reproducibility/repro'
        audit.OLD = DATA_ROOT/'artifacts/model_versions/lgbm-094-cap900'
        audit.BUNDLE = DATA_ROOT/'artifacts/129-candidate-mixture-serving/bundle.json'
        audit.output_path = lambda which:EVIDENCE/('recent-'+which+'-independent-review.json')
    verify_module_origins()
    return audit


def execute(phase):
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    if phase.startswith('audit-'):
        audit = bind_audit(phase)
        audit.run() if phase == 'audit-scores' else audit.run(phase.removeprefix('audit-'))
    else:
        core = bind()
        if phase == 'preflight':
            core.gate()
            bundle,candidate,conversion,lambdas,files = core.load_models()
            verify_module_origins()
            print(json.dumps({'status':'PREFLIGHT_PASS','source_root':str(SOURCE_ROOT),
                'data_root':str(DATA_ROOT),'core_source':core.__file__,'n_members':len(bundle.members),
                'candidate_seed':candidate.metadata['seed'],'candidate_train_through':candidate.metadata['train_through'],
                'n_te_columns':len(conversion),'model_files_checked':len(files),
                'database_connected':False,'recent_values_read':False,'recent_results_read':False}),flush=True)
        else:
            result = getattr(core,phase)()
            verify_module_origins()
            print(json.dumps(result,allow_nan=False),flush=True)


def main():
    if Path(__file__).resolve().parent != SOURCE_ROOT/'scripts':
        raise ValueError('Execute the isolated wrapper copy')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('preflight','capture','predict','score',
                                        'audit-inputs','audit-predictions','audit-scores'))
    phase = parser.parse_args().phase
    configure_sources()
    from owned_source_identity_135_54586a4bf86c import source_identity_context
    with source_identity_context(SOURCE_ROOT,DATA_ROOT):
        execute(phase)


if __name__ == '__main__':
    main()
