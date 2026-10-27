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
python tests/browser_directory_scrolling.py
python tests/browser_text_size.py
python tests/browser_map_gestures.py
node tests/past_mic_logic.cjs
node tests/map_viewport.cjs
```

Further scripts in `tests/` cover the calendar, host dashboard, admin management and
shop pages.

Directory checks cover nine viewport sizes from 320x400 to 1440x900 (including
landscape), continuous mobile scrolling with real Chromium touch input, desktop
pagination, map visibility and area filtering, date/time/search/borough filters,
and compact detail and filter dialogs. Text-size checks cover all four reading
sizes, scaled rows and pagination, keyboard access, and saved preferences across
reloads using a locally served preview. Fixed-clock checks cover New York time,
midnight rollover, daylight saving, recurrence and future-date brightness. CI runs
the directory logic and browser regressions after the backend suite. Further scripts
cover admin login/logout, tabs, manual mic creation and upload; the map gesture test
mocks vibration calls.

They use an in-process API transport and exclude external map tiles, so they do not
verify public hosting, live source sites, live geocoding, or physical haptics.

## Preview build

`python scripts/build_preview.py` produces `preview.html`, a self-contained, read-only
demo of the interface using fictional listings. Preview data is never seeded into a
real database.
