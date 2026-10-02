"""Source provenance is independent of host ownership."""
import json
from pathlib import Path
from urllib.parse import urlsplit

OFFICIAL_HOSTS = frozenset(urlsplit(row['url']).hostname.removeprefix('www.')
    for row in json.loads(Path(__file__).with_name('club_catalog.json').read_text()))

def official_venue_source(url):
    try:
        parsed = urlsplit(url)
        return parsed.scheme in ('http', 'https') and (parsed.hostname or '').removeprefix('www.') in OFFICIAL_HOSTS
    except ValueError:
        return False
