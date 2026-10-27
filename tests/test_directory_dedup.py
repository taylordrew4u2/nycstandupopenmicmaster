"""Consolidate generated weekly directory rows without merging stored targets."""
from copy import deepcopy
import secrets

from fastapi.testclient import TestClient
import pytest

from app.comediq import FEED_URL
from app.main import create_app
from app.models import SourceConfig
from app.parsers import normalize_rows
from app.store import deduplicate_generated_occurrences, deduplicate_laughingbuddha_occurrences


UID = '11111111-1111-4111-8111-111111111111'


def row(event_date='2026-10-20', missing=1, **fields):
    return {'id': 'mic-' + event_date, 'external_id': f'comediq:{UID}:{event_date}',
            'name': 'Example Mic', 'venue': 'Example Room', 'address': '1 Broadway',
            'borough': 'Manhattan', 'date': event_date, 'weekday': None,
            'start_time': '19:00', 'frequency': '', 'recurrence_anchor': None,
            'excluded_dates': [], 'status': 'scheduled', 'claimed': False,
            'curated': False, 'hidden': False, 'overridden_fields': [],
            'checked_at': 100 if missing else 200, 'stale': bool(missing),
            'sources': [{'id': 'comediq-source', 'name': 'Comediq', 'url': FEED_URL,
                         'kind': 'auto', 'missing_count': missing,
                         'checked_at': 100 if missing else 200}], **fields}


def test_rollover_has_one_directory_row_with_current_id_and_historical_provenance():
    old = row(name='Previous provider title')
    old['sources'].append({'id': 'other-source', 'name': 'Other calendar',
                           'url': 'https://example.org/calendar', 'kind': 'auto',
                           'missing_count': 2, 'checked_at': 90})
    current = row('2026-10-27', 0, name='Current provider title')
    original = deepcopy([old, current])
    result = deduplicate_generated_occurrences(original)
    assert len(result) == 1
    chosen = result[0]
    assert chosen['id'] == current['id'] and chosen['date'] == current['date']
    assert chosen['name'] == 'Current provider title' and not chosen['stale']
    assert chosen['duplicate_ids'] == [old['id']]
    assert chosen['recurring_history'] == [{'id': old['id'], 'date': old['date'], 'sources': old['sources']}]
    assert {source['id'] for source in chosen['sources']} == {'comediq-source', 'other-source'}
    assert next(source for source in chosen['sources'] if source['id'] == 'comediq-source')['missing_count'] == 0
    assert original == [old, current]  # Public presentation never edits its inputs.
    assert deduplicate_generated_occurrences(result) == result


def test_removed_series_keeps_its_latest_stale_representative():
    old, latest = row(), row('2026-10-27', 3)
    latest['sources'][0]['checked_at'] = latest['checked_at'] = 150
    result = deduplicate_generated_occurrences([old, latest])
    assert len(result) == 1 and result[0]['id'] == latest['id']
    assert result[0]['stale'] and result[0]['duplicate_ids'] == [old['id']]


def test_street_suffixes_and_locality_formatting_do_not_duplicate_the_same_provider_session():
    old = row(address='156 West 29th Street, New York, NY')
    current = row('2026-10-27', 0, address='156 W 29 St')
    assert len(deduplicate_generated_occurrences([old, current])) == 1


def test_provider_id_in_provenance_is_used_when_another_source_supplies_the_title():
    old, current = row(), row('2026-10-27', 0)
    old['sources'][0]['external_id'] = old['external_id']
    old['external_id'] = 'official-venue-event-id'
    assert len(deduplicate_generated_occurrences([old, current])) == 1


@pytest.mark.parametrize('fields', [
    {'start_time': '20:00'}, {'weekday': 2}, {'venue': 'A different room'},
    {'address': '2 Broadway'}, {'borough': 'Queens'},
    {'excluded_dates': ['2026-11-03']}, {'status': 'cancelled'},
    {'frequency': 'one-time'}, {'frequency': 'biweekly'}, {'frequency': 'non-weekly'},
    {'recurrence_anchor': '2026-10-20'}, {'claimed': True}, {'curated': True},
    {'hidden': True}, {'overridden_fields': ['date']},
    {'external_id': 'comediq:22222222-2222-4222-8222-222222222222:2026-10-20'},
    {'external_id': f'comediq:{UID}:2026-10-19'}, {'date': None},
])
def test_distinct_sessions_schedules_and_protected_ids_are_preserved(fields):
    old, current = row(**fields), row('2026-10-27', 0)
    assert deduplicate_generated_occurrences([old, current]) == [old, current]


def test_real_date_confirmed_by_another_live_source_is_preserved():
    old, current = row(), row('2026-10-27', 0)
    old['sources'].append({'id': 'venue-calendar', 'url': 'https://example.org/calendar',
                           'kind': 'auto', 'missing_count': 0})
    assert deduplicate_generated_occurrences([old, current]) == [old, current]


@pytest.mark.parametrize('source_change', [
    {'url': 'https://example.org/mics.json'}, {'kind': 'upload'}, {'id': 'other-source'},
])
def test_provider_looking_ids_from_other_authorities_do_not_merge(source_change):
    old, current = row(), row('2026-10-27', 0)
    old['sources'][0].update(source_change)
    assert deduplicate_generated_occurrences([old, current]) == [old, current]


def test_ambiguous_active_dates_and_provider_observations_stay_separate():
    old, current = row(missing=0), row('2026-10-27', 0)
    assert deduplicate_generated_occurrences([old, current]) == [old, current]
    old = row()
    old['sources'].append(dict(old['sources'][0]))
    assert deduplicate_generated_occurrences([old, current]) == [old, current]


def official_buddha(event_date='2026-10-27', **fields):
    result = row(event_date, 0, id='official-' + event_date,
               external_id=f'laughingbuddha:2124933:{event_date}:1900',
               name='Laughing Buddha 1 Hour Mic', venue='The Buddha Room',
               address='410 8th Ave, 2nd Floor New York, NY 10001',
               frequency='one-time', latitude=None, longitude=None,
               sources=[{'id': 'buddha-source', 'name': 'Laughing Buddha official',
                         'url': 'https://www.laughingbuddhacomedy.com/mics',
                         'kind': 'auto', 'missing_count': 0, 'checked_at': 250}])
    return {**result, **fields}


def test_official_buddha_anchor_consolidates_exact_organizer_event_and_retains_map_and_history():
    old = row(venue='The Buddha Room', address='410 8th Ave')
    current = row('2026-10-27', 0, venue='The Buddha Room', address='410 8th Ave',
                  latitude=40.75, longitude=-73.99, map_status='source', host_names='Public Host')
    generic = row('2026-10-27', 0, id='badslava-mic', name='Open Mic',
                  venue='Laughing Buddha Comedy', address='410 8th Ave 2nd floor',
                  external_id='badslava:12345:2026-10-27',
                  sources=[{'id': 'badslava-source', 'url': 'https://badslava.com/open-mics-state.php?state=NY&type=Comedy',
                            'kind': 'auto', 'missing_count': 0}])
    official = official_buddha()
    original = deepcopy([old, current, generic, official])
    result = deduplicate_laughingbuddha_occurrences(deduplicate_generated_occurrences(original))
    assert len(result) == 1
    chosen = result[0]
    assert chosen['id'] == official['id'] and chosen['frequency'] == 'one-time'
    assert chosen['name'] == official['name']
    assert chosen['latitude'] == 40.75 and chosen['longitude'] == -73.99
    assert chosen['host_names'] == 'Public Host'
    assert set(chosen['duplicate_ids']) == {old['id'], current['id'], generic['id']}
    assert {source['id'] for source in chosen['sources']} == {'comediq-source', 'badslava-source', 'buddha-source'}
    assert {record['id'] for record in chosen['recurring_history']} == {old['id'], current['id'], generic['id']}
    assert original == [old, current, generic, official]
    assert deduplicate_laughingbuddha_occurrences(result) == result


@pytest.mark.parametrize('fields', [
    {'start_time': '20:00'}, {'date': '2026-10-28'}, {'address': '411 8th Ave'},
    {'venue': 'Different Room'}, {'status': 'cancelled'}, {'claimed': True},
    {'curated': True}, {'hidden': True}, {'excluded_dates': ['2026-11-03']},
    {'recurrence_anchor': '2026-10-27'},
])
def test_buddha_consolidation_preserves_other_sessions_dates_and_protected_records(fields):
    existing = row('2026-10-27', 0, venue='The Buddha Room', address='410 8th Ave')
    existing.update(fields)
    official = official_buddha()
    assert deduplicate_laughingbuddha_occurrences([existing, official]) == [existing, official]


def test_shared_venue_requires_explicit_organizer_and_future_official_dates_remain_independent():
    existing = row('2026-10-27', 0, name='Unrelated named mic', venue='The Stand', address='116 E 16th St')
    official = official_buddha(venue='The Stand', address='116 E 16th St New York, NY 10003')
    assert deduplicate_laughingbuddha_occurrences([existing, official]) == [existing, official]
    existing['name'] = 'Buddha 7 @The Stand'
    future = official_buddha('2026-11-03')
    result = deduplicate_laughingbuddha_occurrences([existing, official, future])
    assert len(result) == 2 and {record['date'] for record in result} == {'2026-10-27', '2026-11-03'}


def test_two_named_provider_sessions_at_same_room_and_time_are_not_guessed_to_be_one():
    first = row('2026-10-27', 0, venue='The Buddha Room', address='410 8th Ave')
    second = {**first, 'id': 'separate-mic', 'name': 'Separate Buddha Mic',
              'external_id': 'comediq:22222222-2222-4222-8222-222222222222:2026-10-27'}
    official = official_buddha()
    assert deduplicate_laughingbuddha_occurrences([first, second, official]) == [first, second, official]


def private_buddha_pair():
    feed = row('2026-10-27', 0, name='4:30 Buddha Room Hour Mic',
               venue='The Buddha Room', address='410 8th Ave', start_time='16:30')
    calendar = row('2026-10-27', 0, id='badslava-mic', name='Open Mic',
                   venue='Laughing Buddha Comedy', address='410 8th Ave 2nd floor', start_time='16:30',
                   external_id='badslava:46346:2026-10-27',
                   sources=[{'id': 'badslava-source', 'url': 'https://badslava.com/open-mics-state.php?state=NY&type=Comedy',
                             'kind': 'auto', 'missing_count': 0, 'priority': 50}])
    return feed, calendar


def test_private_buddha_room_pair_consolidates_without_an_official_dated_anchor():
    feed, calendar = private_buddha_pair()
    feed['duplicate_ids'] = ['prior-feed-id']
    feed['recurring_history'] = [{'id': 'prior-feed-id', 'date': '2026-10-20', 'sources': feed['sources']}]
    original = deepcopy([feed, calendar])
    result = deduplicate_laughingbuddha_occurrences(original)
    assert len(result) == 1 and result[0]['id'] == feed['id']
    assert result[0]['name'] == '4:30 Buddha Room Hour Mic'
    assert set(result[0]['duplicate_ids']) == {'prior-feed-id', calendar['id']}
    assert {source['id'] for source in result[0]['sources']} == {'comediq-source', 'badslava-source'}
    assert {record['id'] for record in result[0]['recurring_history']} == {'prior-feed-id', calendar['id']}
    assert original == [feed, calendar]


@pytest.mark.parametrize('fields', [
    {'start_time': '17:30'}, {'date': '2026-10-28'}, {'address': '411 8th Ave'},
    {'claimed': True}, {'curated': True}, {'hidden': True}, {'status': 'cancelled'},
    {'frequency': 'biweekly'}, {'frequency': 'non-weekly'}, {'frequency': 'one-time'},
    {'notes': 'Every other Tuesday'}, {'recurrence_anchor': '2026-10-27'},
    {'excluded_dates': ['2026-11-03']}, {'overridden_fields': ['frequency']},
])
def test_private_buddha_pair_preserves_other_dates_schedules_and_protected_records(fields):
    feed, calendar = private_buddha_pair()
    feed.update(fields)
    assert deduplicate_laughingbuddha_occurrences([feed, calendar]) == [feed, calendar]


def test_private_buddha_pair_requires_current_unambiguous_provider_records():
    feed, calendar = private_buddha_pair()
    feed['sources'][0]['missing_count'] = 1
    assert deduplicate_laughingbuddha_occurrences([feed, calendar]) == [feed, calendar]
    feed['sources'][0]['missing_count'] = 0
    second = {**feed, 'id': 'second-feed-session', 'name': 'Separate Buddha session'}
    assert deduplicate_laughingbuddha_occurrences([feed, calendar, second]) == [feed, calendar, second]
    protected = {**second, 'claimed': True}
    assert deduplicate_laughingbuddha_occurrences([feed, calendar, protected]) == [feed, calendar, protected]


@pytest.mark.parametrize('venue,address', [
    ('The Stand', '116 E 16th St'), ('New York Comedy Club - UWS', '236 W 78th St'),
    ('The Rose', '160 W 25th St'),
])
def test_shared_rooms_still_require_an_official_anchor(venue, address):
    feed, calendar = private_buddha_pair()
    for record in (feed, calendar):
        record['venue'], record['address'] = venue, address
    assert deduplicate_laughingbuddha_occurrences([feed, calendar]) == [feed, calendar]


def test_only_directory_api_consolidates_and_management_and_dated_urls_remain(database, monkeypatch):
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv('ADMIN_PASSWORD', password)
    monkeypatch.setenv('LOCAL_DEV', '1')
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    app = create_app(database)
    config = SourceConfig(name='Comediq fixture', url=FEED_URL, permission_confirmed=True)

    def imported(event_date):
        rows, warnings, skipped = normalize_rows([{
            'external_id': f'comediq:{UID}:{event_date}', 'name': 'Example Mic',
            'venue': 'Example Room', 'address': '1 Broadway', 'borough': 'Manhattan',
            'date': event_date, 'start_time': '19:00',
        }], config)
        assert not warnings and not skipped
        return rows

    with TestClient(app, headers={'X-Requested-With': 'MicList'}) as client:
        store = app.state.store
        source_id = store.commit_preview(store.preview(config.model_dump(), {
            'rows': imported('2026-10-20'), 'warnings': [], 'skipped': 0,
        }))
        old_id = store.public_data()['listings'][0]['id']
        store.success(source_id, imported('2026-10-27'), {})
        full = store.public_data()['listings']
        assert len(full) == 2

        def records():
            with store.connect() as connection:
                return {table: [tuple(record) for record in connection.execute('SELECT * FROM ' + table)]
                        for table in ('observations', 'mic_registry', 'ownership', 'overlays', 'mic_flags')}

        before = records()
        directory = client.get('/api/public').json()['listings']
        assert len(directory) == 1 and directory[0]['duplicate_ids'] == [old_id]
        assert directory[0]['date'] == '2026-10-27'
        assert directory[0]['sources'][0]['external_id'] == f'comediq:{UID}:2026-10-27'
        assert directory[0]['recurring_history'][0]['sources'][0]['external_id'] == f'comediq:{UID}:2026-10-20'
        assert records() == before
        assert len(store.public_data()['listings']) == 2
        assert client.get('/mics/' + old_id).status_code == 200
        assert client.post('/api/login', json={'password': password}).status_code == 200
        assert len(client.get('/api/admin/community').json()['listings']) == 2
