# NYC Stand Up Open Mic Master

A working, self-hosted open-mic directory with a source manager, automatic polling,
a five-borough map, public corrections, moderated claims, and scoped host accounts.

**Hosting:** the production site uses Vercel, hosted PostgreSQL, and scheduled
Badslava and Comediq source checks. The standalone preview contains explicitly
fictional listings and does not support real accounts.

## Start on your Mac

1. Unzip the project.
2. Double-click `Start-Mic-List.command`. Python 3.11+ and an internet connection for
   the initial dependency install are required. Alternatively, open Terminal in the
   project folder and run `bash Start-Mic-List.command`.
3. Open `http://127.0.0.1:8000`.
4. Enter a unique admin password when the launcher prompts, then open
   `http://127.0.0.1:8000/admin` and sign in with it.
5. Add a source, preview its extracted listings, and approve the import.

Leave the server window open for scheduled checks. Closing it stops the site and
its worker. This is a local address, not a public website URL.

The admin password is checked on the server and never embedded in the frontend.
No admin password is included in the repository. Set `ADMIN_PASSWORD` in your
server environment or enter it at the local launcher prompt (at least 15 characters).

## Public directory

- Search mic names, venues, addresses, boroughs and neighborhoods.
- Compact day and borough dropdowns update the map and the list together.
- The directory fills one viewport. Multiple small rows are visible; Previous/Next
  buttons reach further results without page scrolling or a scrolling list. The number
  of rows adapts to the available height. The map shows all matching located mics.
- Mic details use Mic, Notes and Sources tabs. Long notes and source lists have pages.
- Filter by borough, entry fee and signup method. Purchase minimums remain separate.
- Click a map pin for mic details. Nearby venues and sessions are grouped to prevent
  overlapping pins; the popup lists each mic. Zoom in to separate nearby locations.
- The map initially fits the five boroughs; borough labels remain visible at city scale.
- Unmapped mics remain in the list. Cancelled/postponed mics have no active pin.
- Saved mics are stored only in that browser. CSV export is available for real listings.
- Source links, last-check timestamps, conflicts, stale warnings and curated edits are visible.
- A checked source is not treated as a host confirmation.

The background map uses OpenStreetMap tiles, with visible attribution and normal
browser caching. No tiles are bundled or downloaded for offline use. Internet access
is needed to display the map background. Pins and the list still work if tiles fail.

## Administration: /admin

The control room contains **Sources**, **Corrections**, **Claims**, **Host access**,
**All mics**, and **Activity**.

### Sources

Paste a public URL or upload `.xlsx`/CSV. Review the initial extraction before publishing.
Supported input adapters are CSV, XLSX, JSON event arrays, HTML tables, event JSON-LD,
and HTML cards configured with CSS selectors. Table column names can be mapped to
standard fields. A source-wide default can fill genuinely shared fields such as borough.

- URL sources can refresh every 15, 30, 60, 180, 360, 720 or 1440 minutes.
- Uploaded files are snapshots, not live connections to files on someone else's computer.
- For ongoing spreadsheet updates, use a stable, accessible online XLSX or CSV URL.
- For Google Sheets, use a published CSV export URL; this build does not implement private
  Google Drive/OneDrive OAuth connections.
- Sources can be paused, manually checked, reprioritized and removed.
- Updates preserve manually/host-edited fields. Unedited fields continue syncing.
- Failed or partial imports retain the last saved records and surface a review warning.
- Missing rows are marked for review, not automatically declared cancelled or deleted.
- Higher-priority sources supply the displayed value when sources disagree. Conflicts are shown.
- Source checks honor robots rules and enforce request, redirect, size and public-network limits.

**Not a universal scraper:** arbitrary prose, screenshots, Instagram, login-only pages,
CAPTCHAs and JavaScript-only listings are not automatically parsed. They need an authorized
structured feed or another adapter. No paywall or authentication bypass is included.
The application does not infer unknown times, boroughs or complicated recurring schedules.

Only connect sources you may access and reuse. Source URLs are visible to visitors.
Do not place private credentials or sensitive query tokens in source URLs.

### Source fields

Required: `name`, `venue`, `borough`, `start_time`, plus `weekday` or `date`.
Optional: `id`, `address`, `neighborhood`, `signup_time`, `cost`, `purchase_minimum`,
`set_minutes`, `signup_method`, `signup_url`, `notes`, `status`, `excluded_dates`,
`latitude`, `longitude`.

Use full weekday names. Dates must include a year. Times need an explicit format such
as `7:00 PM` or `19:00`; a bare `7` is rejected. Monthly or alternating schedules must
be provided as individual dates. Multiple sessions need distinct IDs.
A stable source ID helps keep records attached when a title changes. A source changing
its ID or weekly recurrence can require administrator reconciliation rather than a guessed merge.

`examples/open-mics-template.csv` is a blank import template. `examples/DEMO-mics.csv`
is deliberately fictional test data, not an actual New York City schedule.

### Fix submissions

Every public listing has **Submit a fix**. No visitor account is required. Names,
optional emails and evidence stay in the private review queue, not in the public API.

Open the correction in `/admin`, edit the mic with the correct information, then mark
it resolved. Rejection and review notes are also available. A free-text correction never
silently changes the listing.

### Claims and host accounts

1. A visitor selects **Claim this mic**, supplies their name, email and evidence that they run it.
2. You verify the claim in `/admin` and approve or reject it.
3. Approval creates a one-use signup link, valid for seven days. **Copy and send it to the
   verified host yourself. The app does not send email.**
4. The host opens the link and creates an account with a password of at least 15 characters.
5. Their account can edit only the mic you approved. Access is checked on every write.
6. They return through `/owner`. An existing host can use another approved invitation
   to attach a second mic to the same account after entering their current password.
7. You can revoke access, hide the mic, edit it yourself, or restore imported values.

The compact host dashboard has **Mic**, **When**, **Where**, and **Hosts** sections,
a last-updated timestamp, and a public-listing link. Hosts can update names, fees,
times, venue and pin details, notes, host names, social URLs, and signup links.
Edits stay in the form when switching sections; **Save changes** publishes them.
Hosts have separate passwords from the site administrator. **Password** requires
their current password, accepts a new password of at least 15 characters, and
signs out all their host sessions.

For supported Badslava and Comediq feeds, an exact provider mic ID carries an active
claim into later dated occurrences. New occurrences inherit persistent host details
without copying one-date cancellations, date overrides, or skipped dates. Dates
remain separately editable. Existing administrator edits are preserved. Ambiguous
provider identities require review instead of automatically extending access.
Revoking any occurrence removes the whole approved claim's recurring access while
leaving separately approved mics alone.

Invitation tokens and sessions are hashed in the database. Claim links put the token in
an URL fragment so it is not included in ordinary HTTP access logs. Links are single-use;
replacing or rejecting an invitation invalidates the old one. Revocation takes effect on
the next edit request, even if the host still has a valid login session.

Automatic email verification, automated invitation emails, and self-service password
recovery are not implemented in this version. Admin verification of the claimant is essential.
An audit trail records edits, approvals, rejection, visibility changes and revocations.

### Map coordinates

Sources can supply latitude/longitude. Otherwise, the worker attempts cached US Census
street-address geocoding. Matches are approximate and labeled; ambiguous/no matches stay
unmapped. The admin/host editor supports manual pin coordinates. Changing an address
clears its old coordinates unless replacement coordinates are explicitly supplied.

The geocoder uses a fixed government endpoint, not arbitrary user-provided URLs. It
sends venue street addresses and boroughs, never claim evidence or contact information.

## Deploy to Vercel

The FastAPI entrypoint is `app/main.py`. `vercel.json` uses a 300-second function
limit. Static assets are served from `/assets`; the app never writes a database to
Vercel's temporary filesystem. Database initialization is deferred until the first
request, so builds do not require a working database connection.

1. Link this repository to a Vercel project.
2. Connect a hosted PostgreSQL database (for example, Neon through Vercel Marketplace).
   Set the pooled connection URL as **DATABASE_URL** in the production environment.
   Use the provider's TLS-enabled URL; never commit it. `POSTGRES_URL` is also supported.
3. Set **ADMIN_PASSWORD** to a unique password of at least 15 characters and
   **LOCAL_DEV=0**. Set **PUBLIC_ORIGIN** to the actual production HTTPS origin.
4. Set **GITHUB_SYNC_ENABLED=1**. Deploy and verify `/api/health` reports
   `storage: postgresql` before connecting real sources.
5. Put the verified production origin in `deployment.json` and commit it. The included
   **Check mic sources** GitHub Actions workflow calls `/api/cron/sync` on a 15-minute
   schedule. Until that URL is configured, the workflow deliberately makes no request.
6. Run the workflow manually and check the source heartbeat in `/admin`.

Scheduled requests use short-lived GitHub OIDC tokens. The server validates the issuer,
audience, immutable repository/owner IDs, main branch, exact workflow, event and expiry.
A fork or another workflow cannot trigger imports. No administrator password or long-lived
site credential is stored in GitHub Actions. The scheduler has no access to host accounts.

GitHub schedules can be delayed and are automatically disabled in public repositories
after 60 days without repository activity; watch the heartbeat and re-enable the workflow
if needed. This schedule avoids requiring sub-daily Vercel Cron on a paid hosting plan.
Alternatively, a scheduler can send `Authorization: Bearer <CRON_SECRET>` to the same
endpoint after setting a unique random **CRON_SECRET** of at least 32 characters.
Do not add that secret to a URL, source file or public log.

A database lease prevents overlapping scheduled batches across instances. Each batch is
bounded to leave time before Vercel's function timeout. Sources not yet due are skipped;
failed checks retain existing listings. Vercel does not start the always-running worker.
The next scheduled batch continues pending sources and address geocoding.

The real deployment, external source fetching and map tiles must still be verified on
hosting. The site starts empty; fictional demo listings are never seeded automatically.

### Persistent Python/Docker hosting

SQLite remains supported for a single always-on process with persistent storage.
Use `Dockerfile`, `compose.yaml` or the optional `render.yaml` blueprint, set a persistent
**DATABASE_PATH**, **SCHEDULER_ENABLED=1**, **LOCAL_DEV=0**, **ADMIN_PASSWORD**, and the
actual **PUBLIC_ORIGIN**. Serve behind HTTPS. Configure trusted proxy IPs only for your
actual reverse proxy. `Start-Mic-List.command` remains available for local use.

## Storage and backups

PostgreSQL (Vercel) or SQLite (local/persistent hosting) keeps sources, observations,
overlays, moderation submissions, accounts and the audit trail. The public directory is not a browser-only localStorage mockup.

`/api/export` exports **source configurations and observations only**; it does not back up
host accounts, claims or edits. Use `scripts/backup.py` for a full consistent SQLite backup:

```bash
python scripts/backup.py data/miclist.sqlite3 /private/backups/miclist.sqlite3
```

For PostgreSQL, use the database provider's backup/restore tools or `pg_dump` for a full backup.

Full backups contain private contact details and password hashes. Keep them confidential.
Do not commit `.env`, `data/`, databases, backups or runtime sessions to a public repository.

## Testing

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
# Browser test: set CHROMIUM_PATH to your installed Chromium/Chrome executable.
PYTHONPATH=. python tests/browser_checks.py
python scripts/build_preview.py
python tests/directory_layout_checks.py
```

The backend suite also covers PostgreSQL persistence, transaction rollback, concurrent
source leases, authenticated scheduled jobs and rejection of unauthorized GitHub identities.
Set **TEST_DATABASE_URL** to a disposable PostgreSQL database to run storage-dependent
tests against both engines. Each test creates and removes its own isolated schema.
The included GitHub CI workflow runs this suite against PostgreSQL 16.
Backend tests cover authentication, CSRF rejection, owner scoping, claim approval,
single-use/expired invitations, account reuse, revoked access, hidden listings,
private submission data, rate limits, snapshots, XLSX/CSV/HTML/JSON-LD parsing,
source changes and failure retention, and server-side request restrictions.

Browser checks cover all seven day filters, pin grouping and popups, nine viewport sizes
(from 320×400 to 1440×900, including landscape), complete result pagination, saved
filters, no directory/list scrolling, and compact details/filter dialogs, plus
admin login/logout, tabs, manual mic creation and upload-form selection. They use an
in-process API transport because localhost navigation is restricted in the build browser.
This is not an assertion that public hosting, live source sites or map tiles were tested.

`preview.html` is a self-contained, read-only interface demonstration. Its fictional
listings are never automatically seeded into the real database.

## Reference documentation

- OpenStreetMap tile policy: https://operations.osmfoundation.org/policies/tiles/
- US Census geocoding API: https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html
- Authentication guidance: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
