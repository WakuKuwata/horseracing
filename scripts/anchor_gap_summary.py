"""118 paired three-seed summary: retention and research preference are separate."""
from __future__ import annotations

from copy import deepcopy
import statistics

import gap_seed_summary as prior
import small_gain_research as research

SEEDS = (42, 43, 44)
CONTRASTS = ('anchor', 'retained')
PERIODS = ('all', 'recent_3y', 'recent_5y')
finite, close, stats = prior.finite, prior.close, prior.stats


def scope(mapping):
    if set(mapping) != set(SEEDS) or any(set(mapping[s]) != set(CONTRASTS) for s in SEEDS):
        raise ValueError('Exactly three seeds and both registered contrasts required')


def quality_pass(q):
    return (q['top2_diff'] <= .0005 and q['top3_diff'] <= .0005
            and q['cand_ece'] - q['act_ece'] <= .001 and q['cand_ece'] < .05)


def summarize_reports(reports):
    scope(reports)
    contrasts = {}
    for name in CONTRASTS:
        seeds, blocked = {}, []
        for seed in SEEDS:
            r = reports[seed][name]
            if r.get('can_adopt') is not False or r.get('eligible_for_verdict') is not False:
                raise ValueError('Explicit research-only reports required')
            if any(type(r.get('gate', {}).get(k)) is not bool for k in ('recent_guard', 'top_noninferior', 'calibration')):
                raise ValueError('Quality flags must be explicit booleans')
            critical = r.get('subgroups', {}).get('critical')
            if (not isinstance(critical, list) or len(critical) != 3
                or set(critical) != {'canonical', 'nk', 'recent_year_only'}):
                raise ValueError('All fixed critical subgroups must be declared')
            disposition = research.assess_research(r)
            if r.get('research_disposition') != disposition:
                raise ValueError('Disposition mismatch')
            # Failed numeric quality is reviewable; incomplete evidence is not a valid average.
            invalid = [why for why in disposition['blocking_reasons'] if not why.startswith(
                ('quality_guard_not_passed:', 'critical_subgroup_fail:', 'subgroup_guard_fail'))]
            if invalid:
                raise ValueError(f'Invalid/incomplete research evidence: {invalid}')
            q = {k: finite(r['gate']['reasons'][k]) for k in ('top2_diff', 'top3_diff', 'cand_ece', 'act_ece')}
            periods = {p: {k: finite(r['periods'][p][k]) for k in ('candidate', 'active', 'diff')} for p in PERIODS}
            for v in periods.values():
                close(v['candidate'] - v['active'], v['diff'])
            if disposition['state'] == 'BLOCKED' or not quality_pass(q):
                blocked.append(seed)
            seeds[str(seed)] = {'periods': periods, 'quality': q,
                'research_state': disposition['state'], 'blocking_reasons': disposition['blocking_reasons'],
                'numeric_quality_pass': quality_pass(q), 'total_ci': deepcopy(r['total_ci']),
                'quality_guards': {k: r['gate'].get(k) for k in ('recent_guard', 'top_noninferior', 'calibration')},
                'recent_guard_evidence': deepcopy(r['gate']['reasons'].get('recent')),
                'subgroup_evidence': deepcopy(r['subgroups'])}
        means = {p: {k: stats(seeds[str(s)]['periods'][p][k] for s in SEEDS) for k in ('candidate', 'active', 'diff')} for p in PERIODS}
        mq = {k: statistics.mean(seeds[str(s)]['quality'][k] for s in SEEDS) for k in ('top2_diff', 'top3_diff', 'cand_ece', 'act_ece')}
        mq['ece_diff'] = mq['cand_ece'] - mq['act_ece']
        state = ('REVIEW_REQUIRED' if blocked or not quality_pass(mq) else
                 'MEAN_IMPROVEMENT' if means['all']['diff']['mean'] < 0 else 'MEAN_NOT_IMPROVED')
        contrasts[name] = {'state': state, 'seed_results': seeds, 'equal_seed_mean_periods': means,
            'mean_quality': mq, 'mean_quality_pass': quality_pass(mq), 'blocked_seeds': blocked,
            'negative_seed_count_descriptive_only': sum(seeds[str(s)]['periods']['all']['diff'] < 0 for s in SEEDS)}
    for seed in SEEDS:
        for p in PERIODS:
            close(contrasts['anchor']['seed_results'][str(seed)]['periods'][p]['candidate'],
                  contrasts['retained']['seed_results'][str(seed)]['periods'][p]['candidate'])
    a, b = (contrasts[k]['state'] for k in CONTRASTS)
    if 'REVIEW_REQUIRED' in (a, b):
        decision, retention, preferred = 'REVIEW_REQUIRED', 'REVIEW_REQUIRED', 'UNCHANGED_PENDING_REVIEW'
    elif a != 'MEAN_IMPROVEMENT':
        decision, retention, preferred = 'ANCHOR_GAP_DEFERRED', 'DEFER', 'PRUNING_GAP'
    elif b == 'MEAN_IMPROVEMENT':
        decision, retention, preferred = 'ANCHOR_GAP_PREFERRED', 'RETAIN', 'ANCHOR_GAP'
    else:
        decision, retention, preferred = 'ANCHOR_GAP_ALTERNATIVE_RETAINED', 'RETAIN', 'PRUNING_GAP'
    return {'artifact_kind': 'anchor_gap_three_seed_research_summary', 'can_adopt': False,
        'eligible_for_verdict': False, 'seeds': list(SEEDS), 'deployment_seed': 42,
        'research_decision': decision, 'candidate_retention': retention,
        'preferred_research_configuration': preferred, 'upstream116_verdict_modified': False,
        'contrasts': contrasts, 'limitations': [
            'Average losses are not metrics of averaged probabilities or an ensemble.',
            'No all-seeds-negative, majority or per-seed-significance condition is added.',
            'No average CI is claimed; three-seed SD is descriptive, not deployed-seed noise.',
            'Individual v4 CIs retain k_seeds=1 and transferred fold noise; uncertainty in both fitted gamma sequences and post117 selection is not fully covered.',
            'Diagnostic groups do not define extra adoption gates or group-specific model rules.',
            'Historical research only; no unused-period confirmation, slot reservation or production activation.']}


def validate_population(reports, evidence, legacy):
    """Both new baselines must exactly reproduce the corresponding 116 race losses."""
    scope(reports)
    scope(evidence)
    if set(legacy) != set(SEEDS):
        raise ValueError('All three verified legacy baselines required')
    reference = None
    race_hash = None
    for seed in SEEDS:
        old = legacy[seed]['rows']
        old_identity = [(r['race_id'], r['race_day']) for r in old]
        for name in CONTRASTS:
            r = reports[seed][name]
            rows = evidence[seed][name]['rows']
            identity = [(x['race_id'], x['race_day']) for x in rows]
            if (len(identity) != 22990 or len({x[0] for x in identity}) != 22990
                or len({x[1] for x in identity}) != 715 or r['n_races'] != 23030
                or r['n_eligible'] != 22990 or identity != old_identity
                or any(not '2020-01-01' <= day <= '2026-08-23' for _, day in identity)):
                raise ValueError('Evaluation population/count/date mismatch')
            if reference is None:
                reference, race_hash = identity, r['race_id_set_hash']
            if identity != reference or not race_hash or r['race_id_set_hash'] != race_hash:
                raise ValueError('Seed/contrast populations differ')
            for x, original in zip(rows, old, strict=True):
                close(x['candidate_winner_nll'] - x['active_winner_nll'], x['diff'])
                field = 'active_winner_nll' if name == 'anchor' else 'candidate_winner_nll'
                if finite(x['active_winner_nll']) != finite(original[field]):
                    raise ValueError('Baseline does not exactly replay original116 loss')
            for role, field in [('candidate', 'candidate_winner_nll'), ('active', 'active_winner_nll'), ('diff', 'diff')]:
                close(statistics.mean(x[field] for x in rows), r['periods']['all'][role])
        for x, y in zip(evidence[seed]['anchor']['rows'], evidence[seed]['retained']['rows'], strict=True):
            if finite(x['candidate_winner_nll']) != finite(y['candidate_winner_nll']):
                raise ValueError('Candidate differs between contrasts')
    return {'n_eligible': 22990, 'n_races': 23030, 'n_days': 715, 'race_id_set_hash': race_hash,
            'all_new_baseline_losses_equal_original116': True, 'all_candidate_losses_equal_across_contrasts': True}


def fixed_diagnostics(evidence, attributes):
    scope(evidence)
    reference = evidence[42]['anchor']['rows']
    if (len(attributes) != 22990 or len({x['race_id'] for x in attributes}) != 22990
        or [(x['race_id'], x['race_day']) for x in attributes] != [(x['race_id'], x['race_day']) for x in reference]):
        raise ValueError('117 diagnostic attributes do not match scored population')
    groups = {'2026_all': lambda a: a['year'] == 2026,
              '2026_nakayama': lambda a: a['year'] == 2026 and a['venue'] == '06',
              '2026_partial_relative': lambda a: a['year'] == 2026 and a['relative_coverage'] == '(.5,1)'}
    expected = {'2026_all': (2296, 70), '2026_nakayama': (300, 25), '2026_partial_relative': (1538, 70)}
    result = {}
    for name, select in groups.items():
        indices = [i for i, a in enumerate(attributes) if select(a)]
        n_days = len({attributes[i]['race_day'] for i in indices})
        if (len(indices), n_days) != expected[name]:
            raise ValueError('Fixed117 diagnostic group membership differs')
        values = {}
        for contrast in CONTRASTS:
            by_seed = {str(s): {k: statistics.mean(finite(evidence[s][contrast]['rows'][i][field]) for i in indices)
                for k, field in [('candidate', 'candidate_winner_nll'), ('active', 'active_winner_nll'), ('diff', 'diff')]}
                for s in SEEDS}
            values[contrast] = {'by_seed': by_seed, 'equal_seed_means': {k: stats(by_seed[str(s)][k] for s in SEEDS) for k in ('candidate', 'active', 'diff')}}
        result[name] = {'n_races': len(indices), 'n_days': n_days, 'contrasts': values}
    return {'source': 'frozen117 race attributes', 'new_ci': False, 'extra_quality_gate': False, 'groups': result}


def main():
    import anchor_gap_recheck as driver
    cfg, frozen = driver.verify()
    if (driver.WORK / 'running.lock').exists() or not all(driver.completed(j) for j in frozen['jobs']):
        raise ValueError('Both valid warmup receipts and stopped workers required')
    reports, evidence, hashes, legacy = {}, {}, {}, {}
    c116, f116 = driver.d.verify()
    for seed in SEEDS:
        reports[seed], evidence[seed], hashes[str(seed)] = {}, {}, {}
        old_contrast = next(c for c in driver.d.CONTRASTS if c['id'] == 'anchor')
        if not driver.d.verified_result(seed, old_contrast, c116, f116):
            raise ValueError('Original116 baseline missing')
        old_report = driver.s.read_json(driver.d.result_path(seed, 'anchor'))
        legacy[seed] = driver.s.read_json(old_report['evidence_path'])
        for contrast in driver.CONTRASTS:
            name = contrast['id']
            if not driver.verified_result(seed, contrast, cfg, frozen):
                raise ValueError('All six verified new reports required')
            path = driver.result_path(seed, name)
            report = reports[seed][name] = driver.s.read_json(path)
            evidence[seed][name] = driver.s.read_json(report['evidence_path'])
            hashes[str(seed)][name] = {'report_sha256': driver.s.p.digest(path), 'evidence_sha256': driver.s.p.digest(report['evidence_path'])}
    population = validate_population(reports, evidence, legacy)
    result = summarize_reports(reports)
    attrs_path = driver.ROOT / 'artifacts/117-pruning-2026-diagnostic/race-diagnostic.json'
    attrs = driver.s.read_json(attrs_path)['rows']
    result.update(population=population, sources=hashes, diagnostics=fixed_diagnostics(evidence, attrs),
        attributes117_sha256=driver.s.p.digest(attrs_path), run_freeze_sha256=driver.s.p.digest(driver.WORK / 'run-freeze.json'),
        summarizer_sha256=driver.s.p.digest(__file__), config_hash=driver.s.p.gate_config_hash(cfg))
    driver.verify()
    path = driver.SPEC / 'verdict.json'
    if path.exists():
        if driver.s.read_json(path) != result:
            raise ValueError('Preserve changed existing summary')
    else:
        driver.g.screen.write_json(path, result)
    print({'research_decision': result['research_decision'], 'candidate_retention': result['candidate_retention'],
           'preferred_research_configuration': result['preferred_research_configuration'],
           'means': {k: v['equal_seed_mean_periods']['all']['diff']['mean'] for k, v in result['contrasts'].items()}})


if __name__ == '__main__':
    main()
