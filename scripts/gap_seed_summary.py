"""116 paired-seed research summary: average losses, never average predictions."""
from __future__ import annotations

import math
from copy import deepcopy
from pathlib import Path
import statistics

import small_gain_research as research

SEEDS = (42, 43, 44)
CONTRAST_IDS = ('increment', 'anchor')


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Missing or nonfinite numerical evidence')
    return float(value)


def stats(values):
    values = [finite(v) for v in values]
    if len(values) != 3:
        raise ValueError('Exactly three equally weighted seeds required')
    return {'mean': statistics.mean(values), 'sample_sd_descriptive': statistics.stdev(values),
            'min': min(values), 'max': max(values)}


def close(a, b):
    if not math.isclose(finite(a), finite(b), rel_tol=0, abs_tol=1e-12):
        raise ValueError('Paired evidence arithmetic mismatch')


def validate_population(reports, evidence):
    if set(reports) != set(SEEDS) or set(evidence) != set(SEEDS):
        raise ValueError('Missing/extra seed; never average a completed subset')
    reference = None
    race_hash = None
    for seed in SEEDS:
        if set(reports[seed]) != set(CONTRAST_IDS) or set(evidence[seed]) != set(CONTRAST_IDS):
            raise ValueError('Both contrasts required for every seed')
        for name in CONTRAST_IDS:
            report = reports[seed][name]
            rows = evidence[seed][name]['rows']
            identity = [(r['race_id'], r['race_day']) for r in rows]
            if len(identity) != 22990 or len({r[0] for r in identity}) != len(identity):
                raise ValueError('Eligible population count/uniqueness mismatch')
            if (report.get('n_eligible') != len(identity) or report.get('n_races') != 23030
                or len({r[1] for r in identity}) != 715
                or any(not '2020-01-01' <= day <= '2026-08-23' for _, day in identity)):
                raise ValueError('Evaluation window/population mismatch')
            if reference is None:
                reference, race_hash = identity, report.get('race_id_set_hash')
            if identity != reference or not race_hash or report.get('race_id_set_hash') != race_hash:
                raise ValueError('Seed/contrast evaluation populations differ')
            for row in rows:
                close(row['candidate_winner_nll'] - row['active_winner_nll'], row['diff'])
            for role, field in [('candidate', 'candidate_winner_nll'), ('active', 'active_winner_nll'), ('diff', 'diff')]:
                close(statistics.mean(r[field] for r in rows), report['periods']['all'][role])
        for a, b in zip(evidence[seed]['increment']['rows'], evidence[seed]['anchor']['rows'], strict=True):
            close(a['candidate_winner_nll'], b['candidate_winner_nll'])
    return {'eligible_races': 22990, 'all_races': 23030, 'race_days': 715,
            'six_reports_same_ordered_race_id_and_day': True, 'race_id_set_hash': race_hash}


def summarize_reports(reports):
    """Pure policy view after provenance/population checks by the caller."""
    if set(reports) != set(SEEDS) or any(set(reports[s]) != set(CONTRAST_IDS) for s in SEEDS):
        raise ValueError('Exactly three seeds and two contrasts required')
    contrasts = {}
    for name in CONTRAST_IDS:
        seeds, blocked = {}, []
        for seed in SEEDS:
            report = reports[seed][name]
            if report.get('can_adopt') is not False or report.get('eligible_for_verdict') is not False:
                raise ValueError('Source is not explicitly research-only')
            disposition = research.assess_research(report)
            if report.get('research_disposition') != disposition:
                raise ValueError('Source research disposition mismatch')
            if disposition['state'] == 'BLOCKED':
                blocked.append(seed)
            reasons = report['gate']['reasons']
            quality = {k: finite(reasons[k]) for k in ('top2_diff', 'top3_diff', 'cand_ece', 'act_ece')}
            periods = {k: {field: finite(report['periods'][k][field]) for field in ('candidate', 'active', 'diff')}
                       for k in ('all', 'recent_3y', 'recent_5y')}
            for period in periods.values():
                close(period['candidate'] - period['active'], period['diff'])
            groups = report['subgroups']
            seeds[str(seed)] = {'periods': periods, 'quality': quality,
                'research_state': disposition['state'], 'blocking_reasons': disposition['blocking_reasons'],
                'total_ci': report['total_ci'],
                'quality_guards': {k: report['gate'].get(k) for k in ('recent_guard', 'top_noninferior', 'calibration')},
                'recent_guard_evidence': deepcopy(reasons.get('recent')),
                'subgroup_evidence': deepcopy(groups),
                'subgroup_status': groups.get('subgroup_guard_status'),
                'critical_subgroup_states': groups.get('subgroup_decisions'),
                'critical_residual_risk': groups.get('critical_residual_risk'),
                'latest_year': groups.get('race_subgroups', {}).get('recent_year_only'),
                'nk_horses': groups.get('horse_subgroups', {}).get('nk')}
        mean_periods = {p: {field: stats(seeds[str(s)]['periods'][p][field] for s in SEEDS)
                           for field in ('candidate', 'active', 'diff')}
                        for p in ('all', 'recent_3y', 'recent_5y')}
        mean_quality = {k: statistics.mean(seeds[str(s)]['quality'][k] for s in SEEDS)
                        for k in ('top2_diff', 'top3_diff', 'cand_ece', 'act_ece')}
        mean_quality['ece_diff'] = mean_quality['cand_ece'] - mean_quality['act_ece']
        quality_pass = (mean_quality['top2_diff'] <= .0005 and mean_quality['top3_diff'] <= .0005
                        and mean_quality['ece_diff'] <= .001 and mean_quality['cand_ece'] < .05)
        mean_diff = mean_periods['all']['diff']['mean']
        state = ('REVIEW_REQUIRED' if blocked or not quality_pass else
                 'MEAN_IMPROVEMENT' if mean_diff < 0 else 'MEAN_NOT_IMPROVED')
        contrasts[name] = {'state': state, 'seed_results': seeds, 'equal_seed_mean_periods': mean_periods,
            'mean_quality': mean_quality, 'mean_quality_pass': quality_pass,
            'seeds_with_blocked_quality_or_evidence': blocked,
            'negative_seed_count_descriptive_only': sum(seeds[str(s)]['periods']['all']['diff'] < 0 for s in SEEDS)}
    for seed in SEEDS:
        for period in ('all', 'recent_3y', 'recent_5y'):
            close(contrasts['increment']['seed_results'][str(seed)]['periods'][period]['candidate'],
                  contrasts['anchor']['seed_results'][str(seed)]['periods'][period]['candidate'])
    states = [v['state'] for v in contrasts.values()]
    decision = ('REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in states else
                'SEED_MEAN_IMPROVEMENT_RETAINED' if all(s == 'MEAN_IMPROVEMENT' for s in states)
                else 'MEAN_IMPROVEMENT_NOT_REPRODUCED')
    return {'artifact_kind': 'gap_seed_reproducibility_summary', 'can_adopt': False,
        'eligible_for_verdict': False, 'seeds': list(SEEDS), 'deployment_seed': 42,
        'research_decision': decision, 'contrasts': contrasts,
        'limitations': [
            'Equal averages of seed-specific losses/ECE are not metrics of averaged probabilities or an ensemble.',
            'No all-seeds-negative, majority-vote or per-seed significance requirement is added.',
            'No mean CI is claimed. Three-seed sample SD is descriptive, not deployed-seed or ensemble noise.',
            'Per-seed v4 total CIs retain their transferred noise assumption; gamma/selection uncertainty is not fully covered.',
            'Deployment seed42 retains its own recent-year and subgroup uncertainty; seed averaging does not resolve it.',
            'Historical full-information research only; no prospective confirmation or production activation.']}


def main():
    import gap_seed_recheck as driver
    cfg, frozen = driver.verify()
    if (driver.WORK / 'running.lock').exists() or not all(driver.completed(j) for j in frozen['jobs']):
        raise ValueError('All thirty valid fitting receipts and stopped workers required')
    reports, evidence, hashes = {}, {}, {}
    for seed in SEEDS:
        reports[seed], evidence[seed], hashes[str(seed)] = {}, {}, {}
        for contrast in driver.CONTRASTS:
            name = contrast['id']
            if not driver.verified_result(seed, contrast, cfg, frozen):
                raise ValueError('All six verified reports are required')
            path = driver.result_path(seed, name)
            report = reports[seed][name] = driver.s.read_json(path)
            evidence[seed][name] = driver.s.read_json(report['evidence_path'])
            hashes[str(seed)][name] = {'report': str(path), 'sha256': driver.s.p.digest(path),
                                     'evidence_sha256': driver.s.p.digest(report['evidence_path'])}
    population = validate_population(reports, evidence)
    result = summarize_reports(reports)
    result.update(population=population, sources=hashes,
        config_hash=driver.s.p.gate_config_hash(cfg),
        run_freeze_sha256=driver.s.p.digest(driver.WORK / 'run-freeze.json'),
        summarizer_sha256=driver.s.p.digest(__file__))
    driver.verify()
    path = driver.SPEC / 'verdict.json'
    if path.exists():
        if driver.s.read_json(path) != result:
            raise ValueError('Preserve changed existing summary for diagnosis')
    else:
        driver.g.screen.write_json(path, result)
    print({'research_decision': result['research_decision'], 'means': {
        k: v['equal_seed_mean_periods']['all']['diff']['mean'] for k, v in result['contrasts'].items()}})


if __name__ == '__main__':
    main()
