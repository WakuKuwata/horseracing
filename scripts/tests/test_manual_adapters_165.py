from __future__ import annotations

import datetime as dt

import pytest

from scripts.roi_explore.manual_observation_165.adapters import ObservationAdapters, ObservationDisabled, SQL
from scripts.roi_explore.model_connection_163.run import connection_disabled

RACE = '202609040611'


class Clock:
    def __init__(self, values=None):
        self.at = dt.datetime(2026, 9, 20, 5, 40, tzinfo=dt.UTC)
        self.values = iter(values) if values is not None else None

    def now(self):
        if self.values is not None:
            return next(self.values)
        at = self.at
        self.at += dt.timedelta(milliseconds=1)
        return at.isoformat()


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Context:
    def __init__(self, conn, kind):
        self.conn, self.kind = conn, kind

    def __enter__(self):
        self.conn.log.append('enter_'+self.kind)
        return self.conn

    def __exit__(self, *args):
        self.conn.log.append('exit_'+self.kind)


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.log = []
        self.executed = []

    def __enter__(self):
        self.log.append('enter_connection')
        return self

    def __exit__(self, *args):
        self.log.append('exit_connection')

    def execution_options(self, **kwargs):
        assert kwargs == {'isolation_level': 'REPEATABLE READ'}
        self.log.append('repeatable_read')
        return self

    def begin(self):
        return Context(self, 'transaction')

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.executed.append((sql, parameters))
        if sql == 'SET TRANSACTION READ ONLY':
            assert len(self.executed) == 1
            return Result([])
        assert self.executed[0][0] == 'SET TRANSACTION READ ONLY'
        assert parameters == {'race_id': RACE}
        kind = next(k for k, v in SQL.items() if v == sql)
        return Result(self.rows[kind])


class Engine:
    def __init__(self):
        self.connections = []
        self.disposed = 0
        self.rows = dict(race=[dict(race_id=RACE, race_date=dt.date(2026, 9, 20),
            post_time=dt.datetime(2026, 9, 20, 6, 0, tzinfo=dt.UTC))],
            entries=[dict(horse_id='nk:a', horse_number=1, entry_status='started'),
                dict(horse_id='b', horse_number=2, entry_status='cancelled')], results=[])

    def connect(self):
        conn = Connection(self.rows)
        self.connections.append(conn)
        return conn

    def dispose(self):
        self.disposed += 1


class Fetcher:
    def __init__(self, result='saved raw\r\nbody'):
        self.result, self.calls, self.closed = result, [], 0

    def get(self, url, *, use_cache):
        self.calls.append((url, use_cache))
        return self.result

    def close(self):
        self.closed += 1


def test_disabled_touches_no_factory_environment_or_clock():
    def forbidden():
        raise AssertionError('disabled factory touched')
    adapter = ObservationAdapters(engine_factory=forbidden, fetcher_factory=forbidden)
    adapter.enabled = True  # A similarly named public flag cannot enable the private gate.
    with connection_disabled():
        with pytest.raises(ObservationDisabled):
            adapter.observe_guard('before', RACE, object())
        with pytest.raises(ObservationDisabled):
            adapter.fetch_quote(RACE, object())
        with pytest.raises(ObservationDisabled):
            adapter.engine()
        adapter.close()


@pytest.mark.parametrize('enabled', [None, 1, 'true', [], {}])
def test_nonboolean_enable_flag_rejected(enabled):
    with pytest.raises(ValueError, match='enabled_must_be_boolean'):
        ObservationAdapters(enabled=enabled)


def test_fresh_readonly_snapshot_each_stage_and_original_ids_preserved():
    engine = Engine()
    adapter = ObservationAdapters(enabled=True, engine_factory=lambda:engine)
    clock = Clock()
    with connection_disabled():
        first = adapter.observe_guard('before', RACE, clock)
        second = adapter.observe_guard('after', RACE, clock)
        final = adapter.observe_guard('decision', RACE, clock)
    assert len(engine.connections) == 3
    assert len({id(c) for c in engine.connections}) == 3
    for conn in engine.connections:
        assert conn.log == ['enter_connection','repeatable_read','enter_transaction','exit_transaction','exit_connection']
        assert len(conn.executed) == 4
        assert conn.executed[0] == ('SET TRANSACTION READ ONLY', None)
    for observation in (first, second, final):
        assert set(observation) == {'race','entries','results'}
        assert len({q['started_at'] for q in observation.values()}) == 1
        assert observation['entries']['rows'] == engine.rows['entries']
        assert observation['results']['rows'] == []
        assert observation['race']['rows'][0]['post_time'] == '2026-09-20T06:00:00.000000+00:00'
        assert observation['race']['rows'][0]['race_date'] == '2026-09-20'
        assert all(q['started_at'] <= q['completed_at'] for q in observation.values())
    assert first['race']['started_at'] < second['race']['started_at'] < final['race']['started_at']


def test_results_are_preserved_not_silently_marked_pending():
    engine = Engine()
    engine.rows['results'] = [dict(race_id=RACE), dict(race_id=RACE)]
    result = ObservationAdapters(enabled=True,engine_factory=lambda:engine).observe_guard('decision',RACE,Clock())
    assert result['results']['rows'] == engine.rows['results']


def test_quote_uncached_exact_body_and_call_clock():
    fetcher = Fetcher()
    adapter = ObservationAdapters(enabled=True,fetcher_factory=lambda:fetcher,
        engine_factory=lambda:(_ for _ in ()).throw(AssertionError('injected fetch should not need DB')))
    with connection_disabled():
        response = adapter.fetch_quote(RACE,Clock())
    assert response['raw_text'] == 'saved raw\r\nbody'
    assert fetcher.calls == [(response['source_url'],False)]
    assert response['fetch_started_at'] < response['received_at']
    assert response['source_url'] == f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={RACE}&type=1&action=update'


@pytest.mark.parametrize('stage', ['seal',None,'BEFORE'])
def test_invalid_stage_does_not_create_engine(stage):
    adapter = ObservationAdapters(enabled=True,engine_factory=lambda:(_ for _ in ()).throw(AssertionError('engine used')))
    with pytest.raises(ValueError,match='invalid_guard_stage'):
        adapter.observe_guard(stage,RACE,Clock())


@pytest.mark.parametrize('rid', [None,'2026','2026../../xx','202609040611x',123])
def test_invalid_race_does_not_create_any_resource(rid):
    def forbidden():
        raise AssertionError('resource used')
    adapter = ObservationAdapters(enabled=True,engine_factory=forbidden,fetcher_factory=forbidden)
    with pytest.raises(ValueError,match='invalid_race_id'):
        adapter.fetch_quote(rid,Clock())
    with pytest.raises(ValueError,match='invalid_race_id'):
        adapter.observe_guard('before',rid,Clock())


def test_clock_reversal_fails_without_retry():
    fetcher = Fetcher()
    adapter = ObservationAdapters(enabled=True,fetcher_factory=lambda:fetcher)
    clock = Clock(['2026-09-20T05:40:01Z','2026-09-20T05:40:00Z'])
    with pytest.raises(ValueError,match='observation_clock_reversed'):
        adapter.fetch_quote(RACE,clock)
    assert len(fetcher.calls) == 1


@pytest.mark.parametrize('raw', ['', '  ', b'raw', None])
def test_invalid_quote_body_fails_without_retry(raw):
    fetcher = Fetcher(raw)
    adapter = ObservationAdapters(enabled=True,fetcher_factory=lambda:fetcher)
    with pytest.raises(ValueError,match='quote_response_empty_or_nontext'):
        adapter.fetch_quote(RACE,Clock())
    assert len(fetcher.calls) == 1


def test_fetch_refusal_is_not_retried():
    class Refusal(RuntimeError):
        pass
    class Refuser(Fetcher):
        def get(self,url,*,use_cache):
            self.calls.append((url,use_cache))
            raise Refusal('rate limited')
    fetcher = Refuser()
    adapter = ObservationAdapters(enabled=True,fetcher_factory=lambda:fetcher)
    with pytest.raises(Refusal):
        adapter.fetch_quote(RACE,Clock())
    assert len(fetcher.calls) == 1


def test_naive_db_datetime_rejected_and_connection_closed():
    engine = Engine()
    engine.rows['race'][0]['post_time'] = dt.datetime(2026,9,20,6,0)
    adapter = ObservationAdapters(enabled=True,engine_factory=lambda:engine)
    with pytest.raises(ValueError,match='database_timestamp_naive'):
        adapter.observe_guard('before',RACE,Clock())
    assert engine.connections[0].log[-2:] == ['exit_transaction','exit_connection']


def test_close_releases_lazy_resources_once():
    engine,fetcher = Engine(),Fetcher()
    adapter = ObservationAdapters(enabled=True,engine_factory=lambda:engine,fetcher_factory=lambda:fetcher)
    adapter.observe_guard('before',RACE,Clock())
    adapter.fetch_quote(RACE,Clock())
    adapter.close()
    adapter.close()
    assert engine.disposed == fetcher.closed == 1
