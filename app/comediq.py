"""Read Comediq's public JSON feed as conservative, dated NYC occurrences.

Only explicit weekly schedules are expanded. Public descriptions and rules are
used to reject exceptions, never copied into our listings or executed.
"""
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

FEED_URL = 'https://comediq.us/mics.json'
NY = ZoneInfo('America/New_York')
BOROUGHS = {'manhattan': 'Manhattan', 'brooklyn': 'Brooklyn', 'queens': 'Queens',
            'bronx': 'Bronx', 'staten island': 'Staten Island'}
REQUIRED = {'id', 'openMic', 'day', 'startTime', 'venueName', 'borough', 'location', 'city', 'frequency'}


def supports(url):
    return url == FEED_URL


def text(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def _exception(row):
    name = text(row.get('openMic')).lower()
    rules = ' '.join(text(row.get(k)) for k in ('otherRules', 'signUpInstructions', 'frequencyCustomText'))
    if re.search(r'\b(?:improv|poetry)\b', name):
        return 'not a stand-up mic'
    if re.search(r'\b(?:show[ -]dependent|closed|cancelled|canceled|postponed|final|resumes?|skips?)\b', name):
        return 'conditional or exceptional schedule'
    # A date without a year cannot safely establish when an exception expires.
    date_pattern = (r'\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}\b|\b(?:jan(?:uary)?|feb(?:ruary)?|march|april|may|june|july|'
                    r'aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}\b')
    if re.search(date_pattern, name + ' ' + rules, re.I):
        return 'dated exception needs review'
    if re.search(r'\b(?:every other|bi[ -]?weekly|monthly|every (?:1st|2nd|3rd|4th|last)|'
                 r'(?:is|are) cancell?ed|not running|final mic|last mic)\b', rules, re.I):
        return 'conditional or exceptional schedule'
    return None


def _street_address(value):
    address = text(value)
    civic = re.search(r'(?:^|,\s*)(\d+(?:-\d+)?[A-Za-z]?\s.+)$', address)
    if not civic:
        return None
    address = civic[1]
    # Require an explicit New York state suffix, not just a NYC city selection.
    suffix = re.search(r'(?:,?\s+)(?:NY|New York)(?:,?\s+\d{5}(?:-\d{4})?)?(?:,?\s+USA)?$', address, re.I)
    if not suffix:
        return None
    street = address[:suffix.start()].rstrip(', ')
    street = re.sub(r'(?:,?\s+)(?:New York|NY|Manhattan|Brooklyn|Queens|Bronx|Staten Island|'
                    r'Astoria|Long Island City|Flushing|Ridgewood|Woodside|Jackson Heights|Jamaica)$', '', street, flags=re.I)
    # Comma-delimited locality names are never part of the street address.
    return street.split(',', 1)[0].strip()


def _fee(value):
    """Extract only an explicit base fee; retain the complete fee label separately."""
    if re.fullmatch(r'free|no cover', value, re.I):
        return 0.0
    match = re.fullmatch(r'\$?\s*(\d+(?:\.\d{1,2})?)(?:\s+cash(?: only)?)?', value, re.I)
    if not match:
        match = re.fullmatch(r'\$\s*(\d+(?:\.\d{1,2})?)(?:\s+cash(?: only)?)?\s*(?:\+|and)\s*'
                             r'(?:1|one|2|two)\s+(?:drink|item)s?(?:\s+(?:min|minimum))?', value, re.I)
    return float(match[1]) if match else None


def _instagram(value):
    handle = text(value)
    if re.fullmatch(r'@?[A-Za-z0-9_.]{1,30}', handle):
        return 'https://www.instagram.com/' + handle.lstrip('@') + '/'
    parsed = urlsplit(handle)
    if parsed.scheme == 'https' and parsed.hostname in ('instagram.com', 'www.instagram.com') and not parsed.username and not parsed.password:
        handle = parsed.path.strip('/')
        if re.fullmatch(r'[A-Za-z0-9_.]{1,30}', handle):
            return 'https://www.instagram.com/' + handle + '/'
    return ''


def extract_feed(content, config, today=None):
    from .parsers import normalize_rows, parse_days, parse_time, safe_link

    data = json.loads(content)
    if not isinstance(data, list) or not data:
        raise ValueError('Comediq feed must be a nonempty array of public mic records.')
    today = today or datetime.now(NY).date()
    if isinstance(today, datetime):
        today = today.astimezone(NY).date()
    if not isinstance(today, date):
        raise ValueError('A valid New York calendar date is required.')
    raw, warnings, fee_labels = [], [], {}
    excluded = Counter()
    malformed = 0
    for index, item in enumerate(data, 1):
        try:
            if not isinstance(item, dict) or 'city' not in item or not isinstance(item['city'], str):
                raise ValueError('record or city field is missing or invalid')
            if item['city'].strip().casefold() != 'new york':
                excluded['outside New York City'] += 1
                continue
            if REQUIRED - item.keys():
                raise ValueError('required feed fields are missing')
            if any(item.get(k) is not None and not isinstance(item[k], str) for k in REQUIRED | {'hosts', 'instagramHandle', 'otherRules', 'signUpInstructions', 'frequencyCustomText', 'status', 'cost', 'stageTime', 'neighborhood', 'signupUrl'}):
                raise ValueError('a public text field has an unexpected type')
            if text(item['frequency']).lower() != 'weekly':
                excluded['nonweekly schedule'] += 1
                continue
            reason = _exception(item)
            if reason:
                excluded[reason] += 1
                continue
            status = text(item.get('status')).lower()
            if status in ('closed', 'cancelled', 'canceled', 'postponed', 'paused', 'inactive', 'removed'):
                excluded['inactive listing'] += 1
                continue
            if status not in ('', 'verified', 'trial', 'active'):
                raise ValueError('listing status is not recognized')
            borough = BOROUGHS.get(text(item['borough']).lower())
            address = _street_address(item['location'])
            if not all((text(item['openMic']), text(item['venueName']), borough, address)):
                excluded['incomplete or unconfirmed NYC venue'] += 1
                continue
            uid = str(UUID(text(item['id'])))
            days = parse_days(item['day'])
            if len(days) != 1:
                raise ValueError('exactly one weekly day is required')
            start = parse_time(item['startTime'])
            if not start:
                raise ValueError('start time is missing or ambiguous')
            event_date = today + timedelta(days=(days[0] - today.weekday()) % 7)
            coordinates = {}
            lat, lng = item.get('latitude'), item.get('longitude')
            if lat is not None or lng is not None:
                if isinstance(lat, bool) or isinstance(lng, bool) or lat is None or lng is None:
                    raise ValueError('coordinates must be supplied as a pair')
                lat, lng = float(lat), float(lng)
                if not math.isfinite(lat) or not math.isfinite(lng):
                    raise ValueError('coordinates must be finite')
                if not (40.47 <= lat <= 40.93 and -74.27 <= lng <= -73.68):
                    excluded['coordinates outside NYC'] += 1
                    continue
                coordinates = {'latitude': lat, 'longitude': lng}
            external_id = f'comediq:{uid}:{event_date.isoformat()}'
            fee = text(item.get('cost'))
            fee_labels[external_id] = fee
            raw.append({'external_id': external_id, 'name': text(item['openMic']), 'display_name': text(item['openMic']),
                        'venue': text(item['venueName']), 'address': address, 'borough': borough,
                        'neighborhood': text(item.get('neighborhood')), 'date': event_date.isoformat(),
                        'start_time': start, 'host_names': text(item.get('hosts')),
                        'host_socials': _instagram(item.get('instagramHandle')),
                        'signup_url': safe_link(item.get('signupUrl')) or f'https://comediq.us/open-mics#{uid}',
                        'cost': _fee(fee), 'set_minutes': text(item.get('stageTime')), 'status': 'scheduled',
                        'notes': 'Listed in Comediq’s public feed. Confirm the schedule and signup details at the source before traveling.',
                        **coordinates})
        except (ValueError, TypeError, OverflowError) as exc:
            malformed += 1
            if len(warnings) < 40:
                warnings.append(f'Comediq record {index}: {exc}.')
    rows, normalization_warnings, skipped = normalize_rows(raw, config.model_copy(update={'mapping': {}, 'defaults': {}}))
    for row in rows:
        row['cost_text'] = fee_labels[row['external_id']][:100]
    warnings.extend(normalization_warnings)
    if excluded:
        warnings.append('Intentionally excluded: ' + '; '.join(f'{n} {reason}' for reason, n in sorted(excluded.items())) + '.')
    return {'rows': rows, 'warnings': warnings, 'skipped': malformed + skipped,
            'excluded': sum(excluded.values()), 'raw_count': len(data), 'detected_kind': 'comediq',
            'sheets': [], 'headers': list(raw[0]) if raw else []}
