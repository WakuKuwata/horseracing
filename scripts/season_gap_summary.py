"""119 incremental seasonal retention; equal seed losses, never a probability ensemble."""
from __future__ import annotations
import statistics
import anchor_gap_summary as checks

SEEDS = checks.SEEDS
CONTRASTS = checks.CONTRASTS
GROUPS = ('no_female', 'mixed', 'all_female', 'missing_sex')
finite = checks.finite


def summarize_reports(reports):
    # Preserve the proven strict finite/quality/subgroup checks, but explicitly
    # replace118's alternative-retention rule with119's both-comparisons rule.
    result = checks.summarize_reports(reports)
    states = [result['contrasts'][name]['state'] for name in CONTRASTS]
    decision = ('QUALITY_REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in states else
                'SEASON_GAP_RETAINED' if all(v == 'MEAN_IMPROVEMENT' for v in states)
                else 'SEASON_GAP_DEFERRED')
    result.update(artifact_kind='season_gap_three_seed_research_summary', research_decision=decision,
        candidate_retention='RETAIN' if decision == 'SEASON_GAP_RETAINED' else
                            'REVIEW_REQUIRED' if decision == 'QUALITY_REVIEW_REQUIRED' else 'DEFER',
        preferred_research_configuration='JOINT_SEASON_GAP' if decision == 'SEASON_GAP_RETAINED' else
                                         'UNCHANGED_PENDING_REVIEW' if decision == 'QUALITY_REVIEW_REQUIRED' else 'PRUNING_GAP')
    result['limitations'] = [
        'Joint3-term correction re-estimates gap together with seasonal-sex coefficients; this is not an isolated season effect holding gap fixed.',
        'Both anchor and retained mean NLL contrasts must improve for incremental retention; no all-seed, majority or per-seed significance condition.',
        'Average losses are not metrics of averaged probabilities. No mean CI; three-seed SD is descriptive.',
        'Individual k_seeds1 transferred CIs and coefficient-fixed bootstrap do not cover joint-vector refitting, retained-side gamma refitting or selection completely.',
        'Known-sex homogeneous races have race-constant seasonal terms; missing-sex races are separately described.',
        'Diagnostic groups add no gate or subgroup model rule. Deployment seed42 retains its individual residual risk.',
        'Historical full-information research only, no unused holdout, confirmation slot or production activation.']
    return result


def validate_population(reports, evidence, legacy, legacy_race_hashes):
    result = checks.validate_population(reports, evidence, legacy)
    if set(legacy_race_hashes) != set(SEEDS):
        raise ValueError('All three original116 full scored hashes required')
    for seed in SEEDS:
        for name in CONTRASTS:
            if not legacy_race_hashes[seed] or reports[seed][name]['race_id_set_hash'] != legacy_race_hashes[seed]:
                raise ValueError('Full scored race hash differs from original116, including noneligible races')
    result['all_full_scored_hashes_equal_original116'] = True
    return result


def sex_diagnostics(evidence, audit):
    checks.scope(evidence)
    rows = audit['race_attributes']
    reference = evidence[42]['anchor']['rows']
    if (len(rows) != 23030 or len({r['race_id'] for r in rows}) != 23030
        or any(type(r.get('eligible')) is not bool or r.get('sex_group') not in GROUPS
               or r.get('year') != int(r['race_day'][:4]) for r in rows)):
        raise ValueError('Invalid fixed119 sex diagnostic population')
    eligible = [r for r in rows if r['eligible']]
    if (len(eligible) != 22990 or [(r['race_id'], r['race_day']) for r in eligible] !=
        [(r['race_id'], r['race_day']) for r in reference]):
        raise ValueError('119 sex attributes differ from ordered scored population')
    result = {}
    for period in ('all', '2026'):
        groups = {}
        for group in GROUPS:
            indices = [i for i, r in enumerate(eligible) if r['sex_group'] == group and (period == 'all' or r['year'] == 2026)]
            n_days = len({eligible[i]['race_day'] for i in indices})
            values = {}
            for contrast in CONTRASTS:
                per_seed = {str(seed): {k: statistics.mean(finite(evidence[seed][contrast]['rows'][i][field]) for i in indices)
                                       if indices else None
                    for k, field in [('candidate', 'candidate_winner_nll'), ('active', 'active_winner_nll'), ('diff', 'diff')]}
                    for seed in SEEDS}
                values[contrast] = {'by_seed': per_seed, 'equal_seed_mean': {
                    k: statistics.mean(per_seed[str(seed)][k] for seed in SEEDS) if indices else None
                    for k in ('candidate', 'active', 'diff')}}
            groups[group] = {'n_eligible': len(indices), 'n_days': n_days, 'contrasts': values}
        result[period] = groups
    return {'source': 'frozen119 season-input-audit.json', 'extra_quality_gate': False,
            'new_ci': False, 'groups': result}


def main():
    import season_gap_recheck as driver
    cfg, frozen = driver.verify()
    reports, evidence, legacy, hashes, legacy_race_hashes = {}, {}, {}, {}, {}
    cfg116, f116 = driver.d.verify()
    for seed in SEEDS:
        reports[seed], evidence[seed], hashes[str(seed)] = {}, {}, {}
        if not driver.d.verified_result(seed, 'anchor', cfg116, f116):
            raise ValueError('Original116 baseline evidence required')
        old = driver.s.read_json(driver.d.result_path(seed, 'anchor'))
        legacy_race_hashes[seed] = old['race_id_set_hash']
        legacy[seed] = driver.s.read_json(old['evidence_path'])
        for contrast in driver.CONTRASTS:
            name = contrast['id']
            if not driver.verified_result(seed, contrast, cfg, frozen):
                raise ValueError('All six verified119 reports required')
            path = driver.result_path(seed, name)
            r = reports[seed][name] = driver.s.read_json(path)
            evidence[seed][name] = driver.s.read_json(r['evidence_path'])
            hashes[str(seed)][name] = {'report_sha256': driver.s.p.digest(path), 'evidence_sha256': driver.s.p.digest(r['evidence_path'])}
    population = validate_population(reports, evidence, legacy, legacy_race_hashes)
    result = summarize_reports(reports)
    audit_path = driver.WORK / 'season-input-audit.json'
    audit = driver.s.read_json(audit_path)
    attrs_path = driver.ROOT / 'artifacts/117-pruning-2026-diagnostic/race-diagnostic.json'
    result.update(population=population, sources=hashes,
        fixed117_diagnostics=checks.fixed_diagnostics(evidence, driver.s.read_json(attrs_path)['rows']),
        seasonal_sex_diagnostics=sex_diagnostics(evidence, audit),
        seasonal_input_audit_sha256=driver.s.p.digest(audit_path), attributes117_sha256=driver.s.p.digest(attrs_path),
        run_freeze_sha256=driver.s.p.digest(driver.WORK / 'run-freeze.json'),
        config_hash=driver.s.p.gate_config_hash(cfg), summarizer_sha256=driver.s.p.digest(__file__))
    driver.verify()
    path = driver.SPEC / 'verdict.json'
    if path.exists():
        if driver.s.read_json(path) != result:
            raise ValueError('Preserve changed119 summary')
    else:
        driver.write_json(path, result)
    print({'research_decision': result['research_decision'], 'means': {
        k: v['equal_seed_mean_periods']['all']['diff']['mean'] for k, v in result['contrasts'].items()}})


if __name__ == '__main__':
    main()
