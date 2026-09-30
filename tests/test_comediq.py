import json
from datetime import date, datetime, timezone

import pytest

from app.comediq import FEED_URL, extract_feed, supports
from app.models import SourceConfig
from app.parsers import extract


def row(**changes):
    return {'id': '00de8153-b986-40f3-8080-16fab9905b77', 'openMic': 'A Weekly Mic',
            'day': 'Wednesday', 'startTime': '5:00 PM', 'venueName': 'A Comedy Room',
            'borough': 'Manhattan', 'location': '358 W 44th St, New York, NY 10036, USA',
            'city': 'New York', 'frequency': 'weekly', 'status': 'verified',
            'cost': '$5 cash + 1 item', 'stageTime': '5 minutes', 'hosts': 'A Public Host',
            'instagramHandle': '@publicmic', 'otherRules': 'A house rule that should never be copied.',
            'signUpInstructions': 'A description that should never be copied.',
            'latitude': 40.75955, 'longitude': -73.99150, 'creatorId': 'private-irrelevant-id', **changes}


def config(kind='auto', url=FEED_URL):
    return SourceConfig(name='Comediq', url=url, kind=kind, permission_confirmed=True)


def parse(rows, today=date(2026, 9, 30)):
    return extract_feed(json.dumps(rows).encode(), config(), today=today)


def test_public_fields_dates_fees_and_source_link():
    result = parse([row()])
    assert result['skipped'] == result['excluded'] == 0
    mic, = result['rows']
    assert mic['date'] == '2026-09-30' and mic['start_time'] == '17:00'
    assert mic['external_id'] == 'comediq:00de8153-b986-40f3-8080-16fab9905b77:2026-09-30'
    assert mic['name'] == mic['display_name'] == 'A Weekly Mic'
    assert mic['host_names'] == 'A Public Host'
    assert mic['host_socials'] == 'https://www.instagram.com/publicmic/'
    assert mic['cost'] == 5 and mic['cost_text'] == '$5 cash + 1 item' and mic['set_minutes'] == 5
    assert mic['address'] == '358 W 44th St'
    assert (mic['latitude'], mic['longitude']) == (40.75955, -73.99150)
    assert mic['signup_url'] == 'https://comediq.us/open-mics#00de8153-b986-40f3-8080-16fab9905b77'
    assert not any(word in json.dumps(mic) for word in ('house rule', 'description that', 'private-irrelevant-id', 'creatorId', 'claimed'))


def test_rolling_dates_follow_new_york_not_utc_and_preserve_same_date_ids():
    utc_midnight = datetime(2026, 9, 30, 1, tzinfo=timezone.utc)  # Still Tuesday in New York.
    first, = parse([row(day='Tuesday')], today=utc_midnight)['rows']
    assert first['date'] == '2026-09-29'
    later, = parse([row(day='Tuesday')], today=date(2026, 9, 30))['rows']
    assert later['date'] == '2026-10-06' and later['remote_key'] != first['remote_key']
    changed, = parse([row(day='Tuesday', startTime='6:00 PM')], today=utc_midnight)['rows']
    assert changed['remote_key'] == first['remote_key']


@pytest.mark.parametrize('changes', [
    {'city': 'Los Angeles'}, {'frequency': 'bi_weekly'}, {'frequency': '1st_of_month'},
    {'frequency': 'custom'}, {'openMic': 'PIT Improv Jam'}, {'openMic': 'Poetry Mic'},
    {'openMic': '*Show Dependent* Sunday Mic'}, {'openMic': 'A Mic *Skips 9/14*'},
    {'openMic': 'A Mic *Final: 9/16 & 10/7*'}, {'status': 'cancelled'},
    {'otherRules': 'The mic is canceled every 4th Tuesday.'},
    {'frequencyCustomText': 'Active in August except off August 7.'},
    {'otherRules': 'Closed on 2026-10-01.'}, {'venueName': ''}, {'borough': ''},
    {'location': 'A room somewhere in NYC'}, {'latitude': 34.01, 'longitude': -118.41},
])
def test_intentional_exclusions_do_not_block_sync(changes):
    result = parse([row(**changes)])
    assert result['rows'] == [] and result['skipped'] == 0 and result['excluded'] == 1


@pytest.mark.parametrize('changes', [
    {'id': 'not-a-uuid'}, {'day': 'Sometimes'}, {'day': 'Wednesday Thursday'}, {'startTime': '7'},
    {'latitude': 'broken'}, {'latitude': None, 'longitude': -73.99}, {'latitude': float('nan')},
    {'hosts': ['unexpected', 'array']}, {'status': 'unrecognized-status'},
])
def test_malformed_relevant_rows_require_review(changes):
    result = parse([row(**changes)])
    assert result['rows'] == [] and result['skipped'] == 1 and result['excluded'] == 0
    assert result['warnings']


def test_feed_schema_changes_and_duplicate_ids_require_review():
    malformed = row()
    del malformed['startTime']
    assert parse([malformed])['skipped'] == 1
    assert parse([{'openMic': 'Missing city'}])['skipped'] == 1
    assert parse([row(), row()])['skipped'] == 2
    with pytest.raises(ValueError, match='nonempty array'):
        parse([])
    with pytest.raises(ValueError, match='nonempty array'):
        parse({'mics': [row()]})


def test_ambiguous_optional_fields_remain_unknown_and_links_are_safe():
    mic, = parse([row(cost='$5 for some people, $8 for others', stageTime='5-7 min',
                      instagramHandle='not-a-handle@example.com', signupUrl='javascript:alert(1)',
                      latitude=None, longitude=None)])['rows']
    assert mic['cost'] is None and mic['set_minutes'] is None
    assert mic['host_socials'] == '' and mic['signup_url'].startswith('https://comediq.us/open-mics#')
    assert mic['latitude'] is None and mic['longitude'] is None
    safe, = parse([row(signupUrl='https://venue.example/signup', cost='Free',
                       instagramHandle='https://instagram.com/publicmic/')])['rows']
    assert safe['signup_url'] == 'https://venue.example/signup' and safe['cost'] == 0
    assert safe['host_socials'] == 'https://www.instagram.com/publicmic/'


@pytest.mark.parametrize('address,expected', [
    ('156 W. 29th Street, Floor 2, New York, NY, 10001', '156 W. 29th Street'),
    ('750A St Nicholas Ave, New York, NY 10031, USA', '750A St Nicholas Ave'),
    ('inside Ange Noir Cafe, 247 Varet St, Brooklyn, NY 11206, USA', '247 Varet St'),
    ('27-16 23rd Ave Queens NY', '27-16 23rd Ave'),
])
def test_explicit_street_addresses_with_unit_letters_and_venue_prefixes(address, expected):
    mic, = parse([row(location=address)])['rows']
    assert mic['address'] == expected


def test_exact_public_feed_is_detected_without_affecting_other_json_feeds():
    for kind in ('auto', 'json'):
        result = extract(json.dumps([row()]).encode(), config(kind), 'application/json')
        assert result['detected_kind'] == 'comediq' and len(result['rows']) == 1
    for url in ('https://other.example/mics.json', FEED_URL + '?other=1', FEED_URL + '/extra', 'http://comediq.us/mics.json'):
        assert not supports(url)
    ordinary = {'name': 'Other Feed Mic', 'venue': 'Room', 'borough': 'Queens', 'weekday': 'Friday', 'start_time': '19:00'}
    result = extract(json.dumps([ordinary]).encode(), config('json', 'https://other.example/mics.json'), 'application/json')
    assert result['detected_kind'] == 'json' and result['rows'][0]['date'] is None
