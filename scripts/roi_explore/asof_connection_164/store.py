"""As-of evidence journal and current-clock gate. Never seals a historical decision anew."""
from __future__ import annotations

import copy
from pathlib import Path

from scripts.roi_explore.model_connection_163 import contract as c
from scripts.roi_explore.model_connection_163.clock import RealClock
from scripts.roi_explore.model_connection_163 import models
from scripts.roi_explore.prospective_rehearsal_162.store import publish, read
from .contract import inspect_candidate, validate_prediction, validate_quote
from .features import MODE, materialize


class AsOfEvidenceStore:
    def __init__(self, root, clock):
        c.require(type(clock) is RealClock, 'real_clock_required')
        self.root, self.clock = Path(root), clock
        self.protocol = dict(schema_version=1, mode=MODE, eligible_for_prospective=False,
            new_collection=False, database_access=False, max_quote_age_seconds=1800,
            prediction_available='original durable receipt <= decision', guards='source-bound before/after/decision',
            model_protocol=c.make_protocol())
        publish(self.root/'protocol.json', self.protocol)

    def _check_protocol(self):
        c.require(read(self.root/'protocol.json') == self.protocol, 'protocol_changed')

    def record_candidate(self, candidate):
        self._check_protocol()
        report = inspect_candidate(candidate)
        path = self.root/'candidates'/candidate['race_id']
        publish(path/'reference.json', candidate)
        publish(path/'checks.json', report)
        return report

    def replay_component(self, reference):
        """Current inference on the authentic saved matrix; not a past forecast/ready receipt."""
        self._check_protocol()
        packet = materialize(reference)
        start = self.clock.now()
        fn = models.infer_legacy if packet['kind'] == 'legacy' else models.infer_mev
        args = dict(race_id=packet['race_id'], race_date=packet['race_date'])
        if packet['kind'] == 'legacy':
            args['history'] = packet['history']
        output = fn(packet['rows'], **args)
        done = self.clock.now()
        path = self.root/'component-replays'/packet['race_id']/packet['kind']
        body = dict(artifact_kind='current_model_component_replay_v1', mode=MODE,
            eligible_for_prospective=False, race_id=packet['race_id'], source_ref=reference,
            original_feature_available_at=packet['available_by_at'], started_at=start, computed_at=done,
            input_sha256=packet['input_sha256'], result=output,
            historical_ready_receipt_created=False, usable_as_original_prediction=False)
        checksum = publish(path/'prediction.json', body)
        publish(path/'current-ready.json', dict(mode=MODE, eligible_for_prospective=False,
            source_sha256=checksum, available_by_at=self.clock.now(), historical=False))
        return body

    def seal_now(self, candidate):
        """Only a current prestart case can be sealed; replay never receives a past seal."""
        self._check_protocol()
        path = self.root/'decisions'/candidate['race_id']
        if (path/'accepted.json').exists():
            previous = read(path/'candidate.json')
            c.require({k:v for k,v in candidate.items() if k != 'decision_at'} ==
                {k:v for k,v in previous.items() if k != 'decision_at'}, 'different_candidate_for_existing_decision')
            return self.verify_decision(candidate['race_id'])
        c.require(not (path/'decision.json').exists(), 'incomplete_seal_requires_audit')
        now = self.clock.now()
        c.require(c.time(now) < c.time(candidate['post_time']), 'historical_reference_cannot_be_sealed_now')
        current = copy.deepcopy(candidate)
        current['decision_at'] = now
        report = inspect_candidate(current)
        c.require(report['contract_ready'], 'asof_contract_incomplete')
        decision = self._build_decision(current)
        publish(path/'candidate.json', current)
        checksum = publish(path/'decision.json', decision)
        observed = self.clock.now()
        # Recheck final guard, freshness, full field and source bindings after actual publication.
        after = dict(current, decision_at=observed)
        c.require(inspect_candidate(after)['contract_ready'], 'asof_seal_conditions_expired')
        publish(path/'seal.json', dict(mode=MODE, eligible_for_prospective=False,
            decision_sha256=checksum, observed_saved_at=observed))
        acknowledged = self.clock.now()
        c.require(inspect_candidate(dict(current, decision_at=acknowledged))['contract_ready'],
            'asof_seal_conditions_expired')
        publish(path/'accepted.json', dict(mode=MODE, eligible_for_prospective=False,
            seal_sha256=c.digest(read(path/'seal.json')), acknowledged_at=acknowledged))
        return self.verify_decision(candidate['race_id'])

    def _build_decision(self, current):
        packets = {k: materialize(current['features'][k]) for k in ('mev', 'legacy')}
        predictions = {k: validate_prediction(current['predictions'][k], kind=k, packet=packets[k],
            race_id=current['race_id'], race_date=current['race_date'], expected_field=current['field'],
            decision_at=current['decision_at']) for k in packets}
        quote = validate_quote(current['quote'], race_id=current['race_id'],
            expected_field=current['field'], decision_at=current['decision_at'])
        bundle = dict(race_id=current['race_id'], race_date=current['race_date'],
            post_time=current['post_time'], field=current['field'], quote=quote, predictions=predictions,
            session_id='asof-'+c.digest(current)[:24])
        decision = c.decide(bundle, current['decision_at'], c.make_protocol())
        decision.pop('synthetic_post', None)
        decision.update(mode=MODE, schedule_origin='saved_guard_packet', real_race_verified=False,
            eligible_for_prospective=False, asof_candidate_sha256=c.digest(current),
            asof_protocol_sha256=c.digest(self.protocol))
        return decision

    def verify_decision(self, race_id):
        self._check_protocol()
        path = self.root/'decisions'/race_id
        candidate, decision, seal, accepted = [read(path/name) for name in
            ('candidate.json', 'decision.json', 'seal.json', 'accepted.json')]
        c.require(candidate['race_id'] == race_id and c.digest(candidate) == decision['asof_candidate_sha256'],
            'decision_candidate_changed')
        c.require(c.digest(decision) == seal['decision_sha256'] and c.digest(seal) == accepted['seal_sha256'],
            'decision_seal_changed')
        c.require(c.time(decision['decision_at']) <= c.time(seal['observed_saved_at']) <= c.time(accepted['acknowledged_at']),
            'decision_seal_clock_order')
        for at in (decision['decision_at'], seal['observed_saved_at'], accepted['acknowledged_at']):
            c.require(inspect_candidate(dict(candidate, decision_at=at))['contract_ready'], 'saved_contract_no_longer_valid')
        c.require(decision['mode'] == accepted['mode'] == MODE and decision['eligible_for_prospective'] is False
            and accepted['eligible_for_prospective'] is False and decision['real_race_verified'] is False,
            'offline_mode_changed')
        c.require(decision == self._build_decision(candidate), 'decision_not_reproducible')
        return decision

    def add_settlement(self, race_id, evidence):
        from .settlement import settle
        decision = self.verify_decision(race_id)
        path = self.root/'decisions'/race_id
        decision = dict(decision, sealed_at=read(path/'accepted.json')['acknowledged_at'])
        c.require(c.time(evidence['observed_at']) <= c.time(self.clock.now()), 'official_observation_from_future')
        revision = evidence.get('revision')
        c.require(type(revision) is int and 1 <= revision <= 9999, 'invalid_settlement_revision')
        folder = path/'settlements'/f'{revision:04}'
        existing = list((path/'settlements').glob('[0-9][0-9][0-9][0-9]'))
        c.require(folder.exists() or revision == len(existing)+1, 'settlement_revision_gap')
        previous = read(path/'settlements'/f'{revision-1:04}'/'result.json') if revision > 1 else None
        if previous:
            c.require(c.time(evidence['observed_at']) > c.time(previous['observed_at']), 'settlement_clock_reversed')
        result = settle(decision, evidence)
        result.update(decision_sha256=c.digest(read(path/'decision.json')), evidence_sha256=c.digest(evidence),
            previous_sha256=c.digest(previous) if previous else None)
        publish(folder/'evidence.json', evidence)
        publish(folder/'result.json', result)
        return result

    def verify_settlements(self, race_id):
        from .settlement import settle
        decision = self.verify_decision(race_id)
        path = self.root/'decisions'/race_id
        decision = dict(decision, sealed_at=read(path/'accepted.json')['acknowledged_at'])
        results, previous = [], None
        for revision, folder in enumerate(sorted((path/'settlements').glob('[0-9][0-9][0-9][0-9]')), 1):
            c.require(folder.name == f'{revision:04}', 'settlement_revision_gap')
            evidence, saved = read(folder/'evidence.json'), read(folder/'result.json')
            c.require(evidence['revision'] == revision, 'settlement_revision_mismatch')
            if previous:
                c.require(c.time(evidence['observed_at']) > c.time(previous['observed_at']), 'settlement_clock_reversed')
            result = settle(decision, evidence)
            result.update(decision_sha256=c.digest(read(path/'decision.json')), evidence_sha256=c.digest(evidence),
                previous_sha256=c.digest(previous) if previous else None)
            c.require(result == saved, 'settlement_changed')
            previous = saved
            results.append(saved)
        return results
