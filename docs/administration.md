# Administration guide

Everything an administrator or verified host can do. The control room lives at
`/admin`; approved hosts sign in at `/owner`.

Admin tabs: **Sources**, **New mics**, **Reports**, **Corrections**, **Claims**,
**Host access**, **All mics**, **Activity**, and **About page**. The dashboard also
shows estimated visitor counts (see [directory.md](directory.md#visitor-counts)).

## Sources

Paste a public URL or upload `.xlsx`/CSV, review the extracted listings, then approve
the import.

Supported input adapters:

- CSV, XLSX and JSON event arrays
- HTML tables, event JSON-LD, and HTML cards configured with CSS selectors
- Dedicated readers for Badslava, Comediq, Bushwick Comedy Club, and the official
  weekly schedules of several NYC clubs (`app/club_sources.py`, catalog in
  `app/club_catalog.json`)

Table column names can be mapped to standard fields, and a source-wide default can
fill genuinely shared fields such as borough.

- URL sources refresh every 15, 30, 60, 180, 360, 720 or 1440 minutes.
- Uploaded files are snapshots. For ongoing spreadsheet updates, use a stable public
  XLSX or CSV URL (for Google Sheets, a published CSV export URL). Private Google
  Drive/OneDrive OAuth connections are not implemented.
- Sources can be paused, checked manually, reprioritized and removed.
- Updates preserve manually or host-edited fields; unedited fields keep syncing.
- Failed or partial imports retain the last saved records and surface a review warning.
- Rows missing from a source are flagged for review, never automatically cancelled or deleted.
- When sources disagree, the higher-priority source supplies the displayed value and
  the conflict is shown.
- Source checks honor robots rules and enforce request, redirect, size and
  public-network limits.

**Not a universal scraper.** Arbitrary prose, screenshots, Instagram, login-only pages,
CAPTCHAs and JavaScript-only listings are not parsed; they need an authorized
structured feed or a dedicated adapter. No paywall or authentication bypass is
included. The importer never infers unknown times, boroughs or complicated recurring
schedules.

Only connect sources you may access and reuse. Source URLs are visible to visitors,
so never put credentials or private tokens in them.

### Source-specific behavior

- **Bushwick Comedy Club** reads the public `/open-mics` calendar, accepts only event
  titles that explicitly contain "open mic", and uses actual dated occurrences and
  venue coordinates. It does not assume weekly recurrence or infer fees. Only exact
  address/date/time matches join generic directory entries.
- **Official club schedules** are read conservatively: times always come from the
  fetched page, and an unrecognized layout fails for review rather than replacing a
  schedule with an empty import. Listings found on an official venue site are labeled
  **Venue confirmed**, which is distinct from a host claim.

### Source fields

Required: `name`, `venue`, `borough`, `start_time`, plus `weekday` or `date`.

Optional: `id`, `address`, `neighborhood`, `signup_time`, `cost`, `purchase_minimum`,
`set_minutes`, `signup_method`, `signup_url`, `notes`, `status`, `excluded_dates`,
`latitude`, `longitude`.

- Use full weekday names. Dates must include a year.
- Times need an explicit format such as `7:00 PM` or `19:00`; a bare `7` is rejected.
- Multiple sessions need distinct IDs. A stable source ID keeps records attached when
  a title changes; a changed ID or recurrence may need administrator reconciliation
  rather than a guessed merge.

`examples/open-mics-template.csv` is a blank import template.
`examples/DEMO-mics.csv` is deliberately fictional test data.

## Corrections and reports

Every public listing has **Submit a fix** and **Report inactive**. No visitor account
is required. Names, optional emails and evidence stay in the private review queue and
are never exposed by the public API.

A free-text correction never silently changes a listing: the administrator edits the
mic, then marks the correction resolved (rejection and review notes are available).
Inactive reports never hide anything automatically; only an authenticated
administrator can hide or restore a listing.

## New mic submissions

`/submit` lets hosts propose a mic without creating an account. A private,
unguessable status link shows the administrator's decision. Approval publishes the
listing and, when the submitter asked to manage it, unlocks a seven-day, one-use
password setup link. Listing-only submissions are published unclaimed. Contact
details stay private.

## Claims and host accounts

1. A visitor selects **Claim this mic** and supplies their name, email and evidence
   that they run it.
2. The administrator verifies the claim in `/admin` and approves or rejects it.
3. Approval creates a one-use signup link valid for seven days. The app does not send
   email: the administrator copies the link to the verified host, and the claimant
   can also see the decision on their private status page.
4. The host creates an account with a password of at least 15 characters.
5. The account can edit only the approved mic; access is checked on every write.
6. Hosts return through `/owner`. An existing host can redeem another approved
   invitation to attach a second mic after re-entering their password.
7. The administrator can revoke access, hide the mic, edit it, or restore imported values.

The host dashboard has **Mic**, **When**, **Where** and **Hosts** sections. Hosts can
update names, fees, times, venue and pin details, notes, host names, social URLs and
signup links. Edits persist across sections until **Save changes** publishes them.
Changing a host password requires the current password and signs out all of that
host's sessions.

### Recurring ownership

For Badslava and Comediq feeds, an exact provider mic ID carries an active claim into
later dated occurrences. New occurrences inherit persistent host details without
copying one-date cancellations, overrides or skipped dates. Ambiguous provider
identities require review instead of extending access automatically. Revoking any
occurrence removes the whole claim's recurring access while leaving separately
approved mics alone.

### Security notes

- Invitation tokens and sessions are stored hashed.
- Claim links carry the token in a URL fragment so it never appears in ordinary HTTP
  access logs. Links are single-use; replacing or rejecting an invitation invalidates
  the old one.
- Revocation takes effect on the next write, even if the host still has a valid session.
- An audit trail records edits, approvals, rejections, visibility changes and revocations.
- Not implemented: automatic email verification, invitation emails, and self-service
  password recovery. Administrator verification of claimants is essential.

## Map coordinates

Sources can supply latitude/longitude. Otherwise the worker tries cached US Census
street-address geocoding; matches are approximate and labeled, and ambiguous or
missing matches stay unmapped. Admins and hosts can set pin coordinates manually.
Changing an address clears old coordinates unless replacements are supplied.

The geocoder uses a fixed government endpoint and sends only venue street addresses
and boroughs, never claim evidence or contact information.

## About page

The **About page** tab edits the page copy and the search/social description. Content
is stored in the database and rendered as plain text.
