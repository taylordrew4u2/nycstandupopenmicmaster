import asyncio
import json
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.laughingbuddha import (BOX_OFFICE, VENUES_URL, calendar_rows,
                               event_links, fetch_feed, supports)
from app.main import create_app
from app.models import SourceConfig
from app.parsers import extract
from app.store import digest

FIXTURES = Path(__file__).parent / 'fixtures' / 'laughingbuddha'
SOURCE = 'https://www.laughingbuddhacomedy.com/mics'
TODAY = date(2026, 10, 27)


def fixture(name):
    return (FIXTURES / name).read_bytes()


def venue_text():
    return BeautifulSoup(fixture('venues.html'), 'html.parser').get_text(' ', strip=True)


def source_config():
    return SourceConfig(name='Laughing Buddha Official', url=SOURCE,
                        priority=100, permission_confirmed=True)


def test_source_match_and_index_exclude_online_workshops_and_regular_shows():
    assert supports(SOURCE) and supports(SOURCE + '/')
    assert not supports('https://other.example/mics')
    assert not supports(SOURCE + '/other')
    links = event_links(fixture('index.html'))
    assert len(links) == 5
    assert all(url.startswith(BOX_OFFICE + '/') for url in links)
    assert len(links) == len(set(links))
    changed = b'<h3 class="event__title"><a href="https://other.example/mic">Open Mic</a></h3>'
    with pytest.raises(ValueError, match='review'):
        event_links(changed)
    with pytest.raises(ValueError, match='review'):
        event_links(b'<p>Buy tickets for our shows</p>')


def test_explicit_dates_prices_and_addresses_from_real_registration_pages():
    raw = calendar_rows(fixture('2124933.html'), BOX_OFFICE + '/2124933', venue_text(), TODAY)
    assert [(r['date'], r['start_time']) for r in raw] == [('2026-10-27', '17:30'), ('2026-11-03', '17:30')]
    assert all(r['frequency'] == 'one-time' and r['venue'] == 'The Buddha Room' for r in raw)
    assert raw[0]['address'] == '410 8th Ave, 2nd Floor New York, NY 10001'
    assert raw[0]['cost'] == '$7.75'
    assert raw[0]['purchase_minimum'] == '1 item minimum'
    assert raw[0]['set_minutes'] == '5'
    assert raw[0]['name'] == 'Laughing Buddha 1 Hour Mic'
    result = extract(json.dumps(raw).encode(), source_config())
    assert result['skipped'] == 0 and not result['warnings']
    assert len({r['remote_key'] for r in result['rows']}) == 2
    assert result['rows'][0]['cost'] == 7.75


def test_festival_timestamp_overrides_static_series_title_without_weekly_inference():
    rows = calendar_rows(fixture('2228278.html'), BOX_OFFICE + '/2228278', venue_text(), TODAY)
    assert [(r['date'], r['start_time']) for r in rows] == [('2026-10-31', '20:00'), ('2026-11-07', '21:45')]
    assert all(r['name'] == 'Laughing Buddha Mic Show' for r in rows)
    assert all('9:45' not in r['name'] and '9:45' in r['notes'] for r in rows)
    later = calendar_rows(fixture('2228278.html'), BOX_OFFICE + '/2228278', venue_text(), date(2026, 11, 1))
    assert [r['date'] for r in later] == ['2026-11-07']


def test_date_identity_survives_time_edits_without_collapsing_multiple_daily_sessions():
    url = BOX_OFFICE + '/2124933'
    original = calendar_rows(fixture('2124933.html'), url, venue_text(), TODAY)
    edited = fixture('2124933.html').replace(b'T17:30:00', b'T18:00:00')
    changed = calendar_rows(edited, url, venue_text(), TODAY)
    assert [r['external_id'] for r in original] == [r['external_id'] for r in changed]
    soup = BeautifulSoup(fixture('2124933.html'), 'html.parser')
    event = json.loads(soup.select_one('script[type="application/ld+json"]').get_text())
    event['startDate'] = '2026-10-27T20:00:00-04:00'
    script = soup.new_tag('script', type='application/ld+json')
    script.string = json.dumps(event)
    soup.append(script)
    rows = calendar_rows(str(soup), url, venue_text(), TODAY)
    assert len({r['external_id'] for r in rows}) == 3
    assert [(r['date'], r['start_time']) for r in rows[:2]] == [('2026-10-27', '17:30'), ('2026-10-27', '20:00')]


@pytest.mark.parametrize('series,venue,address,cost', [
    ('2488293', 'The Rose', '160 W 25th St New York, NY 10001', 7.75),
    ('1071619', 'The Stand', '116 E 16th St New York, NY 10003', 10),
    ('1696871', 'New York Comedy Club - UWS', '236 W 78th St New York, NY 10024', 8.5),
])
def test_each_venue_has_confirmed_street_address_and_performer_fee(series, venue, address, cost):
    raw = calendar_rows(fixture(series + '.html'), BOX_OFFICE + '/' + series, venue_text(), TODAY)
    result = extract(json.dumps(raw).encode(), source_config())
    assert not result['skipped'] and not result['warnings']
    assert all(r['venue'] == venue and r['address'] == address and r['cost'] == cost for r in result['rows'])
    # Stand's JSON-LD also contains an old $9 comic offer; its current stated
    # standard performer price is $8.50 + $1.50, not the audience or old rate.
    if series == '1071619':
        with pytest.raises(ValueError, match='address'):
            calendar_rows(fixture(series + '.html'), BOX_OFFICE + '/' + series, '', TODAY)


def test_missing_or_ambiguous_date_details_require_review_and_audience_fees_stay_unknown():
    broken = fixture('2124933.html').replace(b'2026-10-27T17:30:00-04:00', b'2026-10-27')
    with pytest.raises(ValueError, match='date/time'):
        calendar_rows(broken, BOX_OFFICE + '/2124933', venue_text(), TODAY)
    soup = BeautifulSoup(fixture('2124933.html'), 'html.parser')
    for script in soup.select('script[type="application/ld+json"]'):
        event = json.loads(script.get_text())
        event['offers'] = [{'name': 'Audience', 'price': 100, 'priceCurrency': 'USD'}]
        script.string = json.dumps(event)
    for item in soup.select('.event-page-description li'):
        if item.get_text().startswith('Tickets are'):
            item.decompose()
    rows = calendar_rows(str(soup), BOX_OFFICE + '/2124933', venue_text(), TODAY)
    assert all(r['cost'] == '' for r in rows)


def test_bounded_feed_uses_fetcher_for_every_page_and_does_not_return_partial_results():
    calls = []

    async def fetch(url):
        calls.append(url)
        name = 'index.html' if url == BOX_OFFICE else 'venues.html' if url == VENUES_URL else url.rsplit('/', 1)[-1] + '.html'
        return {'content': fixture(name)}

    fetcher = type('Fetcher', (), {'fetch': staticmethod(fetch)})()
    content = asyncio.run(fetch_feed(fetcher, TODAY))
    result = extract(content, source_config())
    assert len(result['rows']) == 14 and result['skipped'] == 0
    assert calls == [BOX_OFFICE, VENUES_URL, *event_links(fixture('index.html'))]

    async def failed_fetch(url):
        if url.endswith('/1071619'):
            raise ValueError('Source could not be fetched safely')
        return await fetch(url)

    fetcher.fetch = failed_fetch
    with pytest.raises(ValueError, match='safely'):
        asyncio.run(fetch_feed(fetcher, TODAY))


@pytest.mark.parametrize('first_status', [200, 304])
def test_sync_reloads_booking_calendar_even_when_mics_page_is_unchanged(database, monkeypatch, first_status):
    import app.main as main

    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    app = create_app(database)
    config = source_config()
    raw = calendar_rows(fixture('2124933.html'), BOX_OFFICE + '/2124933', venue_text(), TODAY)
    feed = json.dumps(raw).encode()
    parsed = extract(feed, config)
    sid = app.state.store.commit_preview(app.state.store.preview(
        config.model_dump(), parsed, validators={'hash': digest(feed), 'etag': 'unchanged'}))
    calls, feed_calls = [], []

    async def fetch(url, etag=None, modified=None):
        calls.append((url, etag, modified))
        return {'status': first_status if len(calls) == 1 else 200,
                'content': b'<p>Same mic landing page</p>', 'headers': {'Content-Type': 'text/html'}}

    async def live_feed(fetcher):
        feed_calls.append(fetcher)
        return feed

    monkeypatch.setattr(app.state.fetcher, 'fetch', fetch)
    monkeypatch.setattr(main, 'laughingbuddha_feed', live_feed)
    assert asyncio.run(app.state.sync_one(sid))['state'] == 'updated'
    assert feed_calls == [app.state.fetcher]
    assert calls == [(SOURCE, None, None)] * (2 if first_status == 304 else 1)


def test_admin_preview_loads_actual_calendar_before_review(database, monkeypatch):
    import app.main as main

    password = 'Test-Laughing-Buddha-Strong-Password-2026'
    monkeypatch.setenv('ADMIN_PASSWORD', password)
    monkeypatch.setenv('LOCAL_DEV', '1')
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('GEOCODING_ENABLED', '0')
    app = create_app(database)
    raw = calendar_rows(fixture('2124933.html'), BOX_OFFICE + '/2124933', venue_text(), TODAY)

    async def fetch(url):
        assert url == SOURCE
        return {'content': b'<p>Mic landing page</p>', 'headers': {'Content-Type': 'text/html'}}

    async def live_feed(fetcher):
        assert fetcher is app.state.fetcher
        return json.dumps(raw).encode()

    monkeypatch.setattr(app.state.fetcher, 'fetch', fetch)
    monkeypatch.setattr(main, 'laughingbuddha_feed', live_feed)
    with TestClient(app, headers={'X-Requested-With': 'MicList'}) as client:
        assert client.post('/api/login', json={'password': password}).status_code == 200
        result = client.post('/api/sources/preview', json=source_config().model_dump())
    assert result.status_code == 200, result.text
    assert len(result.json()['rows']) == 2 and result.json()['skipped'] == 0
    assert result.json()['preview_id']
