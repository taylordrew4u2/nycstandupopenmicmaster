"""Deterministic adapters for tabular files, event JSON-LD and configured HTML cards.

Unknown values stay unknown. Partial/ambiguous parses are not automatically synced.
No LLM is required, and HTML scripts are never executed.
"""
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import date, datetime, time
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from openpyxl import load_workbook

from .models import SourceConfig

NY = ZoneInfo('America/New_York')
DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
BOROUGHS = ['Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island']
ALIASES = {
    'display_name': ['display name'],
    'host_names': ['host names', 'hosts', 'host'],
    'host_socials': ['host socials', 'social links'],
    'external_id': ['id', 'event id', 'mic id', 'external id'],
    'latitude': ['latitude', 'lat'],
    'longitude': ['longitude', 'lng', 'lon'],
    'name': ['name', 'mic', 'mic name', 'event', 'event name', 'title', 'open mic', 'show'],
    'venue': ['venue', 'venue name', 'location', 'club', 'bar'],
    'address': ['address', 'street', 'street address', 'venue address'],
    'borough': ['borough', 'boro'],
    'neighborhood': ['neighborhood', 'neighbourhood', 'area'],
    'weekday': ['day', 'weekday', 'day of week', 'days', 'recurs'],
    'date': ['date', 'event date'],
    'frequency': ['frequency', 'recurrence'],
    'recurrence_anchor': ['recurrence anchor', 'confirmed biweekly date'],
    'start_time': ['start time', 'time', 'starts', 'show time', 'mic time'],
    'signup_time': ['signup time', 'sign up time', 'registration time'],
    'signup_url': ['signup url', 'signup link', 'sign up link', 'registration', 'tickets', 'url', 'link'],
    'signup_method': ['signup method', 'signup type', 'sign up', 'signup', 'format'],
    'cost': ['cost', 'price', 'fee', 'cover', 'entry fee'],
    'purchase_minimum': ['minimum', 'purchase minimum', 'drink minimum', 'item minimum'],
    'set_minutes': ['minutes', 'set minutes', 'stage time', 'set length', 'time on stage'],
    'notes': ['notes', 'description', 'details', 'info'],
    'status': ['status', 'event status'],
    'excluded_dates': ['excluded dates', 'cancelled dates', 'canceled dates', 'exceptions'],
}
FIELDS = list(ALIASES)

def clean(value):
    if value is None:
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip()

def key(value):
    return re.sub(r'[^a-z0-9]', '', clean(value).lower())

def safe_link(value, base=''):
    value = clean(value)
    if not value:
        return ''
    try:
        result = urljoin(base, value)
        p = urlsplit(result)
        if p.scheme in ('https', 'http') and p.hostname and not p.username and not p.password:
            return result[:2048]
    except ValueError:
        pass
    return ''

def parse_time(value):
    if isinstance(value, (datetime, time)):
        return value.strftime('%H:%M')
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 1:
        minutes = round(value * 1440) % 1440
        return f'{minutes // 60:02d}:{minutes % 60:02d}'
    text = clean(value).lower().replace('.', '')
    text = text.replace('noon', '12 pm').replace('midnight', '12 am')
    # Only explicit 24-hour HH:MM or a meridiem; a bare 7 is deliberately ambiguous.
    m = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm)', text)
    if m:
        hour, minute = int(m[1]), int(m[2] or 0)
        if not 1 <= hour <= 12 or minute > 59:
            return None
        hour = hour % 12 + (12 if m[3] == 'pm' else 0)
        return f'{hour:02d}:{minute:02d}'
    if re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?', text):
        return text[:5]
    return None

def parse_date(value):
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    text = clean(value)
    if not text:
        return None
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError('date must include a year (YYYY-MM-DD or MM/DD/YYYY)')

def parse_days(value):
    s = clean(value).lower()
    if not s:
        return []
    if re.search(r'\b(first|second|third|fourth|last|every other|monthly|1st|2nd|3rd|4th)\b', s):
        raise ValueError('monthly/alternating recurrences need individual dates, not a weekly assumption')
    if s in ('daily', 'every day', 'everyday'):
        return list(range(7))
    for prefix in ('every ', 'weekly ', 'weekly on '):
        if s.startswith(prefix):
            s = s[len(prefix):]
    s = re.sub(r'\b(and|on)\b', ',', s)
    tokens = re.split(r'[,/&;\s]+', s)
    result = []
    for token in tokens:
        if not token:
            continue
        match = next((i for i, d in enumerate(DAYS) if token.rstrip('s') in (d.lower(), d[:3].lower(), d[:4].lower())), None)
        if match is None:
            raise ValueError('weekday is missing or ambiguous; use a full weekday name')
        result.append(match)
    return sorted(set(result))

def _map_row(row, config):
    index = {key(k): v for k, v in row.items()}
    out = {}
    for field, aliases in ALIASES.items():
        heading = config.mapping.get(field)
        if heading:
            out[field] = index.get(key(heading), '')
        else:
            out[field] = next((index[key(a)] for a in [field] + aliases if key(a) in index and index[key(a)] not in ('', None)), '')
        if out[field] in ('', None) and field in config.defaults:
            out[field] = config.defaults[field]
    return out

def _table_rows(matrix):
    # Locate a header within the first 20 rows; cover title rows in Excel exports.
    matrix = matrix[:10020]
    candidates = []
    known = {key(a) for aliases in ALIASES.values() for a in aliases}
    for i, row in enumerate(matrix[:20]):
        score = len({key(x) for x in row} & known)
        candidates.append((score, -i, i))
    if not candidates:
        return []
    _, _, index = max(candidates)
    headings = [clean(x) for x in matrix[index]]
    return [{h: row[j] if j < len(row) else '' for j, h in enumerate(headings) if h}
            for row in matrix[index+1:] if any(x is not None and clean(x) for x in row)]

def parse_xlsx(content, config):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            if len(z.infolist()) > 2000 or sum(i.file_size for i in z.infolist()) > 30 * 1024 * 1024:
                raise ValueError('Workbook expands beyond the 30 MB safety limit')
            if any('vbaProject' in x.filename for x in z.infolist()):
                raise ValueError('Macro-enabled workbooks are not supported')
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
        try:
            if config.sheet and config.sheet not in book.sheetnames:
                raise ValueError('The selected worksheet does not exist')
            sheets = [book[config.sheet]] if config.sheet else list(book.worksheets)
            rows = []
            for sheet in sheets[:25]:
                matrix = []
                for idx, row in enumerate(sheet.iter_rows(values_only=True, max_col=80)):
                    if idx >= 10020:
                        raise ValueError('Workbook exceeds 10,000 data rows per worksheet')
                    matrix.append(list(row))
                rows.extend(_table_rows(matrix))
            return rows, book.sheetnames
        finally:
            book.close()
    except zipfile.BadZipFile as exc:
        raise ValueError('This is not a valid .xlsx workbook; export legacy .xls files as .xlsx or CSV') from exc

def _jsonld_nodes(obj):
    if isinstance(obj, list):
        for child in obj:
            yield from _jsonld_nodes(child)
    elif isinstance(obj, dict):
        t = obj.get('@type', [])
        if any(str(x).endswith('Event') for x in (t if isinstance(t, list) else [t])):
            yield obj
        for k, child in obj.items():
            if k in ('@graph', 'itemListElement', 'item', 'subEvent'):
                yield from _jsonld_nodes(child)

def parse_jsonld(soup):
    rows = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            nodes = list(_jsonld_nodes(json.loads(tag.get_text())))
        except (ValueError, TypeError):
            continue
        for obj in nodes:
            location = obj.get('location', {})
            location = location[0] if isinstance(location, list) and location else location
            location = location if isinstance(location, dict) else {'name': location}
            address = location.get('address', {})
            address = address if isinstance(address, dict) else {'streetAddress': address}
            offers = obj.get('offers', {})
            offers = offers[0] if isinstance(offers, list) and offers else offers
            offers = offers if isinstance(offers, dict) else {}
            start = obj.get('startDate', '')
            # Missing times should fail validation rather than become midnight.
            try:
                if 'T' not in start:
                    raise ValueError('No timestamp')
                dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                if dt.tzinfo:
                    dt = dt.astimezone(NY)
                event_date, start_time = dt.date().isoformat(), dt.strftime('%H:%M')
            except (ValueError, TypeError):
                event_date, start_time = '', ''
            locality = clean(address.get('addressLocality'))
            borough = locality  # A city label alone does not prove a borough; use a confirmed source default.
            rows.append({
                'external_id': obj.get('@id') or obj.get('url') or '',
                'name': obj.get('name', ''), 'venue': location.get('name', ''),
                'address': address.get('streetAddress', ''), 'borough': borough,
                'date': event_date, 'start_time': start_time,
                'cost': str(offers['price']) if 'price' in offers else '',
                'signup_url': offers.get('url') or obj.get('url') or '',
                'notes': BeautifulSoup(obj.get('description') or '', 'html.parser').get_text(' ', strip=True),
                'status': 'cancelled' if 'Cancelled' in obj.get('eventStatus', '') else 'scheduled',
            })
    return rows

def normalize_rows(raw_rows, config):
    rows, warnings = [], []
    if len(raw_rows) > 10000:
        raise ValueError('Source exceeds the 10,000 row limit; split it into smaller feeds')
    skipped = 0
    for index, raw in enumerate(raw_rows):
        try:
            mapped = _map_row(raw, config)
            name, venue = clean(mapped['name']), clean(mapped['venue'])
            if not name or not venue:
                raise ValueError('mic name and venue are required')
            borough_input = clean(mapped['borough']).lower().replace('the bronx', 'bronx')
            borough = next((b for b in BOROUGHS if b.lower() == borough_input), None)
            if not borough:
                raise ValueError('an explicit NYC borough is required; set a default only when it applies to every row')
            event_date = parse_date(mapped['date'])
            days = [None] if event_date else parse_days(mapped['weekday'])
            if not days:
                raise ValueError('an event date or weekly day is required')
            start = parse_time(mapped['start_time'])
            if not start:
                raise ValueError('start time is missing or ambiguous; use 7:00 PM or 19:00')
            signup = parse_time(mapped['signup_time'])
            if clean(mapped['signup_time']) and not signup:
                raise ValueError('signup time is ambiguous')
            cost_text = clean(mapped['cost'])
            cost = None
            if cost_text.lower() in ('free', 'no cover'):
                cost = 0.0
            else:
                m = re.fullmatch(r'\$?\s*(\d+(?:\.\d{1,2})?)(?:\s*(?:usd|dollars?))?', cost_text, re.I)
                if m:
                    cost = float(m[1])
            minutes_text = clean(mapped['set_minutes'])
            minutes_match = re.fullmatch(r'(\d{1,2})(?:\.0)?\s*(?:minutes?|mins?)?', minutes_text, re.I)
            minutes = int(minutes_match[1]) if minutes_match else None
            if minutes is not None and not 1 <= minutes <= 90:
                minutes = None
            frequency = clean(mapped['frequency']).lower()
            if frequency not in ('', 'weekly', 'biweekly'):
                raise ValueError('frequency must be weekly or biweekly')
            anchor = parse_date(mapped['recurrence_anchor'])
            if anchor and frequency != 'biweekly':
                raise ValueError('a recurrence anchor requires biweekly frequency')
            if anchor and not event_date and datetime.fromisoformat(anchor).weekday() not in days:
                raise ValueError('confirmed biweekly date must match the selected weekday')
            exceptions = [parse_date(x.strip()) for x in clean(mapped['excluded_dates']).split(',') if x.strip()]
            status = clean(mapped['status']).lower()
            if status not in ('', 'scheduled', 'active', 'cancelled', 'canceled', 'postponed'):
                raise ValueError('status must be scheduled, cancelled, or postponed')
            lat = float(mapped['latitude']) if clean(mapped['latitude']) else None
            lng = float(mapped['longitude']) if clean(mapped['longitude']) else None
            if (lat is None) != (lng is None):
                raise ValueError('latitude and longitude must be provided together')
            if lat is not None and not (40.47 <= lat <= 40.93 and -74.27 <= lng <= -73.68):
                raise ValueError('coordinates are outside the NYC map bounds')
            social_links = [safe_link(u) for u in re.split(r'[\s,]+', clean(mapped['host_socials'])) if u]
            if len(social_links)>5 or any(not u for u in social_links):
                raise ValueError('Provide up to five complete https:// social links')
            for day in days:
                schedule = event_date or f'weekly:{day}'
                # Time is excluded so a time change updates the existing observation.
                basis = f'{key(name)}|{key(venue)}|{schedule}'
                external_id = clean(mapped['external_id'])
                remote_key = hashlib.sha256(f'{external_id or basis}|{schedule if not external_id else day}'.encode()).hexdigest()[:32]
                rows.append({
                    'remote_key': remote_key, 'external_id': external_id,
                    'latitude': lat, 'longitude': lng,
                    'display_name': clean(mapped['display_name'])[:180],
                    'host_names': clean(mapped['host_names'])[:180], 'host_socials': '\n'.join(social_links),
                    'name': name[:180], 'venue': venue[:180], 'address': clean(mapped['address'])[:300],
                    'borough': borough, 'neighborhood': clean(mapped['neighborhood'])[:100],
                    'weekday': day, 'date': event_date, 'start_time': start, 'signup_time': signup,
                    'signup_url': safe_link(mapped['signup_url'], config.url),
                    'signup_method': clean(mapped['signup_method'])[:100],
                    'cost': cost, 'cost_text': cost_text[:100], 'purchase_minimum': clean(mapped['purchase_minimum'])[:150],
                    'set_minutes': minutes, 'notes': clean(mapped['notes'])[:2000],
                    'status': 'cancelled' if status in ('cancelled', 'canceled') else ('scheduled' if status in ('','active') else status),
                    'excluded_dates': exceptions, 'frequency': frequency, 'recurrence_anchor': anchor,
                })
        except (ValueError, TypeError) as exc:
            skipped += 1
            if len(warnings) < 40:
                warnings.append(f'Row {index + 1}: {exc}.')
    counts = Counter(row['remote_key'] for row in rows)
    duplicates = {k for k, count in counts.items() if count > 1}
    if duplicates:
        warnings.append('Ambiguous duplicate rows were excluded. Add a distinct ID column for multiple sessions with the same name, venue and day.')
        removed = [r for r in rows if r['remote_key'] in duplicates]
        skipped += len(removed)
        rows = [r for r in rows if r['remote_key'] not in duplicates]
    return rows, warnings, skipped

def extract(content: bytes, config: SourceConfig, content_type='', filename=''):
    from .comediq import supports as supports_comediq, extract_feed
    if config.kind in ('auto', 'json') and supports_comediq(config.url):
        return extract_feed(content, config)
    kind = config.kind
    path = urlsplit(config.url or filename).path.lower()
    if kind in ('auto', 'upload'):
        if path.endswith('.xlsx') or content.startswith(b'PK\x03\x04'):
            kind = 'xlsx'
        elif path.endswith('.csv') or 'csv' in content_type or filename.lower().endswith('.csv'):
            kind = 'csv'
        elif 'json' in content_type or content.lstrip().startswith((b'{', b'[')):
            kind = 'json'
        else:
            kind = 'auto'
    sheets = []
    if kind == 'xlsx':
        raw, sheets = parse_xlsx(content, config)
    elif kind == 'csv':
        text = content.decode('utf-8-sig', errors='replace')
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        raw = _table_rows(list(csv.reader(io.StringIO(text), dialect)))
    elif kind == 'json':
        obj = json.loads(content)
        raw = obj if isinstance(obj, list) else obj.get('events', obj.get('mics', []))
        if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
            raise ValueError('JSON must be an array of rows, or an object with an events/mics array')
    else:
        soup = BeautifulSoup(content.decode('utf-8', errors='replace'), 'html.parser')
        raw = []
        from .badslava import supports, calendar_rows
        if kind == 'auto' and supports(config.url):
            raw = calendar_rows(soup, config.url)
            kind = 'badslava'
        from .bushwick import supports as bushwick_source, calendar_rows as bushwick_rows
        if kind == 'auto' and bushwick_source(config.url):
            raw = bushwick_rows(soup, config.url)
            kind = 'bushwick'
        from .club_sources import source_kind, calendar_rows as club_rows
        if kind == 'auto' and source_kind(config.url):
            raw = club_rows(soup, config.url)
            kind = 'official_club'
        if kind in ('auto', 'jsonld'):
            raw = parse_jsonld(soup)
            if raw:
                kind = 'jsonld'
        if not raw and kind in ('auto', 'table'):
            tables = soup.select(config.row_selector or 'table')
            for table in tables:
                matrix = []
                for tr in table.select('tr'):
                    cells = []
                    for td in tr.find_all(['td', 'th'], recursive=False):
                        text = td.get_text(' ', strip=True)
                        a = td.find('a', href=True)
                        if a and text.lower() in ('signup', 'sign up', 'register', 'link', 'tickets'):
                            text = urljoin(config.url, a['href'])
                        cells.append(text)
                    if cells:
                        matrix.append(cells)
                raw.extend(_table_rows(matrix))
            kind = 'table'
        elif kind == 'cards':
            if not config.row_selector or not config.mapping:
                raise ValueError('HTML cards require a row selector and field selectors')
            for element in soup.select(config.row_selector)[:10000]:
                row = {}
                for field, selector in config.mapping.items():
                    if field not in FIELDS or not selector:
                        continue
                    el = element.select_one(selector)
                    row[field] = (el.get('href', '') if field == 'signup_url' else el.get_text(' ', strip=True)) if el else ''
                raw.append(row)
        if not raw:
            raise ValueError('No supported event data found. Use a CSV/XLSX link, a page with event JSON-LD or a table, or configure HTML card selectors. JavaScript-only pages need a custom adapter; no data was invented.')
    normal_config = config.model_copy(update={'mapping': {}}) if kind in ('cards', 'jsonld') else config
    rows, warnings, skipped = normalize_rows(raw, normal_config)
    return {'rows': rows, 'warnings': warnings, 'skipped': skipped, 'raw_count': len(raw),
            'detected_kind': kind, 'sheets': sheets, 'headers': list(raw[0]) if raw else []}
