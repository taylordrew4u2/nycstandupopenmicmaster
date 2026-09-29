import json

from app.models import SourceConfig
from app.parsers import extract
from app.badslava import supports

URL = 'https://badslava.com/open-mics-state.php?state=NY&type=Comedy'


def test_calendar_dates_nyc_filter_and_map_join():
    venues = ['Tuesday<br>Open Mic<br><b>Example Club</b><br>123 Main St<br>Brooklyn, NY<br><br>6:00pm<br>$5 for 5 minutes<br>Weekly']
    html = '''<table><tr><th>Tuesday 09/29/26</th></tr>
    <tr><td>6:00pm</td><td><a href="details.php?id=101"><b>Example Club</b><br>123 Main St Brooklyn NY</a></td></tr>
    <tr><td>8:00pm</td><td><a href="details.php?id=102"><b>Outside NYC</b><br>1 Main St Buffalo NY</a></td></tr>
    <tr><td>9:00pm</td><td><a href="details.php?id=103"><b>Example Club</b><br>123 Main St Brooklyn NY</a></td></tr>
    <tr><th>Tuesday 10/06/26</th></tr>
    <tr><td>6:00pm</td><td><a href="details.php?id=101"><b>Example Club</b><br>123 Main St Brooklyn NY</a></td></tr></table>'''
    html += '<script>var venue = ' + json.dumps(venues) + ';\nvar latitude = ["40.70"];\nvar longitude = ["-73.95"];\n</script>'
    result = extract(html.encode(), SourceConfig(name='Calendar', url=URL))
    assert result['skipped'] == 0
    assert len(result['rows']) == 3
    first, late, next_week = result['rows']
    assert first['borough'] == 'Brooklyn' and first['address'] == '123 Main St'
    assert first['date'] == '2026-09-29' and first['weekday'] is None
    assert first['latitude'] == 40.70 and first['set_minutes'] == 5
    assert late['latitude'] is None  # Never join metadata solely by venue.
    assert first['remote_key'] != next_week['remote_key']
    assert first['signup_url'] == 'https://badslava.com/details.php?id=101'


def test_adapter_only_accepts_explicit_comedy_calendar():
    assert supports(URL)
    assert not supports(URL.replace('Comedy', 'Music'))
    assert not supports(URL.replace('badslava.com', 'example.com'))
