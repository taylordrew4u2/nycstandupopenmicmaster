# Deployment and operations

Two supported hosting models:

| Model | Database | Scheduled source checks |
| --- | --- | --- |
| Vercel (production) | Hosted PostgreSQL | GitHub Actions calling `/api/cron/sync` |
| Always-on Python / Docker | SQLite on a persistent volume | In-process scheduler |

## Vercel

The FastAPI entrypoint is `app/main.py`; `vercel.json` sets a 300-second function
limit. Static assets are served from `/assets`, and the app never writes a database to
Vercel's ephemeral filesystem. Database initialization is deferred until the first
request, so builds do not need a database connection.

1. Link the repository to a Vercel project.
2. Connect a hosted PostgreSQL database (for example, Neon via the Vercel Marketplace)
   and set its pooled, TLS-enabled URL as `DATABASE_URL` (`POSTGRES_URL` also works).
3. Set `ADMIN_PASSWORD` (at least 15 characters), `LOCAL_DEV=0`, and `PUBLIC_ORIGIN`
   to the production HTTPS origin.
4. Set `GITHUB_SYNC_ENABLED=1`, deploy, and confirm `/api/health` reports
   `storage: postgresql` before connecting real sources.
5. Put the production origin in `deployment.json`. The **Check mic sources** workflow
   (`.github/workflows/sync.yml`) then calls `/api/cron/sync` every 15 minutes; until
   the URL is configured it makes no request.
6. Run the workflow manually and check the source heartbeat in `/admin`.

### Scheduled sync authentication

Scheduled requests use short-lived GitHub OIDC tokens. The server validates the
issuer, audience, immutable repository and owner IDs, `main` branch, exact workflow,
event and expiry, so a fork or another workflow cannot trigger imports. No admin
password or long-lived credential is stored in GitHub Actions, and the scheduler has
no access to host accounts.

As an alternative, an external scheduler can send `Authorization: Bearer <CRON_SECRET>`
after you set a random `CRON_SECRET` of at least 32 characters. Never put that secret
in a URL, source file or public log.

A database lease prevents overlapping batches across instances, and each batch is
bounded to finish before the function timeout. Sources not yet due are skipped;
failed checks keep existing listings; the next batch continues pending sources and
geocoding.

GitHub may delay scheduled workflows and disables them in public repositories after
60 days without activity. Watch the heartbeat and re-enable the workflow if needed.

### Custom domain

The public domain is `https://nycopenmicmasterlist.com`. Canonical URLs, About
structured data, the sitemap and the robots sitemap reference use it. Connect it in
Vercel Domains (registrar URL forwarding is not a custom-domain connection), set
`PUBLIC_ORIGIN=https://nycopenmicmasterlist.com`, and redeploy. The server also accepts
the configured Vercel origin during migration; unrelated origins stay blocked.

## Persistent Python / Docker hosting

SQLite is supported for a single always-on process with persistent storage. Use the
`Dockerfile`, `compose.yaml`, or the optional `render.yaml` blueprint, and set:

- `DATABASE_PATH` on a persistent volume
- `SCHEDULER_ENABLED=1`, `LOCAL_DEV=0`
- `ADMIN_PASSWORD` and the real `PUBLIC_ORIGIN`

Serve behind HTTPS, and set `TRUSTED_PROXY_IPS` only to your actual reverse proxy.
The image runs a single uvicorn worker as an unprivileged user.

Note: `compose.yaml` sets `LOCAL_DEV=1` and binds to `127.0.0.1`, so it is intended for
local use over plain HTTP. Change both for a public deployment.

## Environment variables

See [`.env.example`](../.env.example). Key settings:

| Variable | Purpose |
| --- | --- |
| `ADMIN_PASSWORD` | Admin password, at least 15 characters. Never committed. |
| `DATABASE_URL` / `POSTGRES_URL` | PostgreSQL connection (required on Vercel). |
| `DATABASE_PATH` | SQLite file path for local or persistent hosting. |
| `PUBLIC_ORIGIN` | Production HTTPS origin. |
| `LOCAL_DEV` | `1` only for HTTP localhost; `0` in production. |
| `SCHEDULER_ENABLED` | Run the in-process source scheduler. |
| `GEOCODING_ENABLED` | Enable US Census address geocoding. |
| `GITHUB_SYNC_ENABLED` | Accept OIDC-authenticated GitHub Actions sync calls. |
| `CRON_SECRET` | Optional bearer secret for an external scheduler. |
| `FOURTHWALL_STOREFRONT_TOKEN` | Public storefront token for the `/shop` page. |

## Storage and backups

PostgreSQL (Vercel) or SQLite (local/persistent) stores sources, observations,
overlays, moderation submissions, accounts and the audit trail.

`/api/export` exports **source configurations and observations only**; it does not
back up host accounts, claims or edits. For a full, consistent SQLite backup:

```bash
python scripts/backup.py data/miclist.sqlite3 /private/backups/miclist.sqlite3
```

For PostgreSQL, use the provider's backup tools or `pg_dump`.

Full backups contain private contact details and password hashes. Keep them
confidential, and never commit `.env`, `data/`, databases or backups.

## References

- OpenStreetMap tile policy: https://operations.osmfoundation.org/policies/tiles/
- US Census geocoding API: https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html
- OWASP Authentication Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
