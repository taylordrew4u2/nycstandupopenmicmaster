import json
import pytest
from app.models import SourceConfig
from app.parsers import extract
from app.store import Store

CONFIG = SourceConfig(name='Bushwick calendar', url='https://www.bushwickcomedy.com/open-mics', permission_confirmed=True)

def event(title='Wednesday Open Mic', start='2026-09-30T23:30:00.000Z', id='event-one'):
    return {'id': id, 'title': title, 'scheduling': {'config': {'startDate': start}},
            'location': {'name': 'Bushwick Comedy Club', 'address': '259 Melrose St, Brooklyn, NY 11206, USA',
                         'coordinates': {'lat': 40.7020079, 'lng': -73.9299529}}}

def page(events):
    return ('<script id="wix-warmup-data" type="application/json">'+json.dumps({'appsWarmupData': {'widget': {'events': events}}})+'</script>').encode()

def test_only_explicit_open_mics_exact_dates_and_timezone():
    result = extract(page([event(), event('Comedy Showcase', id='show'), event('Saturday Open-Mic', '2026-11-08T00:30:00Z', 'second')]), CONFIG)
    assert result['skipped'] == 0 and len(result['rows']) == 2
    first, second = result['rows']
    assert (first['date'], first['start_time']) == ('2026-09-30', '19:30')
    assert (second['date'], second['start_time']) == ('2026-11-07', '19:30')
    assert first['latitude'] == 40.7020079 and first['cost'] is None
    assert first['signup_url'] == CONFIG.url

@pytest.mark.parametrize('events', [[], [event('Other show')], [event(start='ambiguous')]])
def test_missing_or_ambiguous_calendar_requires_review(events):
    with pytest.raises(ValueError):
        extract(page(events), CONFIG)

def test_exact_same_venue_date_time_deduplicates_only_generic_calendar(database):
    store = Store(database)
    bad = SourceConfig(name='Other calendar',url='https://badslava.com/open-mics.php?state=NY&type=Comedy',kind='json',permission_confirmed=True)
    # Use normalized rows to isolate matching from source extraction.
    from app.parsers import normalize_rows
    raw = {'id':'one','name':'Open Mic','venue':'Bushwick Comedy','address':'259 Melrose Street','borough':'Brooklyn','date':'2026-09-30','start_time':'19:30'}
    rows, warnings, skipped = normalize_rows([raw],bad)
    store.commit_preview(store.preview(bad.model_dump(),{'rows':rows,'warnings':warnings,'skipped':skipped}))
    original = store.public_data()['listings'][0]['id']
    parsed = extract(page([event(),event('Later Open Mic','2026-10-01T01:00:00Z','later')]), CONFIG)
    store.commit_preview(store.preview(CONFIG.model_dump(),parsed))
    records = store.public_data()['listings']
    assert len(records) == 2
    merged = next(r for r in records if r['id']==original)
    assert len(merged['sources'])==2
    assert merged['name']=='Wednesday Open Mic'
