"""The public directory survives cold starts without rebuilding the database."""
import gzip
import json
import secrets

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SourceConfig
from app.parsers import normalize_rows


HEADERS = {'X-Requested-With': 'MicList'}


def test_healthy_empty_database_does_not_use_production_bundle(database, monkeypatch, tmp_path):
    from app import public_snapshot
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setattr(public_snapshot, 'FALLBACK_PATH', tmp_path / 'must-not-be-used.gz')
    app = create_app(database)
    app.state.store._initialize()
    with TestClient(app) as visitor:
        data = visitor.get('/api/public').json()
        assert data['listings'] == [] and data['mode'] == 'empty' and not data['stale']
        assert visitor.get('/api/public/revision').json()['revision'] == 'empty'


@pytest.fixture
def snapshot_app(tmp_path, monkeypatch):
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv('ADMIN_PASSWORD', password)
    monkeypatch.setenv('LOCAL_DEV', '1')
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.delenv('PUBLIC_ORIGIN', raising=False)
    database = str(tmp_path / 'snapshot.sqlite3')
    app = create_app(database)
    with TestClient(app, headers=HEADERS) as client:
        assert client.post('/api/login', json={'password': password}).status_code == 200
        yield app, client, database


def add_source(store, name='Fixture mic', uid='fixture'):
    config = SourceConfig(name='Public source', kind='upload', permission_confirmed=True)
    rows = normalized(name, uid)
    return store.commit_preview(store.preview(config.model_dump(), {
        'rows': rows, 'warnings': [], 'skipped': 0,
    }))


def normalized(name='Fixture mic', uid='fixture'):
    rows, warnings, skipped = normalize_rows([{
        'external_id': uid, 'name': name, 'venue': 'A public room',
        'address': '1 Broadway', 'borough': 'Manhattan', 'weekday': 'Monday',
        'start_time': '19:00', 'cost': '5', 'status': 'scheduled',
    }], SourceConfig(name='Fixture', permission_confirmed=True))
    assert not warnings and not skipped
    return rows


def trace_queries(store, monkeypatch):
    statements = []
    original = store._open

    def traced_open():
        connection = original()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, '_open', traced_open)
    return statements


def assert_no_public_rebuild(statements):
    normalized_sql = [' '.join(sql.lower().split()) for sql in statements]
    assert not any(' from observations' in sql for sql in normalized_sql), normalized_sql
    assert not any(' from mic_registry' in sql for sql in normalized_sql), normalized_sql
    assert not any('insert into mic_registry' in sql or 'update mic_registry' in sql
                   for sql in normalized_sql), normalized_sql


def test_repeated_visits_revision_and_seo_do_not_scan_or_write_registry(snapshot_app, monkeypatch):
    app, client, _ = snapshot_app
    store = app.state.store
    add_source(store)
    initial = client.get('/api/public')
    assert initial.status_code == 200
    payload = initial.json()
    assert payload['revision'] and payload['snapshot_at'] and not payload['stale']
    statements = trace_queries(store, monkeypatch)
    assert client.get('/api/public').json() == payload
    revision = client.get('/api/public/revision')
    assert revision.status_code == 200
    assert revision.json() == {key: payload[key] for key in (
        'revision', 'snapshot_at', 'stale', 'last_sync_at')}
    assert len(revision.content) < 1024
    assert client.get('/nyc-comedy-open-mics').status_code == 200
    assert client.get('/mics/' + payload['listings'][0]['id']).status_code == 200
    assert client.get('/sitemap.xml').status_code == 200
    assert client.get('/api/export.csv').status_code == 200
    assert_no_public_rebuild(statements)


def test_new_server_instance_reads_durable_snapshot_without_rebuild(snapshot_app, monkeypatch):
    app, client, database = snapshot_app
    add_source(app.state.store)
    initial = client.get('/api/public').json()
    cold = create_app(database)
    statements = trace_queries(cold.state.store, monkeypatch)
    with TestClient(cold) as visitor:
        assert visitor.get('/api/public').json() == initial
        assert visitor.get('/api/public/revision').json()['revision'] == initial['revision']
    assert_no_public_rebuild(statements)
    assert not cold.state.store._ready, 'Public reads must not initialize or migrate the schema'
    assert not any(sql.lstrip().lower().startswith(('create ', 'insert ', 'update ', 'delete '))
                   for sql in statements), statements


def test_authorized_bootstrap_materializes_existing_data_only_once(snapshot_app, monkeypatch):
    from app.public_snapshot import INPUT_KEY, REGISTRY_KEY, REVISION_KEY, SNAPSHOT_KEY
    app, _, database = snapshot_app
    original = app.state.store
    add_source(original)
    # Reproduce a database populated by the previous deployment, before any
    # durable directory snapshot existed. No published records are changed.
    with original._open() as connection:
        for key in (INPUT_KEY, REGISTRY_KEY, REVISION_KEY, SNAPSHOT_KEY):
            connection.execute('DELETE FROM meta WHERE key=?', (key,))
    cold = create_app(database)
    store = cold.state.store
    statements = trace_queries(store, monkeypatch)
    with TestClient(cold) as visitor:
        assert visitor.get('/api/public').json()['listings'] == []
        assert not store._ready
        assert_no_public_rebuild(statements)
        assert not any(sql.lstrip().lower().startswith(('create ', 'insert ', 'update ', 'delete '))
                       for sql in statements), statements
        statements.clear()
        store.ensure_public_snapshot()
        assert store._ready
        assert sum('select * from observations' in sql.lower() for sql in statements) == 1
        assert sum('select * from mic_registry' in sql.lower() for sql in statements) == 1
        assert visitor.get('/api/public').json()['listings'][0]['name'] == 'Fixture mic'
        statements.clear()
        store.ensure_public_snapshot()
        assert_no_public_rebuild(statements)


def test_rolled_back_mutation_does_not_publish_a_snapshot(snapshot_app):
    app, client, database = snapshot_app
    store = app.state.store
    add_source(store)
    before = client.get('/api/public').json()
    with pytest.raises(RuntimeError, match='Abort the change'):
        with store.connect() as connection:
            connection.execute('INSERT INTO mic_flags VALUES (?,1)', (before['listings'][0]['id'],))
            raise RuntimeError('Abort the change')
    with TestClient(create_app(database)) as visitor:
        after = visitor.get('/api/public').json()
    assert after == before


def test_source_changes_update_snapshot_and_keep_other_sources(snapshot_app):
    app, client, database = snapshot_app
    store = app.state.store
    first_source = add_source(store)
    add_source(store, 'Independent mic', 'another')
    before = client.get('/api/public').json()
    before_id = next(row['id'] for row in before['listings'] if row['name'] == 'Fixture mic')
    store.success(first_source, normalized('Renamed fixture mic'), {})
    # A different instance must observe the new publication, including stable IDs.
    with TestClient(create_app(database)) as visitor:
        after = visitor.get('/api/public').json()
    assert after['revision'] != before['revision']
    assert {row['name'] for row in after['listings']} == {'Renamed fixture mic', 'Independent mic'}
    assert next(row['id'] for row in after['listings'] if row['name'] == 'Renamed fixture mic') == before_id


def test_sync_replays_changed_rows_without_downloading_historical_payloads(snapshot_app, monkeypatch):
    app, client, _ = snapshot_app
    store = app.state.store
    source_id = add_source(store)
    add_source(store, 'Independent historical mic', 'independent')
    before = client.get('/api/public').json()
    statements = trace_queries(store, monkeypatch)
    store.success(source_id, normalized('Updated existing mic') + normalized('Newly added mic', 'new'), {})
    after = client.get('/api/public').json()
    assert after['revision'] != before['revision']
    assert {row['name'] for row in after['listings']} == {
        'Updated existing mic', 'Newly added mic', 'Independent historical mic'}
    queries = [' '.join(sql.lower().split()) for sql in statements]
    assert not any('select * from observations' in sql or 'select payload from observations' in sql
                   or 'select * from mic_registry' in sql for sql in queries), queries
    registry_writes = [sql for sql in queries if sql.startswith('insert into mic_registry')]
    assert len(registry_writes) == 1, 'Only the newly added observation needs a registry write'
    statements.clear()
    assert client.get('/api/public').json() == after
    assert_no_public_rebuild(statements)


def test_materialized_cache_refreshes_after_another_instance_writes(snapshot_app, monkeypatch):
    app, client, database = snapshot_app
    first = app.state.store
    first_source = add_source(first)
    second = create_app(database).state.store
    second_source = add_source(second, 'Independent mic', 'independent')
    # The first process holds an older in-memory generation and must reload the
    # durable inputs before it can publish its own next source change.
    first.success(first_source, normalized('First process update'), {})
    public = client.get('/api/public').json()
    assert {row['name'] for row in public['listings']} == {'First process update', 'Independent mic'}
    second.success(second_source, normalized('Second process update', 'independent'), {})
    public = client.get('/api/public').json()
    assert {row['name'] for row in public['listings']} == {'First process update', 'Second process update'}
    first.success(first_source, normalized('First refreshed again'), {})
    assert {row['name'] for row in client.get('/api/public').json()['listings']} == {
        'First refreshed again', 'Second process update'}
    statements = trace_queries(first, monkeypatch)
    first.success(first_source, [], {}, unchanged=True)
    queries = [' '.join(sql.lower().split()) for sql in statements]
    assert not any(sql.startswith('select value from meta') and (
        'snapshot_observations' in sql or 'snapshot_registry' in sql) for sql in queries), queries
    assert not any('select * from observations' in sql or 'select * from mic_registry' in sql
                   for sql in queries), queries


def test_reader_returns_the_generation_it_validated_during_concurrent_cache_change():
    from app.public_snapshot import metadata, read
    current = {'revision': 'current', 'snapshot_at': 123, 'stale': False,
               'last_sync_at': 100, 'listings': [{'id': 'published'}]}
    older = {**current, 'revision': 'older', 'listings': [{'id': 'now-hidden'}]}

    class Connection:
        def execute(self, sql, args):
            return self

        def fetchone(self):
            return (json.dumps(metadata(current)),)

        def close(self):
            pass

    class ChangingCache:
        postgres = False
        reads = 0

        def _open(self):
            return Connection()

        @property
        def _public_snapshot(self):
            # Represent another thread installing a delayed older cache entry
            # after this reader has checked the current database generation.
            self.reads += 1
            return current if self.reads <= 2 else older

    assert read(ChangingCache()) == current


def test_replay_preserves_missing_rows_and_updates_successful_check_dates(snapshot_app):
    app, client, _ = snapshot_app
    store = app.state.store
    source_id = add_source(store)
    store.success(source_id, normalized() + normalized('Historical mic', 'historical'), {})
    before = {row['name']: row for row in client.get('/api/public').json()['listings']}
    store.success(source_id, normalized(), {})
    after = {row['name']: row for row in client.get('/api/public').json()['listings']}
    assert set(after) == set(before), 'A source omission retains the historical listing for review'
    assert after['Historical mic']['stale']
    assert after['Historical mic']['sources'][0]['missing_count'] == 1
    assert after['Historical mic']['checked_at'] == before['Historical mic']['checked_at']
    assert after['Fixture mic']['checked_at'] > before['Fixture mic']['checked_at']
    assert not after['Fixture mic']['stale']
    store.success(source_id, [], {}, unchanged=True)
    checked = {row['name']: row for row in client.get('/api/public').json()['listings']}
    assert checked['Historical mic']['checked_at'] == after['Historical mic']['checked_at']
    assert checked['Fixture mic']['checked_at'] > after['Fixture mic']['checked_at']


def test_observation_visibility_and_source_deletion_update_materialized_inputs(snapshot_app):
    app, client, database = snapshot_app
    store = app.state.store
    source_id = add_source(store)
    add_source(store, 'Independent mic', 'independent')
    mic = next(row for row in client.get('/api/public').json()['listings'] if row['name'] == 'Fixture mic')
    remote_key = mic['remote_key']
    assert client.patch(f'/api/listings/{source_id}/{remote_key}/visibility', json={'hidden': True}).status_code == 200
    assert {row['name'] for row in client.get('/api/public').json()['listings']} == {'Independent mic'}
    assert client.patch(f'/api/listings/{source_id}/{remote_key}/visibility', json={'hidden': False}).status_code == 200
    assert len(client.get('/api/public').json()['listings']) == 2
    assert client.delete('/api/sources/' + source_id).status_code == 200
    with TestClient(create_app(database)) as visitor:
        assert {row['name'] for row in visitor.get('/api/public').json()['listings']} == {'Independent mic'}


def test_admin_edits_visibility_and_restore_refresh_public_snapshot(snapshot_app):
    app, client, _ = snapshot_app
    add_source(app.state.store)
    before = client.get('/api/public').json()
    mic_id = before['listings'][0]['id']
    assert client.patch('/api/admin/mics/' + mic_id, json={'fields': {'notes': 'Public host instructions'}}).status_code == 200
    edited = client.get('/api/public').json()
    assert edited['revision'] != before['revision']
    assert edited['listings'][0]['notes'] == 'Public host instructions'
    assert client.patch('/api/admin/mics/' + mic_id + '/visibility', json={'hidden': True}).status_code == 200
    hidden = client.get('/api/public').json()
    assert hidden['revision'] != edited['revision'] and hidden['listings'] == []
    assert client.get('/mics/' + mic_id).status_code == 404
    private = client.get('/api/admin/community')
    assert private.status_code == 200
    assert private.json()['listings'][0]['hidden']
    assert private.headers['cache-control'] == 'no-store'
    assert client.patch('/api/admin/mics/' + mic_id + '/visibility', json={'hidden': False}).status_code == 200
    assert client.post('/api/admin/mics/' + mic_id + '/restore').status_code == 200
    restored = client.get('/api/public').json()
    assert restored['listings'][0]['notes'] != 'Public host instructions'
    assert not restored['listings'][0]['hidden']


def test_public_snapshot_never_contains_moderation_or_account_secrets(snapshot_app):
    app, client, _ = snapshot_app
    add_source(app.state.store)
    before = client.get('/api/public').json()
    mic = before['listings'][0]
    response = client.post('/api/mics/' + mic['id'] + '/submissions', json={
        'kind': 'claim', 'name': 'Private Claimant', 'email': 'private-claim@example.com',
        'message': 'Private verification details should remain in the moderation queue.',
    })
    assert response.status_code == 201
    public = client.get('/api/public')
    assert 'private-claim@example.com' not in public.text
    assert 'Private Claimant' not in public.text
    assert 'Private verification details' not in public.text
    assert public.json()['revision'] == before['revision'], 'Private submissions must not rebuild the public directory'
    assert not {'submissions', 'ownership', 'owners', 'password_hash', 'token_hash'} & public.json().keys()
    assert client.get('/api/admin/community').headers['cache-control'] == 'no-store'
    assert client.get('/api/session').headers['cache-control'] == 'no-store'


def test_owner_edit_and_revocation_publish_across_instances(snapshot_app):
    app, admin, database = snapshot_app
    add_source(app.state.store)
    mic_id = admin.get('/api/public').json()['listings'][0]['id']
    with TestClient(app, headers=HEADERS) as host:
        assert host.post('/api/mics/' + mic_id + '/submissions', json={
            'kind': 'claim', 'name': 'Private Host', 'email': 'host@example.com',
            'message': 'I run this mic and can verify it with the venue.',
        }).status_code == 201
        claim = admin.get('/api/admin/community').json()['submissions'][0]
        approved = admin.post('/api/admin/submissions/' + claim['id'], json={'action': 'approve'})
        assert approved.status_code == 200
        token = approved.json()['invite_path'].split('#')[1]
        redeemed = host.post('/api/owner/redeem', json={'token': token, 'password': 'A sufficiently long host password'})
        assert redeemed.status_code == 200
        assert host.patch('/api/owner/mics/' + mic_id, json={'fields': {'notes': 'Approved public host edit'}}).status_code == 200
        with TestClient(create_app(database)) as visitor:
            claimed = visitor.get('/api/public').json()
            assert claimed['listings'][0]['claimed']
            assert claimed['listings'][0]['notes'] == 'Approved public host edit'
            assert claimed['listings'][0]['host_confirmed_at']
            assert admin.delete('/api/admin/mics/' + mic_id + '/owner').status_code == 200
            revoked = visitor.get('/api/public').json()
            assert revoked['revision'] != claimed['revision']
            assert not revoked['listings'][0]['claimed']
            assert revoked['listings'][0]['host_confirmed_at'] is None
        assert host.get('/api/owner/me').status_code == 401
        assert host.patch('/api/owner/mics/' + mic_id, json={'fields': {'notes': 'Must be rejected'}}).status_code == 401


def test_database_failure_keeps_dated_public_snapshot(snapshot_app, monkeypatch, tmp_path):
    from app import public_snapshot
    app, client, _ = snapshot_app
    store = app.state.store
    add_source(store)
    published = client.get('/api/public').json()
    fallback_path = tmp_path / 'fallback.json.gz'
    fallback_path.write_bytes(gzip.compress(json.dumps(published).encode()))
    monkeypatch.setattr(public_snapshot, 'FALLBACK_PATH', fallback_path)

    def unavailable():
        raise OSError('Database unavailable for this test')

    # A cold serverless instance has only its bundled public snapshot to fall back to.
    cold = create_app('postgresql://unreachable.example/fixture')
    monkeypatch.setattr(cold.state.store, '_open', unavailable)
    with TestClient(cold) as visitor:
        response = visitor.get('/api/public')
        assert response.status_code == 200
        fallback = response.json()
        assert [{k: v for k, v in row.items() if k != 'stale'} for row in fallback['listings']] == [
            {k: v for k, v in row.items() if k != 'stale'} for row in published['listings']]
        assert fallback['snapshot_at'] == published['snapshot_at']
        assert fallback['stale'] is True
        assert visitor.get('/api/public/revision').json()['stale'] is True
        assert visitor.get('/mics/' + published['listings'][0]['id']).status_code == 200
        assert 'Database unavailable for this test' not in response.text
        with pytest.raises(OSError, match='Database unavailable for this test'):
            cold.state.store.public_data(include_hidden=True)


@pytest.mark.parametrize('corruption', ['hidden', 'nested_private', 'top_private', 'missing_date'])
def test_invalid_fallback_is_rejected_without_exposing_private_data(snapshot_app, monkeypatch, tmp_path, corruption):
    from app import public_snapshot
    app, client, _ = snapshot_app
    add_source(app.state.store)
    data = client.get('/api/public/snapshot').json()
    if corruption == 'hidden':
        data['listings'][0]['hidden'] = True
    elif corruption == 'nested_private':
        data['listings'][0]['sources'][0]['email'] = 'must-not-be-published@example.com'
    elif corruption == 'top_private':
        data['owners'] = [{'password_hash': 'must-not-be-published'}]
    else:
        del data['snapshot_at']
    path = tmp_path / 'invalid-fallback.json.gz'
    path.write_bytes(gzip.compress(json.dumps(data).encode()))
    monkeypatch.setattr(public_snapshot, 'FALLBACK_PATH', path)
    cold = create_app('postgresql://unreachable.example/fixture')
    with pytest.raises(ValueError, match='Invalid public fallback snapshot'):
        public_snapshot.fallback(cold.state.store)
