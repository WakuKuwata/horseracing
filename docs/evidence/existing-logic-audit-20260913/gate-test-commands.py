"""Save exact test logs; every invocation uses DB-free unit tests only."""
import subprocess
from pathlib import Path

ROOT = Path('/Users/kuwatawaku/workspace/horseracing')
OUT = Path(__file__).parent
PYTHON = ROOT / 'training/.venv/bin/python'
groups = {
    'training': (ROOT / 'training', [
        'tests/unit/test_adoption_gate.py', 'tests/unit/test_promotion_gate.py',
        'tests/unit/test_promote_preflight.py']),
    'eval': (ROOT / 'eval', [
        'tests/unit/test_gate.py', 'tests/unit/test_gate_decision.py',
        'tests/unit/test_paired_gate_boundary.py', 'tests/unit/test_paired_regime.py',
        'tests/unit/test_opportunity_set.py', 'tests/unit/test_gate_contract_v3.py']),
    '135': (ROOT, [
        'scripts/tests/test_mixture_reproducibility.py',
        'scripts/tests/test_calibration_serving_recheck135.py',
        'scripts/tests/test_mixture_recent_check_135.py',
        'scripts/tests/test_accuracy_batch_common.py']),
}
for name, (cwd, tests) in groups.items():
    cmd = [str(PYTHON), '-m', 'pytest', '-q', *tests]
    result = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (OUT / f'{name}-tests.log').write_text(f'cwd={cwd}\ncommand={cmd!r}\n{result.stdout}\nexit_code={result.returncode}\n')
    print(name, result.stdout.strip(), 'exit_code=', result.returncode)
    if result.returncode:
        raise SystemExit(result.returncode)
