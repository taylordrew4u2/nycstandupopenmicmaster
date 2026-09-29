"""Read Badslava's public comedy calendar without executing page scripts.

Dates come from the calendar, not an assumed weekly recurrence. Map coordinates
and prices are joined only when the venue, address, weekday and time all match.
"""
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup


def supports(url):
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    return (parsed.hostname in ('badslava.com', 'www.badslava.com')
            and parsed.path in ('/open-mics-state.php', '/open-mics.php')
            and query.get('state') == ['NY']
            and [v.lower() for v in query.get('type', [])] == ['comedy'])


def calendar_rows(soup, url):
    def tidy(value):
        return re.sub(r'\s+', ' ', value).strip()

    def identity(venue, address, weekday, start):
        return tuple(tidy(s).casefold() for s in (venue, address, weekday, start))

    metadata = {}
    for script in soup.find_all('script'):
        text = script.string or ''
        arrays = {}
        for name in ('venue', 'latitude', 'longitude'):
            match = re.search(r'\bvar\s+' + name + r'\s*=\s*(\[[^\n]*\])\s*;', text)
            if match:
                try:
                    arrays[name] = json.loads(match[1])
                except (ValueError, TypeError):
                    pass
        if set(arrays) != {'venue', 'latitude', 'longitude'}:
            continue
        if len({len(a) for a in arrays.values()}) != 1:
            continue
        for html, lat, lng in zip(arrays['venue'], arrays['latitude'], arrays['longitude']):
            if not isinstance(html, str):
                continue
            parts = [BeautifulSoup(x, 'html.parser').get_text(' ', strip=True)
                     for x in re.split(r'<br\s*/?>', html, flags=re.I)]
            if len(parts) != 9:
                continue
            key = identity(parts[2], parts[3] + ' ' + parts[4].replace(',', ''), parts[0], parts[6])
            metadata.setdefault(key, []).append((parts, lat, lng))

    rows = []
    for table in soup.find_all('table'):
        event_date = None
        for tr in table.find_all('tr'):
            header = tr.find('th')
            if header:
                label = tidy(header.get_text(' ', strip=True))
                try:
                    parsed = datetime.strptime(label, '%A %m/%d/%y')
                    if parsed.strftime('%A') != label.split()[0]:
                        raise ValueError('weekday mismatch')
                    event_date = parsed
                except ValueError:
                    event_date = None
                continue
            cells = tr.find_all('td', recursive=False)
            if not event_date or len(cells) != 2:
                continue
            link = cells[1].find('a', href=True)
            venue_tag = link.find('b') if link else None
            if not link or not venue_tag:
                continue
            venue = tidy(venue_tag.get_text(' ', strip=True))
            parts = [tidy(t) for t in link.stripped_strings]
            address = tidy(' '.join(parts[1:]))
            city = re.search(r'\s(New York|Brooklyn|Queens|Bronx|Staten Island)\s+NY$', address, re.I)
            if not city:
                continue  # State-wide calendar: intentionally omit non-NYC locations.
            borough = {'new york': 'Manhattan', 'brooklyn': 'Brooklyn', 'queens': 'Queens',
                       'bronx': 'Bronx', 'staten island': 'Staten Island'}[city[1].lower()]
            start = tidy(cells[0].get_text(' ', strip=True))
            source_url = urljoin(url, link['href'])
            event_id = parse_qs(urlsplit(source_url).query).get('id', [''])[0]
            if not event_id.isdigit():
                raise ValueError('Badslava calendar event is missing its stable ID')
            row = {'external_id': f'badslava:{event_id}:{event_date.date().isoformat()}',
                   'name': 'Open Mic', 'venue': venue, 'address': address[:city.start()],
                   'borough': borough, 'date': event_date.date().isoformat(),
                   'start_time': start, 'signup_url': source_url,
                   'notes': 'Listed by Badslava. Confirm details with the venue before traveling.'}
            matches = metadata.get(identity(venue, address, event_date.strftime('%A'), start), [])
            if len(matches) == 1:
                fields, lat, lng = matches[0]
                row.update(name=fields[1] or 'Open Mic', cost=fields[7])
                minutes = re.search(r'\bfor (\d{1,2}) minutes\b', fields[7], re.I)
                if minutes:
                    row['set_minutes'] = minutes[1]
                try:
                    y, x = float(lat), float(lng)
                    if 40.47 <= y <= 40.93 and -74.27 <= x <= -73.68:
                        row.update(latitude=y, longitude=x)
                except (ValueError, TypeError):
                    pass
            rows.append(row)
    if not rows:
        raise ValueError('No dated NYC comedy rows found; Badslava layout may have changed')
    return rows
