"""Observed UTC wall time with monotonic cross-check. No offset or fake-time option."""
from __future__ import annotations

import datetime as dt
import time as system_time
import uuid

from .contract import require, time


class RealClock:
    simulated = False

    def __init__(self):
        self.epoch_id = uuid.uuid4().hex
        self.observations = []

    def now(self):
        monotonic = system_time.monotonic_ns()
        wall = dt.datetime.now(dt.UTC)
        if self.observations:
            previous = self.observations[-1]
            before = dt.datetime.fromisoformat(previous['at'])
            require(monotonic >= previous['monotonic_ns'] and wall >= before, 'clock_reversed')
            elapsed = (wall-before).total_seconds()
            mono_elapsed = (monotonic-previous['monotonic_ns'])/1e9
            require(abs(elapsed-mono_elapsed) <= 1, 'clock_wall_monotonic_drift')
        stamp = wall.isoformat(timespec='microseconds')
        self.observations.append(dict(at=stamp, monotonic_ns=monotonic, epoch_id=self.epoch_id))
        return stamp


def verify_observations(observations):
    require(bool(observations), 'clock_observations_missing')
    epoch = observations[0]['epoch_id']
    for row in observations:
        require(set(row) == {'at', 'monotonic_ns', 'epoch_id'} and row['epoch_id'] == epoch
            and isinstance(epoch, str) and len(epoch) == 32 and type(row['monotonic_ns']) is int
            and row['monotonic_ns'] >= 0, 'clock_observation_schema_invalid')
        time(row['at'])
    for before, after in zip(observations, observations[1:]):
        require(before['epoch_id'] == after['epoch_id'] == epoch, 'clock_epoch_changed')
        wall = (time(after['at'])-time(before['at'])).total_seconds()
        mono = (after['monotonic_ns']-before['monotonic_ns'])/1e9
        require(wall >= 0 and mono >= 0 and abs(wall-mono) <= 1, 'clock_observations_invalid')
