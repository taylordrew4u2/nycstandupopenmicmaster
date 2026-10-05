# Testing

## Backend (pytest)

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```

Set `TEST_DATABASE_URL` to a disposable PostgreSQL database to run storage-dependent
tests against both SQLite and PostgreSQL; each test creates and drops its own schema.
CI (`.github/workflows/test.yml`) runs the suite against PostgreSQL 16 on every push
and pull request.

Coverage includes authentication, CSRF rejection, owner scoping, claim approval,
single-use and expired invitations, account reuse, revoked access, hidden listings,
private submission data, rate limits, snapshots, XLSX/CSV/HTML/JSON-LD parsing, the
Badslava, Comediq, Bushwick and club-schedule readers, source changes and failure
retention, server-side request restrictions, PostgreSQL persistence, transaction
rollback, concurrent source leases, and rejection of unauthorized GitHub identities.

## Browser checks (Playwright + Chromium)

```bash
python scripts/build_preview.py
export CHROMIUM_PATH=/path/to/chromium
PYTHONPATH=. python tests/browser_checks.py
python tests/directory_layout_checks.py
python tests/browser_map_gestures.py
```

Further scripts in `tests/` cover the calendar, host dashboard, admin management and
shop pages.

Browser checks cover all seven day filters, pin grouping and popups, nine viewport
sizes from 320x400 to 1440x900 (including landscape), complete result pagination,
saved filters, no page or list scrolling, compact detail and filter dialogs, and admin
login/logout, tabs, manual mic creation and upload. The gesture test drives real
Chromium touch input and mocks vibration calls.

They use an in-process API transport and exclude external map tiles, so they do not
verify public hosting, live source sites, live geocoding, or physical haptics.

## Preview build

`python scripts/build_preview.py` produces `preview.html`, a self-contained, read-only
demo of the interface using fictional listings. Preview data is never seeded into a
real database.
