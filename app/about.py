"""Administrator-editable About copy, rendered safely for visitors and search."""
import json
from pathlib import Path

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

KEY = 'about_content_v1'

class AboutContent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(default='About the NYC open mic map', min_length=1, max_length=120)
    intro: str = Field(default='Find stand up comedy open mics across Manhattan, Brooklyn, Queens, the Bronx, and Staten Island. Search by day, time, or venue.', max_length=1200)
    why_heading: str = Field(default='Why I built it', max_length=120)
    why: str = Field(default='I wanted one simple place to find a mic: a map, useful details, and no account needed to browse.', max_length=2400)
    how_heading: str = Field(default='How listings work', max_length=120)
    how: str = Field(default='Listings come from public directories and approved hosts. A claimed badge means a host can edit that mic; it is not a guarantee. Every listing shows its last update. Confirm details with the venue before going.', max_length=2400)
    hosts: str = Field(default='Hosts can add their names and social links. Only approved hosts get editing accounts.', max_length=1200)
    seo_description: str = Field(default='Why NYC Open Mic Master List exists: a simple map of New York City comedy open mics, with host-managed listings and visible update times.', min_length=1, max_length=300)


def read_content(store):
    with store.connect() as c:
        row = c.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()
    return AboutContent(**json.loads(row['value'])) if row else AboutContent()


def save_content(store, content):
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (KEY, content.model_dump_json()))


def render(store):
    content = read_content(store)
    document = BeautifulSoup((Path(__file__).resolve().parents[1] / 'assets/about.html').read_text(), 'html.parser')
    for name, value in content.model_dump().items():
        if name != 'seo_description':
            document.find(id='about-' + name).string = value
    document.find('meta', attrs={'name': 'description'})['content'] = content.seo_description
    document.find('meta', attrs={'property': 'og:description'})['content'] = content.seo_description
    structured = document.find('script', attrs={'type': 'application/ld+json'})
    data = json.loads(structured.string)
    data['description'] = content.seo_description
    # Escape HTML-significant characters before placing JSON in a script element.
    structured.string = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return str(document)
