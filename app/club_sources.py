"""Conservative readers for official public weekly open-mic schedules.

Times always come from the fetched page. Unrecognized layouts fail for review;
they never silently replace an existing schedule with an empty import.
"""
import re
from urllib.parse import urlsplit

DAYS = 'Monday Tuesday Wednesday Thursday Friday Saturday Sunday'.split()
TIME = r'\d{1,2}(?:[:.]\d{2})?\s*[ap]m'
SOURCES = {
    ('newyorkcomedyclub.com', '/open-mic'): 'nycc',
    ('comicstriplive.com', ''): 'comicstrip',
    ('bkmadecomedy.com', ''): 'bkmade',
    ('eastvillecomedy.com', '/pages/open-mics'): 'eastville',
    ('qedastoria.com', '/collections/open-mic-nights'): 'qed',
    ('comedymob.com', ''): 'mob',
    ('comedyshopnyc.com', '/location/comedy-shop-nyc'): 'shop',
}
def source_kind(url):
    p = urlsplit(url)
    return SOURCES.get((p.hostname.removeprefix('www.') if p.hostname else '', p.path.rstrip('/')))

def calendar_rows(soup, url):
    kind = source_kind(url)
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    text = re.sub(r'\s+', ' ', soup.get_text(' ', strip=True))
    rows = []
    def add(name, venue, address, borough, day, start, **extra):
        # Index distinguishes multiple daily sessions while surviving time edits.
        slot = sum(r['weekday'] == day and r['venue'] == venue for r in rows)
        rows.append(dict(external_id=f'{kind}:{venue}:{day}:{slot}', name=name,
                         venue=venue, address=address, borough=borough, weekday=day,
                         start_time=start.replace('.', ':'), signup_url=url,
                         notes='Official weekly schedule. Check the venue for date-specific changes.',
                         **extra))
    if kind == 'nycc':
        for day, start, room in re.findall(
                rf'\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)s?\s+(?:at\s+)?({TIME})\s+at\s+(Midtown|East Village)', text, re.I):
            address = {'midtown':'241 East 24th Street', 'east village':'85 East 4th Street'}[room.lower()]
            if address.lower() not in text.lower():
                raise ValueError('NYCC venue address changed; review required.')
            add('New York Comedy Club Open Mic', 'New York Comedy Club - '+room.title(),
                address+', New York, NY', 'Manhattan', day, start,
                cost='$5' if re.search(r'\$5 to perform', text, re.I) else '',
                signup_method='In person' if re.search(r'signups are in-person', text, re.I) else '')
    elif kind == 'comicstrip':
        for tr in soup.select('tr'):
            cells = tr.find_all(['td','th'])
            if len(cells) < 2: continue
            name = cells[0].get_text(' ', strip=True)
            schedule = cells[1].get_text(' ', strip=True)
            if not re.search(r'open\s+mic', name, re.I): continue
            t = re.search(TIME, schedule, re.I)
            if not t: raise ValueError('Comic Strip mic time is ambiguous; review required.')
            for day in DAYS:
                if re.search(day, schedule, re.I):
                    add(name.rstrip(':'), 'Comic Strip Live', '1568 Second Ave, New York, NY 10028',
                        'Manhattan', day, t[0])
    elif kind == 'bkmade':
        # Confine extraction to the explicitly labeled mic banner, never SHOWS.
        m = re.search(r'OPEN MICS:\s*(.*?)\s*\|\s*SHOWS:', text, re.I)
        if not m: raise ValueError('BK Made mic banner changed; review required.')
        banner = m[1]
        for segment in banner.split(','):
            match = re.fullmatch(r'\s*([A-Z]+)\s*(?:([-–&])\s*([A-Z]+))?\s+(.*?)\s*', segment, re.I)
            if not match: raise ValueError('BK Made schedule needs review.')
            a, join, b, times = match.groups()
            lookup = {d[:3].lower():i for i,d in enumerate(DAYS)}
            first, last = lookup.get(a[:3].lower()), lookup.get((b or a)[:3].lower())
            if first is None or last is None: raise ValueError('BK Made weekday needs review.')
            days = list(range(first,last+1)) if join in ('-','–') else sorted(set([first,last]))
            meridiem = re.search(r'([ap]m)\s*$', times, re.I)
            if not meridiem: raise ValueError('BK Made time needs review.')
            for i in days:
                for value in times.split('&'):
                    value=value.strip()
                    if not re.search(r'[ap]m',value,re.I): value+=meridiem[1]
                    if not re.fullmatch(TIME,value,re.I): raise ValueError('BK Made time needs review.')
                    add('BK Made Open Mic','BK Made Comedy Club','1241 Halsey St, Brooklyn, NY 11237','Brooklyn',DAYS[i],value)
    elif kind == 'eastville':
        section = re.search(r'OPEN\s+MIC SCHEDULE\s+(.*?)\s+For updates',text,re.I)
        if not section: raise ValueError('EastVille schedule section changed; review required.')
        chunks = re.split(r'\b(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)\b',section[1],flags=re.I)
        for i in range(1,len(chunks)-1,2):
            day, block = chunks[i], chunks[i+1]
            for m in re.finditer(rf'([^:]+):\s*({TIME})\s*[-–]\s*\d{{1,2}}(?::\d{{2}})?\s*(?:[ap]m)?',block,re.I):
                name=m[1].strip()
                if not name: continue
                add(name,'EastVille Comedy Club','487 Atlantic Ave, Brooklyn, NY 11217','Brooklyn',day,m[2],
                    purchase_minimum='1 drink minimum',signup_method='Check venue calendar for this mic')
    elif kind == 'qed':
        section=re.search(r'GENERAL OPEN MIC SCHEDULE(.*?)~~~~',text,re.I)
        if not section: raise ValueError('QED weekly schedule changed; review required.')
        # Explicitly excludes numbered monthly entries, including the no-comedy poetry mic.
        for m in re.finditer(rf'(?<!\w)(MON|TUE|WED|THU|FRI|SAT|SUN)\s*@\s*({TIME})',section[1],re.I):
            if re.search(r'\d(?:st|nd|rd|th)\s*$',section[1][:m.start()],re.I): continue
            day=next(d for d in DAYS if d.lower().startswith(m[1].lower()))
            add('QED Open Mic','QED Astoria','27-16 23rd Avenue, Astoria, NY 11105','Queens',day,m[2],
                cost='Free' if 'free to watch or perform' in text.lower() else '',
                purchase_minimum='$5 purchase' if '$5+ purchase' in text else '')
    elif kind == 'mob':
        section=re.search(r'Weekly Open Mics(.*?)The list opens',text,re.I)
        if not section: raise ValueError('Comedy Mob weekly mic section changed; review required.')
        pattern=rf'\b(SUN|MON|THU)\s+(Sunday Mic|Monday Night Mob|Thursday Mic)\s+(New York Comedy Club\s*[—–-]\s*(?:East 4th St|24th St)|Rodney.s Comedy Club)\s+({TIME})'
        for day,name,venue,start in re.findall(pattern,section[1],re.I):
            if 'East 4th' in venue: venue,address='New York Comedy Club - East Village','85 East 4th Street'
            elif '24th' in venue: venue,address='New York Comedy Club - Midtown','241 East 24th Street'
            else: venue,address="Rodney's Comedy Club",'1118 1st Avenue'
            add('Comedy Mob '+name,venue,address+', New York, NY','Manhattan',
                {'SUN':'Sunday','MON':'Monday','THU':'Thursday'}[day.upper()],start,
                purchase_minimum='1 item minimum',signup_method='Weekly signup form on official site')
    elif kind == 'shop':
        m=re.search(rf'hosts open mics every day at\s*({TIME})\s*&\s*({TIME})',text,re.I)
        if m:
            for day in DAYS:
                for start in m.groups():
                    add('Comedy Shop Open Mic','The Comedy Shop','167 Bleecker Street, New York, NY 10012','Manhattan',day,start)
    if not rows:
        raise ValueError('No confirmed open-mic schedule extracted from official club page; existing mics kept for review.')
    return rows
