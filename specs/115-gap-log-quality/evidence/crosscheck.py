"""Independent per-race arithmetic against the frozen 113 reference evidence."""
import datetime as dt
import math
import sys
from pathlib import Path

ROOT = Path('/Users/kuwatawaku/workspace/horseracing')
sys.path.insert(0, str(ROOT / 'scripts'))
import gap_log_quality as g


def close(a, b):
    if not math.isclose(a, b, rel_tol=0, abs_tol=1e-12):
        raise AssertionError((a, b))


def main():
    cfg, frozen, cfg113, frozen113 = g.verify()
    registered = g.s.read_json(g.SPEC / 'pre-registration.json')
    assert registered['source_hash'] == g.source_hash() == frozen['source_hash']
    assert registered['config_hash'] == g.s.p.gate_config_hash(cfg) == frozen['config_hash']
    assert registered['source_113_freeze_sha256'] == g.s.p.digest(g.s.WORK / 'run-freeze.json')
    reports, rows = {}, {}
    for c in g.CONTRASTS:
        path = g.SPEC / 'evidence' / f"full-{c['id']}.json"
        assert g.verified_output(path, c, frozen)
        r = reports[c['id']] = g.s.read_json(path)
        rows[c['id']] = {v['race_id']: v for v in g.s.read_json(r['evidence_path'])['rows']}
    old_inc = g.s.read_json(g.s.WORK / 'full-increment-evidence.json')['rows']
    old_anchor = g.s.read_json(g.s.WORK / 'full-anchor-evidence.json')['rows']
    prior_inc = {r['race_id']: r for r in old_inc if r['race_day'] >= cfg['eval_window']['from']}
    prior_anchor = {r['race_id']: r for r in old_anchor if r['race_day'] >= cfg['eval_window']['from']}
    assert set(rows['increment']) == set(rows['anchor']) == set(prior_inc) == set(prior_anchor)
    selected = frozen['sources']['selected_arm']
    selected_field = 'candidate_winner_nll' if selected == 'stack' else 'active_winner_nll'
    for rid, inc in rows['increment'].items():
        total, pi, pa = rows['anchor'][rid], prior_inc[rid], prior_anchor[rid]
        assert inc['race_day'] == total['race_day'] == pi['race_day'] == pa['race_day']
        close(inc['candidate_winner_nll'], total['candidate_winner_nll'])
        close(inc['active_winner_nll'], pi[selected_field])
        close(total['active_winner_nll'], pa['active_winner_nll'])
        close(total['diff'] - inc['diff'], pi[selected_field] - pa['active_winner_nll'])
    n = len(rows['increment'])
    assert n == 22990 == reports['increment']['n_eligible'] == reports['anchor']['n_eligible']
    for name, report in reports.items():
        close(sum(r['diff'] for r in rows[name].values()) / n, report['periods']['all']['diff'])
    summary = g.summarize(frozen)
    result = {
        'artifact_kind': 'gap_log_quality_independent_arithmetic',
        'can_adopt': False, 'eligible_for_verdict': False,
        'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'selected_arm': selected, 'n_eligible': n,
        'run_freeze_sha256': g.s.p.digest(g.WORK / 'run-freeze.json'),
        'method_sha256': g.s.p.digest(__file__),
        'preregistration_sha256': g.s.p.digest(g.SPEC / 'pre-registration.json'),
        'per_race_reference_and_cross_contrast_identity': 'PASS, absolute tolerance 1e-12',
        'research_decision': summary['research_decision'],
        'contrasts': {k: {'report_sha256': g.s.p.digest(g.SPEC / 'evidence' / f'full-{k}.json'),
                          'delta': r['periods']['all']['diff'],
                          'research_state': r['research_disposition']['state']}
                      for k, r in reports.items()},
    }
    g.screen.write_json(g.SPEC / 'evidence' / 'crosscheck.json', result)
    print(result)


if __name__ == '__main__':
    main()
