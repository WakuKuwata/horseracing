"""Exclusive, fsynced local evidence publication with readback receipts. Offline clock only."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path

from .contract import (MODE, canonical, check_protocol, decide, digest, gate, require,
    sha_bytes, time, validate_inputs)


class SimulatedClock:
    simulated = True

    def __init__(self, at):
        self.value = time(at)
        self.last = None

    def now(self):
        value = self.value
        require(self.last is None or value >= self.last, 'clock_reversed')
        self.last = value
        self.value += dt.timedelta(microseconds=1)
        return value.isoformat()

    def advance_to(self, at):
        target = time(at)
        require(target >= self.value, 'clock_reversed')
        self.value = target


def read(path):
    return json.loads(Path(path).read_text())


def publish(path, value):
    """No replace: identical retries are idempotent; conflicting payloads always fail."""
    path = Path(path)
    body = canonical(value)
    missing, directory = [], path.parent
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        # A new directory entry must be durable too, not just the eventual JSON file.
        parent = os.open(directory.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    if path.exists():
        require(path.read_bytes() == body, 'immutable_record_conflict')
        return sha_bytes(body)
    fd, temp = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            require(path.read_bytes() == body, 'immutable_record_conflict')
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        require(path.read_bytes() == body, 'readback_failed')
    finally:
        os.unlink(temp)
    return sha_bytes(body)


class EvidenceStore:
    def __init__(self, root, protocol, clock):
        require(type(clock) is SimulatedClock and clock.simulated, 'offline_clock_required')
        check_protocol(protocol)
        self.root, self.protocol, self.clock = Path(root), json.loads(canonical(protocol)), clock
        publish(self.root/'protocol.json', protocol)

    def _race(self, race_id):
        require(isinstance(race_id, str) and len(race_id) == 12 and race_id.isdigit(), 'invalid_race_id')
        return self.root/'races'/race_id

    def prepare(self, bundle):
        require(read(self.root/'protocol.json') == self.protocol, 'protocol_tampered')
        latest = validate_inputs(bundle, self.protocol)
        require(time(self.clock.now()) >= latest, 'input_from_future')
        path = self._race(bundle['race_id'])
        require(not (path/'failure.json').exists(), 'session_already_aborted')
        publish(path/'reservation.json', dict(session_id=bundle['session_id'], input_sha256=digest(bundle)))
        checksum = publish(path/'inputs.json', bundle)
        # This timestamp is observed only AFTER the fsync/readback of both model outputs and quote.
        receipt_path = path/'ready.json'
        if not receipt_path.exists():
            publish(receipt_path, dict(mode=MODE, simulated_clock=True, eligible_for_prospective=False,
                input_sha256=checksum, available_by_at=self.clock.now(), protocol_sha256=digest(self.protocol)))
        return self._prepared(bundle['race_id'])[1]

    def _prepared(self, race_id):
        require(read(self.root/'protocol.json') == self.protocol, 'protocol_tampered')
        path = self._race(race_id)
        require(not (path/'failure.json').exists(), 'session_already_aborted')
        bundle, ready = read(path/'inputs.json'), read(path/'ready.json')
        require(bundle['race_id'] == race_id, 'race_identity_mismatch')
        require(read(path/'reservation.json') == dict(session_id=bundle['session_id'],
            input_sha256=digest(bundle)), 'reservation_tampered')
        require(ready == dict(mode=MODE, simulated_clock=True, eligible_for_prospective=False,
            input_sha256=digest(bundle), available_by_at=ready['available_by_at'],
            protocol_sha256=digest(self.protocol)), 'ready_tampered')
        require(time(ready['available_by_at']) >= validate_inputs(bundle, self.protocol), 'ready_before_computation')
        return bundle, ready

    def seal(self, race_id, guard):
        path = self._race(race_id)
        if (path/'accepted.json').exists():
            return self.verify(race_id)
        require(not (path/'decision.json').exists(), 'incomplete_seal_requires_audit')
        bundle, ready = self._prepared(race_id)
        decision_at = self.clock.now()
        gate(bundle, guard, ready['available_by_at'], decision_at, self.protocol)
        decision = decide(bundle, decision_at, self.protocol)
        decision['guard'] = guard
        decision['input_sha256'] = digest(bundle)
        decision['ready_sha256'] = digest(ready)
        checksum = publish(path/'decision.json', decision)
        # Publish a seal only after decision data has become durable and was read back.
        seal = dict(decision_sha256=checksum, observed_saved_at=self.clock.now())
        gate(bundle, guard, ready['available_by_at'], seal['observed_saved_at'], self.protocol)
        seal_sha = publish(path/'seal.json', seal)
        # Admission records the acknowledgement AFTER the seal itself is durable.
        ack = self.clock.now()
        gate(bundle, guard, ready['available_by_at'], ack, self.protocol)
        publish(path/'accepted.json', dict(mode=MODE, simulated_clock=True,
            eligible_for_prospective=False, seal_sha256=seal_sha, acknowledged_at=ack))
        return self.verify(race_id)

    def abort(self, race_id, reason):
        path = self._race(race_id)
        require(not (path/'accepted.json').exists(), 'accepted_session_cannot_abort')
        # Unaccepted files are staging. Persist reason/hash metadata before removing full quotes.
        if not (path/'failure.json').exists():
            publish(path/'failure.json', dict(mode=MODE, simulated_clock=True,
                eligible_for_prospective=False, race_id=race_id, reason=str(reason),
                recorded_at=self.clock.now(), staging_sha256={p.name: sha_bytes(p.read_bytes())
                    for p in path.glob('*.json') if p.name != 'failure.json'}))
        for name in ('inputs.json', 'ready.json', 'decision.json', 'seal.json'):
            (path/name).unlink(missing_ok=True)
        directory = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def verify(self, race_id):
        require(read(self.root/'protocol.json') == self.protocol, 'protocol_tampered')
        path = self._race(race_id)
        bundle, ready, decision, seal, accepted = [read(path/n) for n in
            ('inputs.json', 'ready.json', 'decision.json', 'seal.json', 'accepted.json')]
        require(bundle['race_id'] == race_id, 'race_identity_mismatch')
        require(read(path/'reservation.json') == dict(session_id=bundle['session_id'],
            input_sha256=digest(bundle)), 'reservation_tampered')
        require(ready == dict(mode=MODE, simulated_clock=True, eligible_for_prospective=False,
            input_sha256=digest(bundle), available_by_at=ready['available_by_at'],
            protocol_sha256=digest(self.protocol)), 'ready_tampered')
        require(time(ready['available_by_at']) >= validate_inputs(bundle, self.protocol), 'ready_before_computation')
        require(accepted == dict(mode=MODE, simulated_clock=True, eligible_for_prospective=False,
            seal_sha256=digest(seal), acknowledged_at=accepted['acknowledged_at']), 'acceptance_tampered')
        require(seal['decision_sha256'] == digest(decision), 'decision_tampered')
        require(time(decision['decision_at']) <= time(seal['observed_saved_at']) <= time(accepted['acknowledged_at']),
            'seal_time_order')
        for stamp in [decision['decision_at'], seal['observed_saved_at'], accepted['acknowledged_at']]:
            gate(bundle, decision['guard'], ready['available_by_at'], stamp, self.protocol)
        reproduced = decide(bundle, decision['decision_at'], self.protocol)
        reproduced.update(guard=decision['guard'], input_sha256=digest(bundle), ready_sha256=digest(ready))
        require(decision == reproduced, 'decision_not_reproducible')
        return dict(**decision, sealed_at=accepted['acknowledged_at'])

    def add_settlement(self, race_id, evidence):
        from .settlement import settle
        decision = self.verify(race_id)
        path = self._race(race_id)/'settlements'
        revision = evidence.get('revision')
        require(type(revision) is int and 1 <= revision <= 9999, 'invalid_settlement_revision')
        destination = path/f'{revision:04}'
        if not destination.exists():
            require(revision == len(list(path.glob('[0-9][0-9][0-9][0-9]')))+1, 'settlement_revision_gap')
        previous = read(path/f'{revision-1:04}'/'result.json') if revision > 1 else None
        if previous is not None:
            require(time(evidence['observed_at']) > time(previous['observed_at']), 'settlement_time_reversed')
        result = settle(decision, evidence)
        result.update(decision_sha256=digest(read(self._race(race_id)/'decision.json')),
            evidence_sha256=digest(evidence), previous_sha256=digest(previous) if previous else None)
        publish(destination/'evidence.json', evidence)
        publish(destination/'result.json', result)
        return result

    def verify_settlements(self, race_id):
        from .settlement import settle
        decision = self.verify(race_id)
        path = self._race(race_id)/'settlements'
        previous, results = None, []
        for index, folder in enumerate(sorted(path.glob('[0-9][0-9][0-9][0-9]')), 1):
            require(folder.name == f'{index:04}', 'settlement_revision_gap')
            evidence, actual = read(folder/'evidence.json'), read(folder/'result.json')
            require(evidence['revision'] == index, 'settlement_revision_mismatch')
            if previous:
                require(time(evidence['observed_at']) > time(previous['observed_at']), 'settlement_time_reversed')
            expected = settle(decision, evidence)
            expected.update(decision_sha256=digest(read(self._race(race_id)/'decision.json')),
                evidence_sha256=digest(evidence), previous_sha256=digest(previous) if previous else None)
            require(actual == expected, 'settlement_tampered')
            previous = actual
            results.append(actual)
        return results
