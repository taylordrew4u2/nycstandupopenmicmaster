"""Explicitly dated in-person mics from Laughing Buddha's official box office.

The /mics page embeds a calendar with stale recurrence metadata. The producer's
Ticket Tailor pages publish individual dated Event records, including temporary
festival time changes. Read those records without extrapolating weekly dates.
Every network request uses the application's robots-aware SafeFetcher.
"""
import asyncio
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

NY = ZoneInfo('America/New_York')
BOX_OFFICE = 'https://www.tickettailor.com/events/laughingbuddhacomedy'
VENUES_URL = 'https://www.laughingbuddhacomedy.com/venues'
MAX_SERIES = 60
_SERIES_PATH = re.compile(r'/events/laughingbuddhacomedy/(\d+)/?')
_MIC = re.compile(r'\bmics?\b', re.I)
_ONLINE = re.compile(r'\bonline\b|\bzoom\b', re.I)


def supports(url):
    parsed = urlsplit(url)
    return parsed.hostname in ('laughingbuddhacomedy.com', 'www.laughingbuddhacomedy.com') and parsed.path.rstrip('/') == '/mics'


def _clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def event_links(content):
    soup = BeautifulSoup(content, 'html.parser')
    links = []
    for link in soup.select('.event__title a[href]'):
        title = link.get_text(' ', strip=True)
        if not _MIC.search(title) or _ONLINE.search(title):
            continue
        url = urljoin(BOX_OFFICE, link['href'])
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'www.tickettailor.com' or not _SERIES_PATH.fullmatch(parsed.path) or parsed.query or parsed.fragment:
            raise ValueError('Laughing Buddha mic registration links changed; review required.')
        if url not in links:
            links.append(url)
    if not links or len(links) > MAX_SERIES:
        raise ValueError('Laughing Buddha mic list changed; existing mics kept for review.')
    return links


def _events(value):
    if isinstance(value, list):
        for child in value:
            yield from _events(child)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if 'Event' in (types if isinstance(types, list) else [types]):
            yield value
        for key in ('@graph', 'itemListElement', 'item', 'subEvent'):
            if key in value:
                yield from _events(value[key])


def _venue_address(event, description, official_venues):
    location = event.get('location', {})
    if not isinstance(location, dict):
        raise ValueError('Laughing Buddha venue details changed; review required.')
    venue = _clean(location.get('name'))
    if venue == 'Laughing Buddha Comedy':
        venue = 'The Buddha Room'
    # Some ticket pages supply only a venue name as their structured address.
    # Prefer the full street address printed in the mic's own description.
    match = re.search(r'(\d{1,5}\s+[^:]{2,100}?\bNew York\s*,?\s*NY\s*\d{5})', description, re.I)
    if match:
        address = match[1]
    else:
        known = {'The Stand': '116 E 16th St New York, NY 10003',
                 'New York Comedy Club - UWS': '236 W 78th St New York, NY 10024'}
        address = known.get(venue, '')
        if not address or address.casefold() not in official_venues.casefold():
            raise ValueError('Laughing Buddha street address is not confirmed; review required.')
    if venue not in ('The Buddha Room', 'The Rose', 'The Stand', 'New York Comedy Club - UWS') or not re.search(r'\b100\d{2}\b', address):
        raise ValueError('Laughing Buddha venue location needs review.')
    return venue, address


def _performer_cost(event, description):
    # The prose identifies the standard performer rate and separates its
    # booking fee from audience prices and legacy/group ticket offers.
    standard = re.search(r'Tickets\s+are\s*\$(\d+(?:\.\d+)?)\s*\+\s*\$(\d+(?:\.\d+)?)\s*booking\s+fee\s+for\s+comics\b', description, re.I)
    if standard:
        base, fee = (Decimal(value) for value in standard.groups())
        return f'${base + fee:.2f}'
    offers = event.get('offers', [])
    offers = offers if isinstance(offers, list) else [offers]
    prices = []
    for offer in offers:
        if not isinstance(offer, dict) or _clean(offer.get('name')).casefold() != 'comic':
            continue
        if offer.get('priceCurrency') != 'USD':
            raise ValueError('Laughing Buddha performer price currency needs review.')
        try:
            price = Decimal(str(offer['price']))
            if not price.is_finite() or price < 0:
                raise InvalidOperation
            prices.append(price)
        except (KeyError, InvalidOperation):
            raise ValueError('Laughing Buddha performer price needs review.') from None
    # Audience offers must never become performer fees.
    return '$' + format(min(prices), '.2f') if prices else ''


def calendar_rows(content, url, official_venues='', today=None):
    series = _SERIES_PATH.fullmatch(urlsplit(url).path)
    if not series:
        raise ValueError('Laughing Buddha registration URL needs review.')
    today = today or datetime.now(NY).date()
    soup = BeautifulSoup(content, 'html.parser')
    section = soup.select_one('.event-page-description')
    if not section:
        raise ValueError('Laughing Buddha mic guidelines are missing; review required.')
    description = _clean(section.get_text(' ', strip=True))
    events = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            events.extend(_events(json.loads(script.get_text())))
        except (ValueError, TypeError):
            raise ValueError('Laughing Buddha dated event data changed; review required.') from None
    if not events:
        raise ValueError('Laughing Buddha has no confirmed dated mic occurrences; review required.')
    rows = []
    for event in sorted(events, key=lambda item: str(item.get('startDate', ''))):
        title = _clean(event.get('name'))
        if not _MIC.search(title) or _ONLINE.search(title):
            raise ValueError('Laughing Buddha mic event type changed; review required.')
        try:
            timestamp = str(event['startDate'])
            if 'T' not in timestamp:
                raise ValueError
            start = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            if start.tzinfo is None:
                raise ValueError
            start = start.astimezone(NY)
        except (KeyError, ValueError):
            raise ValueError('Laughing Buddha mic date/time is incomplete; review required.') from None
        if start.date() < today:
            continue
        venue, address = _venue_address(event, description, official_venues)
        # A series title's usual time can differ from its explicit holiday or
        # festival occurrence. Display the actual timestamp in the time column.
        name = re.sub(r'^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+\d{1,2}:\d{2}\s*[AP]M\s*[-–—]?\s*', '', title, flags=re.I)
        name = re.split(r'\s*@\s*', name, maxsplit=1)[0].strip()
        minimum = '1 item minimum' if re.search(r'\bone[ -]item minimum\b', description, re.I) else ''
        spots = re.search(r'\bSpots are\s+(\d+(?:\.\d+)?)\s+minutes?\b', description, re.I)
        # Keep an occurrence's identity when its start time changes. The slot
        # distinguishes multiple explicitly published sessions on one date.
        slot = sum(row['date'] == start.date().isoformat() for row in rows)
        row = {'external_id': f'laughingbuddha:{series[1]}:{start.date().isoformat()}:{slot}',
               'name': 'Laughing Buddha ' + name, 'venue': venue, 'address': address,
               'borough': 'Manhattan', 'date': start.date().isoformat(), 'frequency': 'one-time',
               'start_time': start.strftime('%H:%M'), 'signup_url': url,
               'signup_method': 'Online registration; check in before the mic',
               'cost': _performer_cost(event, description), 'purchase_minimum': minimum,
               'notes': 'Explicit dated registration on Laughing Buddha’s official box office; no recurring dates inferred. Series: ' + title,
               'status': 'cancelled' if str(event.get('eventStatus', '')).endswith('EventCancelled') else 'scheduled'}
        if spots:
            row['set_minutes'] = spots[1]
        rows.append(row)
    return rows


async def fetch_feed(fetcher, today=None):
    """Fetch a complete bounded snapshot; any failed page aborts the import."""
    try:
        async with asyncio.timeout(120):
            index = await fetcher.fetch(BOX_OFFICE)
            venues = await fetcher.fetch(VENUES_URL)
            venue_text = _clean(BeautifulSoup(venues['content'], 'html.parser').get_text(' ', strip=True))
            rows = []
            for url in event_links(index['content']):
                result = await fetcher.fetch(url)
                rows.extend(calendar_rows(result['content'], url, venue_text, today=today))
            if not rows:
                raise ValueError('No upcoming Laughing Buddha mic dates found; existing mics kept for review.')
            identities = [row['external_id'] for row in rows]
            if len(identities) != len(set(identities)):
                raise ValueError('Duplicate Laughing Buddha dated occurrences need review.')
            return json.dumps(rows, sort_keys=True).encode()
    except TimeoutError:
        raise ValueError('Laughing Buddha calendar fetch timed out; existing mics kept for review.') from None
