"""Build the reviewed October 6 user-screenshot snapshot; never writes to the database."""
import csv
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'data' / 'imports'
STEM = 'user-mics-2026-10-06'
DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
review = json.loads((DIRECTORY / (STEM + '-review.json')).read_text())
venues = {r['screenshot_venue']: r for r in json.loads((DIRECTORY / (STEM + '-venues.json')).read_text())['venues']}
rows = []
for source in review['rows']:
    if source['classification'] != 'new':
        continue
    location = venues.get(source['venue']) or source['venue_location'].get('usable_location') or {}
    row = {key: location.get(key) or '' for key in ('address', 'borough', 'neighborhood', 'latitude', 'longitude')}
    row.update(external_id=f'user-screenshots-2026-10-06-{source["input_index"]}',
               name=source['name'], venue=location.get('venue', source['venue']),
               weekday=DAYS[source['weekday']], start_time=source['start_time'], date='',
               frequency='non-weekly' if source['non_weekly'] else 'weekly', signup_url='',
               notes=f'User-submitted schedule image {source["image_filename"]}, received October 6, 2026. The image publication date is unknown; confirm current details before traveling. ' + source['notes'])
    if source['venue'] == 'The Improv':
        row.update(borough='Manhattan', notes=row['notes']+' Address unconfirmed; shown in the list without a guessed map pin. Comediq identifies this venue as East Village.')
    if source['venue'] == 'UCB':
        row['notes'] += ' The official BYOT page lists the second and fourth Sundays at 9 PM; this is twice monthly, not every other week. Venue address checked on the official page.'
        row['signup_url'] = 'https://ucbcomedy.com/show/byot-08-23-26/'
    if source['venue'] == 'Crystal Lake':
        row['notes'] += ' Official venue website confirms 647 Grand Street, Brooklyn. Exact performance dates remain unconfirmed.'
    if source['venue'] == 'The PIT':
        # The current official calendar changes time on November 10. Preserve its
        # explicit occurrences rather than advertising 5 PM indefinitely.
        row.update(venue='The PIT LAB', address='156 W. 29th Street, Floor 2, New York, NY 10001', borough='Manhattan',
                   latitude=40.747406157082, longitude=-73.992375803176,
                   signup_url='https://thepit-nyc.com/events/goat/')
        row['notes'] += ' Official venue calendar checked October 6: Tuesdays at 5 PM through November 3; 4:30 PM at The Fishbowl starting November 10, through December 29. These are dated occurrences of a recurring mic.'
        current = date(2026, 10, 6)
        while current <= date(2026, 12, 29):
            occurrence = dict(row, date=current.isoformat(), weekday='', external_id=row['external_id']+'-'+current.isoformat())
            if current >= date(2026, 11, 10):
                occurrence.update(start_time='16:30', venue='The PIT Fishbowl')
            rows.append(occurrence)
            current += timedelta(days=7)
        continue
    rows.append(row)

fields = ['external_id', 'name', 'venue', 'address', 'borough', 'neighborhood', 'weekday', 'date', 'start_time', 'frequency', 'latitude', 'longitude', 'signup_url', 'notes']
target = DIRECTORY / (STEM + '-new.csv')
with target.open('w', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
report = ['# October 6 screenshot import review', '',
          '90 screenshot entries checked against 829 public listings. 32 missing mic sessions are represented by 44 import rows: 31 recurring/irregular entries plus 13 confirmed dated occurrences of The Goated Mic. Its official calendar changes time on November 10.', '',
          '38 existing name/alias matches are not imported again. The 20 cases below remain for administrator review; no existing listing is overwritten, hidden or deleted.', '',
          'The source is a user-uploaded snapshot, not a continuously checked feed. New entries remain unclaimed. Non-weekly entries have no invented calendar dates. The cropped blue image is provisionally Monday and retains that caveat. Unknown coordinates stay blank for the normal address geocoder; The Improv has no confirmed address.', '',
          '## Review existing listings', '', '| Screenshot mic | Day / time | Issue | Existing listing IDs |', '|---|---|---|---|']
for source in review['rows']:
    if source['classification'] not in ('conflict', 'likely_duplicate'):
        continue
    descriptions = '; '.join(f"{m['name']} — {m['venue']} {m['start_time']}" for m in source['matches'][:3])
    report.append(f"| {source['name']} | {DAYS[source['weekday']]} {source['start_time']} | {source['classification'].replace('_',' ')}: {descriptions} | {', '.join(source['existing_ids'])} |")
(DIRECTORY / (STEM + '-review.md')).write_text('\n'.join(report) + '\n')
print(f'Prepared {len(rows)} rows for 32 missing mic sessions: {target}')
