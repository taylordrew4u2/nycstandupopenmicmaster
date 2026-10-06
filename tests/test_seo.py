import json
from datetime import datetime, timedelta
from xml.etree import ElementTree

import pytest
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException

from app import seo


def listing(**changes):
    row = {'id': 'test-mic', 'name': 'Test Comedy Mic', 'venue': 'Test Room',
           'address': '123 Example St', 'borough': 'Brooklyn', 'weekday': 0,
           'date': None, 'start_time': '19:30', 'frequency': 'weekly',
           'host_names': 'A Host', 'status': 'scheduled', 'claimed': False,
           'venue_confirmed': True, 'updated_at': 1700000000, 'curated_at': None,
           'sources': [{'url': 'https://example.com/mics', 'name': 'Venue schedule'}]}
    return {**row, **changes}


class Store:
    def __init__(self, rows):
        self.rows = rows

    def public_data(self):
        return {'listings': self.rows}


def endpoint(rows, path):
    app = FastAPI()
    seo.register(app, Store(rows))
    return next(route.endpoint for route in app.routes if route.path == path)


def soup(response):
    return BeautifulSoup(response.body, 'html.parser')


def test_directory_and_borough_render_without_javascript():
    rows = [listing(), listing(id='queens-mic', borough='Queens', name='Queens Mic')]
    doc = soup(endpoint(rows, '/nyc-comedy-open-mics')())
    assert doc.h1.text == 'NYC comedy open mics'
    assert doc.select_one('a[href="/mics/test-mic"]')
    assert doc.select_one('a[href="/open-mics/brooklyn"]')
    assert doc.select_one('a[href="/open-mics/queens"]')
    assert 'Monday at 7:30 PM' in doc.text
    assert '123 Example St' in doc.text
    borough = soup(endpoint(rows, '/open-mics/{borough}')('queens'))
    assert borough.h1.text == 'Queens comedy open mics'
    assert len(borough.select('.listings>li')) == 1
    assert 'Queens Mic' in borough.select_one('.listings').text
    assert 'Test Comedy Mic' not in borough.select_one('.listings').text


def test_pagination_is_crawlable_and_canonical():
    rows = [listing(id=str(i), name=f'Mic {i:03}') for i in range(81)]
    handler = endpoint(rows, '/nyc-comedy-open-mics')
    first = soup(handler())
    assert len(first.select('.listings>li')) == 40
    assert first.select_one('a[rel="next"]')['href'] == '/nyc-comedy-open-mics?page=2'
    last = soup(handler(page=3))
    assert len(last.select('.listings>li')) == 1
    assert last.select_one('link[rel="canonical"]')['href'] == seo.BASE + '/nyc-comedy-open-mics?page=3'
    assert last.select_one('a[rel="prev"]')['href'] == '/nyc-comedy-open-mics?page=2'
    with pytest.raises(HTTPException) as error:
        handler(page=4)
    assert error.value.status_code == 404


def test_empty_borough_is_noindex_but_not_in_sitemap():
    rows = [listing()]
    result = endpoint(rows, '/open-mics/{borough}')('queens')
    assert result.headers['X-Robots-Tag'] == 'noindex, follow'
    assert soup(result).select_one('meta[name="robots"]')['content'] == 'noindex, follow'
    assert '/open-mics/queens' not in seo.sitemap_xml(rows).decode()


def test_hidden_and_missing_mics_are_not_exposed():
    rows = [listing(hidden=True)]
    assert not seo.published(Store(rows))
    with pytest.raises(HTTPException) as error:
        endpoint(rows, '/mics/{mic_id}')('test-mic')
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        endpoint([listing()], '/mics/{mic_id}')('unknown')
    assert error.value.status_code == 404


def test_mic_details_expose_real_info_and_source():
    row = listing(host_socials='https://instagram.com/example', cost_text='$5', notes='Arrive early.',
                  frequency='biweekly', recurrence_anchor='2026-10-05', excluded_dates=['2026-10-19'])
    doc = soup(endpoint([row], '/mics/{mic_id}')('test-mic'))
    assert 'Venue confirmed' in doc.text
    assert 'A Host' in doc.text
    assert 'Biweekly (every other week)' in doc.text
    assert 'Monday, October 5, 2026' in doc.text
    assert 'Monday, October 19, 2026' in doc.text
    assert 'Last updated:' in doc.text
    assert doc.select_one('a[href="https://example.com/mics"]')
    assert doc.select_one('a[href="https://instagram.com/example"]')
    assert doc.select_one('a[href="/?mic=test-mic"]')
    assert json.loads(doc.select_one('script[type="application/ld+json"]').string)['@graph'][0]['@type'] == 'WebPage'


def test_html_jsonld_and_untrusted_links_are_safe():
    dangerous = '</script><img src=x onerror=alert(1)>'
    row = listing(name=dangerous, venue='A & B "room"', notes=dangerous,
                  signup_url='javascript:alert(1)', host_socials='data:text/html,test javascript:alert(1)',
                  sources=[{'name': dangerous, 'url': 'javascript:alert(1)'},
                           {'name': 'A & B', 'url': 'https://example.com/?x=1&y=2'}])
    content = seo.mic_detail(row)
    doc = BeautifulSoup(content, 'html.parser')
    assert not doc.find('img')
    assert len(doc.find_all('script')) == 2
    assert doc.select_one('script[src="/assets/visitors.js"]').has_attr('defer')
    structured = json.loads(doc.select_one('script[type="application/ld+json"]').string)
    assert dangerous in structured['@graph'][0]['name']
    assert '\\u003c/script\\u003e' in content
    assert all(not str(link.get('href')).startswith(('javascript:', 'data:')) for link in doc.find_all('a'))
    assert doc.select_one('a[href="https://example.com/?x=1&y=2"]')


def test_sitemap_includes_pages_and_real_lastmod_only():
    rows = [listing(id=f'mic-{i}') for i in range(41)]
    rows.append(listing(id='missing-updated', updated_at=None, checked_at=1800000000))
    rows.append(listing(id='mic & unusual', updated_at=1700000000, curated_at=1700100000))
    xml = ElementTree.fromstring(seo.sitemap_xml(rows))
    ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    entries = {node.find('s:loc', ns).text: node.find('s:lastmod', ns) for node in xml}
    assert seo.BASE + '/' in entries
    assert seo.BASE + '/about' in entries
    assert seo.BASE + '/nyc-comedy-open-mics?page=2' in entries
    assert seo.BASE + '/open-mics/brooklyn?page=2' in entries
    assert seo.BASE + '/mics/mic%20%26%20unusual' in entries
    assert entries[seo.BASE + '/mics/missing-updated'] is None
    assert entries[seo.BASE + '/nyc-comedy-open-mics'] is None
    assert entries[seo.BASE + '/mics/mic%20%26%20unusual'].text == '2023-11-16T02:00:00+00:00'


def test_historical_dated_mic_remains_accessible_but_is_not_promoted():
    yesterday = (datetime.now(seo.NY).date() - timedelta(days=1)).isoformat()
    old = listing(id='past', date=yesterday, frequency='one-time')
    new = listing(id='current')
    result = endpoint([old, new], '/mics/{mic_id}')('past')
    assert result.status_code == 200
    assert result.headers['X-Robots-Tag'] == 'noindex, follow'
    assert 'Past dated listing' in soup(result).text
    assert len(seo.published(Store([old, new]))) == 2
    assert '/mics/past' not in seo.sitemap_xml([old, new]).decode()
    assert '/mics/current' in seo.sitemap_xml([old, new]).decode()
    assert 'href="/mics/past"' not in seo.directory([old, new])


def test_generated_comediq_date_keeps_weekly_schedule_and_overrides_win():
    row = listing(date='2020-01-01', frequency='', sources=[{'url': 'https://comediq.us/mics.json'}])
    assert seo.recurrence(row) == 'Weekly'
    assert seo.schedule(row) == 'Monday at 7:30 PM · Weekly'
    assert not seo.historical(row)
    assert seo.historical({**row, 'overridden_fields': ['date']})
    assert seo.recurrence({**row, 'frequency': 'one-time'}) == 'Pop-up mic (not recurring)'
    assert seo.historical({**row, 'frequency': 'one-time'})


@pytest.mark.parametrize('value', ['2026-02-30', 'not-a-date', '2026-2-2', None])
def test_invalid_dates_do_not_create_links(value):
    assert seo.valid_date(value) is None
    assert '/?date=' not in seo.mic_detail(listing(date=value, weekday=None, frequency=''))


def test_actual_date_has_weekday_and_date_search_link():
    tomorrow = (datetime.now(seo.NY).date() + timedelta(days=1)).isoformat()
    row = listing(date=tomorrow, frequency='one-time', weekday=None)
    doc = BeautifulSoup(seo.mic_detail(row), 'html.parser')
    assert seo.date_label(tomorrow) in doc.text
    assert doc.select_one(f'a[href="/?date={tomorrow}"]')


def test_unspecified_cadence_and_biweekly_anchor_are_not_guessed():
    row = listing(frequency='', weekday=None, date=None)
    assert seo.recurrence(row) == 'Schedule unconfirmed'
    assert 'Day not listed' in seo.schedule(row)
    content = seo.mic_detail(listing(frequency='biweekly', recurrence_anchor=None))
    assert 'confirm the week with the host or venue' in content


def test_unknown_borough_returns_404():
    with pytest.raises(HTTPException) as error:
        endpoint([listing()], '/open-mics/{borough}')('new-jersey')
    assert error.value.status_code == 404


@pytest.mark.parametrize('row,expected', [({'cost': 0}, 'Free'), ({'cost': 5}, '$5'),
                                        ({'cost': 4.5}, '$4.5'), ({'cost': None}, 'Not listed'),
                                        ({'cost': 5, 'cost_text': '$5 + one item'}, '$5 + one item')])
def test_entry_fee_preserves_numeric_price_and_source_text(row, expected):
    assert seo.entry_fee(row) == expected


def test_empty_sitemap_has_no_empty_landing_pages():
    xml = seo.sitemap_xml([]).decode()
    assert '/nyc-comedy-open-mics' not in xml
    assert '/open-mics/' not in xml


def test_nonweekly_schedule_does_not_claim_weekly_or_create_occurrences():
    row = listing(frequency='non-weekly', notes='Non-weekly; dates to be confirmed.')
    assert seo.recurrence(row) == 'Non-weekly · check schedule'
    assert seo.schedule(row) == 'Monday at 7:30 PM · Non-weekly · check schedule'
    content = seo.mic_detail(row)
    assert 'Confirmed dates' in content and 'check the mic’s schedule before going' in content
    assert '/?date=' not in content
    assert '"startDate"' not in content
