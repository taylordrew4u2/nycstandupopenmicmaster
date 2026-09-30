"""Read explicitly dated open mics from the venue's public Wix calendar."""
import json
import re
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


def supports(url):
    p = urlsplit(url)
    return p.hostname in ('bushwickcomedy.com', 'www.bushwickcomedy.com') and p.path.rstrip('/') == '/open-mics'


def calendar_rows(soup, url):
    script = soup.find('script', id='wix-warmup-data')
    if not script:
        raise ValueError('Bushwick calendar data is unavailable. Existing mics kept for review.')
    try:
        data = json.loads(script.string or script.get_text())
        apps = data['appsWarmupData']
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Bushwick calendar layout changed; review required.') from exc
    events = {}
    def collect(value):
        if isinstance(value, dict):
            if 'title' in value and 'scheduling' in value and 'id' in value:
                events[value['id']] = value
            else:
                for child in value.values():
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(apps)
    rows = []
    for event in events.values():
        title = str(event.get('title', '')).strip()
        # Other venue shows must never become mics merely because of the page URL.
        if not re.search(r'\bopen[\s-]+mic\b', title, re.I):
            continue
        try:
            schedule = event['scheduling']['config']
            if schedule.get('scheduleTbd'):
                raise ValueError('date is not confirmed')
            start = datetime.fromisoformat(schedule['startDate'].replace('Z', '+00:00'))
            if start.tzinfo is None:
                raise ValueError('start time has no timezone')
            local = start.astimezone(ZoneInfo('America/New_York'))
            location = event['location']
            address = location['address']
            if not address or 'brooklyn' not in address.casefold():
                raise ValueError('venue address is not confirmed')
            row = {'external_id': 'bushwick:' + event['id'], 'name': title, 'display_name': title,
                   'venue': location['name'], 'address': address, 'borough': 'Brooklyn',
                   'date': local.date().isoformat(), 'start_time': local.strftime('%H:%M'),
                   'signup_url': url, 'notes': 'Listed on the venue calendar. Check the venue for signup and current details.'}
            coordinates = location.get('coordinates', {})
            if coordinates.get('lat') is not None and coordinates.get('lng') is not None:
                row.update(latitude=coordinates['lat'], longitude=coordinates['lng'])
            # Do not infer performer fees from audience/ticket prices or invent recurrence.
            rows.append(row)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('A Bushwick open mic has incomplete date or location details; review required.') from exc
    if not rows:
        raise ValueError('No explicitly labeled open mics found on Bushwick calendar; existing mics kept for review.')
    return rows
