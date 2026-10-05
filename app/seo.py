"""Crawlable views of the published directory; the interactive finder stays separate."""
import html
import json
import math
import re
from datetime import date, datetime, timezone
from urllib.parse import quote, urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response

BASE = 'https://nycopenmicmasterlist.com'
BRAND = 'NYC Open Mic Master List'
NY = ZoneInfo('America/New_York')
DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
BOROUGHS = {'manhattan': 'Manhattan', 'brooklyn': 'Brooklyn', 'queens': 'Queens',
            'bronx': 'Bronx', 'staten-island': 'Staten Island'}
PAGE_SIZE = 40


def esc(value):
    return html.escape(str(value if value is not None else ''), quote=True)


def safe_url(value):
    try:
        parsed = urlsplit(str(value or ''))
        return str(value) if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username else ''
    except ValueError:
        return ''


def valid_date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else None
    except ValueError:
        return None


def date_label(value):
    parsed = valid_date(value)
    return f'{parsed:%A, %B} {parsed.day}, {parsed.year}' if parsed else 'Date not listed'


def time_label(value):
    try:
        parsed = datetime.strptime(value, '%H:%M')
        return f'{parsed.hour % 12 or 12}:{parsed.minute:02d} {"PM" if parsed.hour >= 12 else "AM"}'
    except (ValueError, TypeError):
        return 'Time not listed'


def entry_fee(row):
    if row.get('cost_text'):
        return row['cost_text']
    value = row.get('cost')
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return 'Free' if value == 0 else f'${value:g}'
    return 'Not listed'


def timestamp(row):
    values = []
    for field in ('updated_at', 'curated_at'):
        try:
            number = float(row.get(field) or 0)
            if math.isfinite(number) and number > 0:
                values.append(number)
        except (TypeError, ValueError):
            pass
    try:
        return datetime.fromtimestamp(max(values), timezone.utc) if values else None
    except (OverflowError, OSError, ValueError):
        return None


def updated_html(row):
    updated = timestamp(row)
    if not updated:
        return 'Last updated: not recorded'
    local = updated.astimezone(NY)
    return f'Last updated: <time datetime="{updated.isoformat()}">{local:%B} {local.day}, {local.year} at {local:%I:%M %p %Z}</time>'


def generated_source_date(row):
    return (row.get('frequency') != 'one-time' and 'date' not in (row.get('overridden_fields') or [])
            and any(source.get('url') == 'https://comediq.us/mics.json' for source in row.get('sources', [])))


def historical(row):
    event_date = valid_date(row.get('date'))
    return bool(event_date and not generated_source_date(row) and event_date < datetime.now(NY).date())


def recurrence(row):
    frequency = row.get('frequency')
    if frequency == 'one-time':
        return 'Pop-up mic (not recurring)'
    if frequency == 'biweekly' or (not frequency and re.search(
            r'\b(?:bi[ -]?weekly|every other (?:week|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b',
            str(row.get('name') or '') + ' ' + str(row.get('notes') or ''), re.I)):
        return 'Biweekly (every other week)'
    weekday = row.get('weekday')
    if (frequency == 'weekly' or generated_source_date(row)
            or re.search(r'\b(?:weekly|every week)\b', str(row.get('notes') or ''), re.I)
            or (not row.get('date') and type(weekday) is int and 0 <= weekday < 7)):
        return 'Weekly'
    return 'Schedule unconfirmed'


def schedule(row):
    actual_date = valid_date(row.get('date'))
    weekday = row.get('weekday')
    if actual_date and not generated_source_date(row):
        day = date_label(row['date'])
    elif type(weekday) is int and 0 <= weekday < 7:
        day = DAYS[weekday]
    else:
        day = 'Day not listed'
    return f'{day} at {time_label(row.get("start_time"))} · {recurrence(row)}'


def status(row):
    return 'Host claimed' if row.get('claimed') else 'Venue confirmed' if row.get('venue_confirmed') else 'Unclaimed'


def mic_path(row):
    return '/mics/' + quote(str(row['id']), safe='')


def published(store):
    # public_data applies administrator visibility decisions and public overlays.
    rows = [row for row in store.public_data()['listings'] if row.get('id') and not row.get('hidden')]
    return sorted(rows, key=lambda row: (str(row.get('borough', '')), str(row.get('venue', '')).casefold(),
                                         str(row.get('name', '')).casefold(), str(row.get('date', '')),
                                         str(row.get('weekday', '')), str(row.get('start_time', '')), str(row['id'])))


def source_links(row):
    links, seen = [], set()
    for source in row.get('sources', []):
        url = safe_url(source.get('url'))
        if url and url not in seen:
            seen.add(url)
            links.append(f'<a href="{esc(url)}" rel="noopener noreferrer">{esc(source.get("name") or urlsplit(url).hostname)}</a>')
    return ', '.join(links) if links else 'No public source link listed'


def breadcrumbs(items):
    return {'@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': i + 1, 'name': name, 'item': BASE + path}
        for i, (name, path) in enumerate(items)]}


def page_html(title, description, path, body, graph, noindex=False):
    structured = json.dumps({'@context': 'https://schema.org', '@graph': graph}, ensure_ascii=False)
    structured = structured.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    robots = 'noindex, follow' if noindex else 'index, follow, max-image-preview:large'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} | {BRAND}</title><meta name="description" content="{esc(description)}">
<meta name="robots" content="{robots}"><link rel="canonical" href="{esc(BASE + path)}">
<meta property="og:type" content="website"><meta property="og:site_name" content="{BRAND}">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(BASE + path)}"><meta name="twitter:card" content="summary">
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/seo.css">
<script src="/assets/visitors.js" defer></script>
<script type="application/ld+json">{structured}</script></head><body>
<a class="skip" href="#content">Skip to content</a>
<header><a href="/">{BRAND}</a><nav aria-label="Main navigation"><a href="/">Map &amp; mic finder</a><a href="/nyc-comedy-open-mics">All mics</a><a href="/about">About</a></nav></header>
<main id="content">{body}</main>
<footer><a href="/submit">Submit a mic</a> · <a href="/owner">Host sign in</a> · <a href="/">Return to the map</a></footer>
</body></html>'''


def borough_links(rows):
    available = {row.get('borough') for row in rows}
    return '<nav class="boroughs" aria-label="Browse open mics by borough">' + ' '.join(
        f'<a href="/open-mics/{slug}">{name}</a>' for slug, name in BOROUGHS.items() if name in available) + '</nav>'


def card(row):
    note = '<p class="notice">Source details may be out of date. Check the source before going.</p>' if row.get('stale') else ''
    event_status = str(row.get('status') or 'scheduled').capitalize()
    return f'''<li><article><h2><a href="{esc(mic_path(row))}">{esc(row.get('name'))}</a></h2>
<p><strong>{esc(row.get('venue'))}</strong> · {esc(row.get('borough'))}<br>{esc(row.get('address') or 'Address not listed')}</p>
<p>{esc(schedule(row))}</p><p>{esc(event_status)} · {esc(status(row))}</p>
<p class="updated">{updated_html(row)}</p>{note}
<a href="/?mic={quote(str(row['id']), safe='')}">Find this mic on the map</a></article></li>'''


def directory(rows, borough=None, page=1):
    # Past dated occurrences remain accessible by URL and in public_data. They
    # are not presented as current opportunities on search landing pages.
    rows = [row for row in rows if not historical(row)]
    selected = [row for row in rows if row.get('borough') == borough] if borough else rows
    total = len(selected)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    if not isinstance(page, int) or page < 1 or page > pages:
        raise HTTPException(404, 'Directory page not found')
    base_path = '/open-mics/' + next(slug for slug, name in BOROUGHS.items() if name == borough) if borough else '/nyc-comedy-open-mics'
    path = base_path + (f'?page={page}' if page > 1 else '')
    title = f'{borough} comedy open mics' if borough else 'NYC comedy open mics'
    if page > 1:
        title += f' — page {page}'
    place = borough or 'New York City'
    description = f'Browse comedy open mic listings in {place}: venues, addresses, schedules, host status, and last updates. Open any mic in the map finder.'
    subset = selected[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    trail = [('Mic finder', '/'), ('NYC comedy open mics', '/nyc-comedy-open-mics')]
    if borough:
        trail.append((f'{borough} open mics', base_path))
    graph = [{'@type': 'CollectionPage', '@id': BASE + path, 'url': BASE + path,
              'name': title, 'description': description, 'mainEntity': {
                  '@type': 'ItemList', 'numberOfItems': len(subset), 'itemListElement': [
                      {'@type': 'ListItem', 'position': (page - 1) * PAGE_SIZE + i + 1,
                       'name': row.get('name'), 'url': BASE + mic_path(row)} for i, row in enumerate(subset)]}}, breadcrumbs(trail)]
    intro = (f'Browse published stand-up comedy open mic listings in {esc(place)}. '
             'Use the <a href="/">mic finder</a> to search by weekday, specific date, time, and location. No account is needed to browse.')
    body = f'<h1>{esc(title)}</h1><p>{intro}</p>' + borough_links(rows)
    body += f'<p>{total} published listing{"s" if total != 1 else ""} with recurring or upcoming dated schedules' + (f' · Page {page} of {pages}' if pages > 1 else '') + '.</p>'
    body += ('<ol class="listings">' + ''.join(card(row) for row in subset) + '</ol>') if subset else '<p>No published mics are listed here yet. <a href="/submit">Submit a mic you know about.</a></p>'
    if pages > 1:
        body += '<nav class="pagination" aria-label="Directory pages">'
        if page > 1:
            previous = base_path + (f'?page={page - 1}' if page > 2 else '')
            body += f'<a rel="prev" href="{esc(previous)}">Previous page</a> '
        if page < pages:
            body += f'<a rel="next" href="{esc(base_path)}?page={page + 1}">Next page</a>'
        body += '</nav>'
    body += '''<section aria-labelledby="listing-guide"><h2 id="listing-guide">Reading the listings</h2>
<p><strong>Host claimed</strong> means an approved host can edit the listing. <strong>Venue confirmed</strong> means the mic appears on an official venue source; a host can still claim it. Neither label guarantees a mic will run.</p>
<p><strong>Weekly</strong> mics repeat every week. <strong>Biweekly</strong> mics run every other week; check the confirmed date or source for the correct week. A <strong>pop-up mic</strong> is not recurring.</p>
<p>Times are in New York time. Check each listing’s last update, entry fee, and source before traveling. Cancelled or postponed listings are labeled, and outdated listings can be reported from the mic finder.</p></section>'''
    return page_html(title, description, path, body, graph, noindex=not selected)


def mic_detail(row):
    path = mic_path(row)
    title = f'{row.get("name")} at {row.get("venue")} — {row.get("borough")} open mic'
    description = f'{row.get("name")} at {row.get("venue")} in {row.get("borough")}. {schedule(row)}. View address, entry details, host status, sources, and last update.'
    borough_path = next(('/open-mics/' + slug for slug, name in BOROUGHS.items() if name == row.get('borough')), '')
    trail = [('Mic finder', '/'), ('NYC comedy open mics', '/nyc-comedy-open-mics')]
    if borough_path:
        trail.append((row['borough'] + ' open mics', borough_path))
    trail.append((str(row.get('name')), path))
    structured = {'@type': 'WebPage', '@id': BASE + path, 'url': BASE + path, 'name': title, 'description': description}
    updated = timestamp(row)
    if updated:
        structured['dateModified'] = updated.isoformat()
    fields = [('Venue', row.get('venue')), ('Address', row.get('address') or 'Address not listed'),
              ('Borough', row.get('borough')), ('Schedule', schedule(row)),
              ('Status', str(row.get('status') or 'scheduled').capitalize()), ('Listing verification', status(row)),
              ('Host', row.get('host_names') or 'Host not listed'),
              ('Entry fee', entry_fee(row)),
              ('Signup time', time_label(row.get('signup_time')))]
    if row.get('neighborhood'):
        fields.insert(3, ('Neighborhood', row['neighborhood']))
    if row.get('set_minutes'):
        fields.append(('Stage time', str(row['set_minutes']) + ' minutes'))
    if row.get('purchase_minimum'):
        fields.append(('Purchase minimum', row['purchase_minimum']))
    if recurrence(row).startswith('Biweekly'):
        fields.append(('Confirmed alternate-week date', date_label(row.get('recurrence_anchor')) if valid_date(row.get('recurrence_anchor')) else 'Not listed; confirm the week with the host or venue'))
    if row.get('excluded_dates'):
        fields.append(('Does not run on', '; '.join(date_label(value) for value in row['excluded_dates'] if valid_date(value))))
    body = f'<h1>{esc(row.get("name"))}</h1><p><a href="/?mic={quote(str(row["id"]), safe="")}">Open this mic in the map finder</a></p>'
    if historical(row):
        body += '<p class="notice"><strong>Past dated listing.</strong> The listed date has passed. This page records that occurrence; it does not confirm a future mic. <a href="/nyc-comedy-open-mics">Browse current schedules.</a></p>'
    body += '<dl>' + ''.join(f'<dt>{esc(label)}</dt><dd>{esc(value)}</dd>' for label, value in fields) + '</dl>'
    body += f'<p class="updated">{updated_html(row)}</p>'
    if row.get('stale'):
        body += '<p class="notice">Source details may be out of date. This listing remains available for review; check the source before going.</p>'
    if row.get('conflicts'):
        body += '<p class="notice">Sources disagree on some details. Check the source or venue before going.</p>'
    if row.get('notes'):
        body += f'<section><h2>Mic notes</h2><p class="notes">{esc(row["notes"])}</p></section>'
    signup = safe_url(row.get('signup_url'))
    if signup:
        body += f'<p><a href="{esc(signup)}" rel="nofollow ugc noopener noreferrer">Signup or source details</a></p>'
    socials = [safe_url(value) for value in str(row.get('host_socials') or '').split()]
    if any(socials):
        body += '<section><h2>Host links</h2><ul>' + ''.join(
            f'<li><a href="{esc(url)}" rel="nofollow ugc noopener noreferrer">{esc(url)}</a></li>' for url in socials if url) + '</ul></section>'
    body += f'<section><h2>Listing sources</h2><p>{source_links(row)}</p><p>Times are in New York time. Host and venue labels describe who maintains or publishes the information; check current details before traveling.</p></section>'
    if valid_date(row.get('date')) and not generated_source_date(row):
        body += f'<p><a href="/?date={esc(row["date"])}">Find mics for {esc(date_label(row["date"]))}</a></p>'
    if borough_path:
        body += f'<p><a href="{borough_path}">More {esc(row["borough"])} comedy open mics</a></p>'
    return page_html(title, description, path, body, [structured, breadcrumbs(trail)], noindex=historical(row))


def sitemap_xml(rows):
    rows = [row for row in rows if not historical(row)]
    root = Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    paths = [('/', None), ('/about', None)]
    if rows:
        paths.append(('/nyc-comedy-open-mics', None))
    paths.extend((f'/nyc-comedy-open-mics?page={page}', None)
                 for page in range(2, math.ceil(len(rows) / PAGE_SIZE) + 1))
    for slug, name in BOROUGHS.items():
        count = sum(row.get('borough') == name for row in rows)
        if count:
            paths.append(('/open-mics/' + slug, None))
            paths.extend((f'/open-mics/{slug}?page={page}', None)
                         for page in range(2, math.ceil(count / PAGE_SIZE) + 1))
    paths.extend((mic_path(row), timestamp(row)) for row in rows)
    for path, updated in paths:
        node = SubElement(root, 'url')
        SubElement(node, 'loc').text = BASE + path
        if updated:
            SubElement(node, 'lastmod').text = updated.isoformat()
    return tostring(root, encoding='utf-8', xml_declaration=True)


def register(app, store):
    @app.get('/nyc-comedy-open-mics', response_class=HTMLResponse)
    def all_mics(page: int = 1):
        return HTMLResponse(directory(published(store), page=page), headers={'Cache-Control': 'public, max-age=60'})

    @app.get('/open-mics/{borough}', response_class=HTMLResponse)
    def borough_mics(borough: str, page: int = 1):
        if borough not in BOROUGHS:
            raise HTTPException(404, 'Borough not found')
        rows = published(store)
        headers = {'Cache-Control': 'public, max-age=60'}
        if not any(row.get('borough') == BOROUGHS[borough] and not historical(row) for row in rows):
            headers['X-Robots-Tag'] = 'noindex, follow'
        return HTMLResponse(directory(rows, BOROUGHS[borough], page), headers=headers)

    @app.get('/mics/{mic_id}', response_class=HTMLResponse)
    def mic(mic_id: str):
        row = next((row for row in published(store) if str(row['id']) == mic_id), None)
        if not row:
            raise HTTPException(404, 'Mic not found')
        headers = {'Cache-Control': 'public, max-age=60'}
        if historical(row):
            headers['X-Robots-Tag'] = 'noindex, follow'
        return HTMLResponse(mic_detail(row), headers=headers)

    @app.get('/sitemap.xml')
    def sitemap():
        return Response(sitemap_xml(published(store)), media_type='application/xml',
                        headers={'Cache-Control': 'public, max-age=300'})
