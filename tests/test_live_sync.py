import asyncio
import json

import pytest

from app.comediq import FEED_URL
from app.main import create_app
from app.models import SourceConfig
from app.parsers import normalize_rows
from app.store import Store, digest

BADSLAVA_URL = 'https://badslava.com/open-mics-state.php?state=NY&type=Comedy'


def mic(**changes):
    return {'external_id': 'badslava:1:2026-09-30', 'name': 'Open Mic', 'venue': "Producer's Club",
            'address': '358 West 44th Street', 'borough': 'Manhattan', 'date': '2026-09-30',
            'start_time': '17:00', **changes}


def comediq_mic(**changes):
    return mic(external_id='comediq:00de8153-b986-40f3-8080-16fab9905b77:2026-09-30',
               name='New Material Mic', display_name='New Material Mic', venue='Producers Club Theater',
               address='358 W. 44 St.', host_names='Mic Host', **changes)


def parsed(raw, config):
    rows, warnings, skipped = normalize_rows(raw, config)
    assert skipped == 0, warnings
    return {'rows': rows, 'warnings': warnings, 'skipped': skipped}


def add_source(store, url, raw, validators=None):
    config = SourceConfig(name='Test calendar', url=url, kind='json', permission_confirmed=True)
    return store.commit_preview(store.preview(config.model_dump(), parsed(raw, config), validators=validators))


@pytest.mark.parametrize('first', ['badslava', 'comediq'])
def test_matching_comediq_calendar_session_merges_and_preserves_known_id(database, first):
    store = Store(database)
    sources = {'badslava': (BADSLAVA_URL, [mic()]), 'comediq': (FEED_URL, [comediq_mic()])}
    first_url, first_rows = sources[first]
    add_source(store, first_url, first_rows)
    original, = store.public_data()['listings']
    other_url, other_rows = sources['comediq' if first == 'badslava' else 'badslava']
    add_source(store, other_url, other_rows)
    merged, = store.public_data()['listings']
    assert merged['id'] == original['id']
    assert merged['name'] == 'New Material Mic' and merged['host_names'] == 'Mic Host'
    assert len(merged['sources']) == 2
    assert store.public_data()['listings'][0]['id'] == original['id']


@pytest.mark.parametrize('changes', [
    {'start_time': '18:00'}, {'address': '360 W 44 Street'},
    {'borough': 'Brooklyn'}, {'date': '2026-10-01'},
])
def test_different_slots_are_not_merged(database, changes):
    store = Store(database)
    add_source(store, BADSLAVA_URL, [mic()])
    add_source(store, FEED_URL, [{**comediq_mic(), **changes}])
    published = store.public_data()['listings']
    assert len(published) == 2
    assert all(len(r['sources']) == 1 for r in published)


@pytest.mark.parametrize('changes', [{'start_time': '18:00'}, {'address': '360 W 44 Street'}])
def test_generic_comediq_names_do_not_bypass_exact_slot_matching(database, changes):
    store = Store(database)
    add_source(store, BADSLAVA_URL, [mic()])
    imported = {**comediq_mic(), 'name': 'Open Mic', 'display_name': 'Open Mic',
                'venue': "Producer's Club", **changes}
    add_source(store, FEED_URL, [imported])
    assert len(store.public_data()['listings']) == 2


def test_ambiguous_venues_at_the_same_slot_are_not_merged(database):
    store = Store(database)
    add_source(store, BADSLAVA_URL, [mic(venue='Upstairs Room'),
                                    mic(external_id='badslava:2:2026-09-30', venue='Downstairs Room')])
    original_ids = {r['id'] for r in store.public_data()['listings']}
    add_source(store, FEED_URL, [comediq_mic()])
    published = store.public_data()['listings']
    assert len(published) == 3 and all(len(r['sources']) == 1 for r in published)
    assert original_ids <= {r['id'] for r in published}


@pytest.mark.parametrize('initial_status', [200, 304])
def test_identical_comediq_feed_is_reparsed_to_advance_occurrence_dates(database, monkeypatch, initial_status):
    import app.main as main_module

    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    app = create_app(database)
    store = app.state.store
    content = b'[{"unchanged":"public weekly schedule"}]'
    sid = add_source(store, FEED_URL, [comediq_mic()],
                     validators={'hash': digest(content), 'etag': 'same-etag', 'modified': 'same-modified'})
    calls, parse_calls = [], []

    async def fetch(url, etag=None, modified=None):
        calls.append((url, etag, modified))
        return {'status': initial_status if len(calls) == 1 else 200,
                'content': content, 'headers': {'Content-Type': 'application/json', 'ETag': 'same-etag'}}

    def extract(content_received, config, content_type):
        parse_calls.append(content_received)
        assert content_received == content
        # The unchanged weekly feed is interpreted in the following week's window.
        return parsed([{**comediq_mic(), 'date': '2026-10-07',
                        'external_id': 'comediq:00de8153-b986-40f3-8080-16fab9905b77:2026-10-07'}], config)

    monkeypatch.setattr(app.state.fetcher, 'fetch', fetch)
    monkeypatch.setattr(main_module, 'extract', extract)
    result = asyncio.run(app.state.sync_one(sid))
    assert result['state'] == 'updated' and result['changed'] == 1
    assert parse_calls == [content]
    assert calls == [(FEED_URL, None, None)] * (2 if initial_status == 304 else 1)
    with store.connect() as c:
        observations = c.execute('SELECT payload,missing_count FROM observations WHERE source_id=?', (sid,)).fetchall()
    assert {json.loads(r['payload'])['date']: r['missing_count'] for r in observations} == {
        '2026-09-30': 1, '2026-10-07': 0}
