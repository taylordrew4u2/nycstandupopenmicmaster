"""NYC open-mic directory with persistent storage and scheduled source checks."""
import asyncio
import contextlib
import csv
import hashlib
import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .fetcher import SafeFetcher, FetchError, MAX_BYTES, validate_url
from .models import SourceConfig, SourceUpdate, PreviewCommit, Login, AdminPasswordUpdate, VisibilityUpdate
from .admin_auth import AdminAuth
from .parsers import extract, FIELDS
from .store import Store, digest

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger('miclist')


def create_app(db_path=None):
    on_vercel = os.getenv('VERCEL') == '1'
    database = db_path or os.getenv('DATABASE_URL') or os.getenv('POSTGRES_URL') or (
        '' if on_vercel else os.getenv('DATABASE_PATH', str(ROOT / 'data' / 'miclist.sqlite3')))
    if on_vercel and database and not str(database).startswith(('postgres://', 'postgresql://')):
        raise RuntimeError('Vercel requires a PostgreSQL DATABASE_URL; local database files are not durable.')
    store = Store(database)
    fetcher = SafeFetcher()
    local_dev = not on_vercel and os.getenv('LOCAL_DEV', '0') == '1'
    auth = AdminAuth(store, os.getenv('ADMIN_PASSWORD', ''))
    background_worker = not on_vercel and os.getenv('SCHEDULER_ENABLED', '1') == '1'
    scheduler_enabled = background_worker or os.getenv('GITHUB_SYNC_ENABLED') == '1' or len(os.getenv('CRON_SECRET', '')) >= 32

    async def sync_one(source_id):
        source = store.get_source(source_id)
        if not source:
            raise HTTPException(404, 'Source not found')
        if source['config']['kind'] == 'upload':
            raise HTTPException(400, 'This is an uploaded snapshot. Import a replacement file to change it, or use an online file URL for automatic syncing.')
        if not store.acquire(source_id):
            return {'state': 'busy', 'message': 'A check is already running.'}
        source = store.get_source(source_id)
        if source['config']['kind'] == 'upload':
            with store.connect() as c:
                c.execute('UPDATE sources SET lease_until=0 WHERE id=?', (source_id,))
            raise HTTPException(400, 'This source was replaced by an uploaded snapshot and cannot auto-sync.')
        try:
            from .comediq import supports as comediq_source
            rolling_dates = comediq_source(source['config']['url'])
            result = await fetcher.fetch(source['config']['url'], None if rolling_dates else source['etag'], None if rolling_dates else source['modified'])
            validators = {'etag': result['headers'].get('Etag') or result['headers'].get('ETag'),
                          'modified': result['headers'].get('Last-Modified')}
            if result['status'] == 304:
                # A 304 after a partially approved preview must not erase its review state.
                if source['state'] == 'review' or rolling_dates:
                    result = await fetcher.fetch(source['config']['url'])
                else:
                    return store.success(source_id, [], validators, unchanged=True)
            content_hash = digest(result['content'])
            validators['hash'] = content_hash
            if content_hash == source['content_hash'] and source['state'] != 'review' and not rolling_dates:
                return store.success(source_id, [], validators, unchanged=True)
            parsed = await asyncio.to_thread(extract, result['content'], SourceConfig(**source['config']), result['headers'].get('Content-Type', ''))
            if not parsed['rows'] or parsed['skipped']:
                message = f"Import needs review: {len(parsed['rows'])} valid, {parsed['skipped']} skipped. Existing listings kept. " + ' '.join(parsed['warnings'][:5])
                store.failure(source_id, message, review=True)
                return {'state': 'review', 'message': message}
            return store.success(source_id, parsed['rows'], validators)
        except (ValueError, OSError, asyncio.TimeoutError) as exc:
            store.failure(source_id, str(exc))
            return {'state': 'error', 'message': str(exc)}
        except Exception:
            log.exception('Source synchronization failed')
            message = 'Unexpected import error. Existing listings were kept; check the server logs.'
            store.failure(source_id, message)
            return {'state': 'error', 'message': message}

    async def worker():
        while True:
            try:
                from .scheduling import run_due
                await run_due(store, sync_one)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('Worker iteration failed')
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(app):
        if auth.configured and auth.bootstrap['weak_password']:
            log.warning('The administrator password is short. Replace it before public launch.')
        if not auth.configured:
            log.warning('Admin is locked. Set ADMIN_PASSWORD on the server to enable administration.')
        task = asyncio.create_task(worker()) if background_worker else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title='NYC Open Mic Master List', version='1.0.0', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.fetcher = fetcher
    app.state.sync_one = sync_one
    app.state.admin_auth = auth

    @app.middleware('http')
    async def security_headers(request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('x-requested-with') != 'MicList':
                return JSONResponse({'detail': 'Missing same-origin request header.'}, status_code=403)
            origin = request.headers.get('origin')
            allowed = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
            expected = str(request.base_url).rstrip('/')
            trusted_origins = {allowed} if allowed else {expected}
            # Keep the explicitly configured legacy origin working during domain migration.
            # Never trust arbitrary Origin or forwarded-host values.
            if on_vercel or allowed == 'https://nycstandupopenmicmaster.vercel.app':
                trusted_origins.add('https://nycopenmicmasterlist.com')
            if origin and origin.rstrip('/') not in trusted_origins:
                return JSONResponse({'detail': 'Cross-origin writes are not allowed.'}, status_code=403)
            try:
                content_length = int(request.headers.get('content-length', '0') or 0)
            except ValueError:
                return JSONResponse({'detail': 'Invalid request size.'}, status_code=400)
            if content_length > MAX_BYTES + 100000:
                return JSONResponse({'detail': 'Upload is too large (5 MB maximum).'}, status_code=413)
        response = await call_next(request)
        response.headers.update({
            'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'strict-origin-when-cross-origin', 'X-Frame-Options': 'DENY',
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        })
        if request.url.path == '/shop':
            response.headers['Content-Security-Policy'] = response.headers['Content-Security-Policy'].replace("connect-src 'self'", "connect-src 'self' https://storefront-api.fourthwall.com").replace("img-src 'self' data: https://tile.openstreetmap.org", "img-src 'self' data: https://*.fourthwall.com https://imgproxy.fourthwall.dev")
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        if request.url.path in ('/admin', '/owner', '/claim', '/submit', '/claim-status'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    def admin(request: Request):
        return auth.session(request.cookies.get('miclist_session', ''))

    from .analytics import register as register_analytics
    register_analytics(app, store, admin, local_dev)

    def admin_ip(request: Request):
        return hashlib.sha256((request.client.host if request.client else 'unknown').encode()).hexdigest()

    def check_config(config):
        if not config.permission_confirmed:
            raise HTTPException(400, 'Confirm that you may access and reuse this source before importing.')
        if set(config.mapping) - set(FIELDS) or set(config.defaults) - set(FIELDS):
            raise HTTPException(400, 'Unknown field in the column mapping or defaults.')
        if len(config.mapping) > 20 or any(len(v) > 300 for v in config.mapping.values()):
            raise HTTPException(400, 'Mapping is too large.')
        if any(len(v) > 2000 for v in config.defaults.values()):
            raise HTTPException(400, 'A default field value is too long.')

    @app.get('/api/session')
    async def session_info(request: Request):
        try:
            credential = await asyncio.to_thread(admin, request)
            authenticated = True
        except HTTPException:
            authenticated = False
        return {'authenticated': authenticated, 'configured': auth.configured,
                'weak_password': credential['weak_password'] if authenticated else None}

    @app.post('/api/login')
    async def login(body: Login, request: Request, response: Response):
        token = await asyncio.to_thread(auth.login, body.password, admin_ip(request))
        response.set_cookie('miclist_session', token, max_age=43200, httponly=True, secure=not local_dev, samesite='strict', path='/')
        return {'authenticated': True}

    @app.post('/api/admin/password', dependencies=[Depends(admin)])
    async def change_admin_password(body: AdminPasswordUpdate, request: Request, response: Response):
        weak = await asyncio.to_thread(auth.change_password, request.cookies.get('miclist_session', ''),
                                      body.current_password, body.new_password, admin_ip(request))
        response.delete_cookie('miclist_session', path='/')
        return {'authenticated': False, 'signin_required': True, 'weak_password': weak}

    @app.post('/api/logout')
    async def logout(request: Request, response: Response):
        token = request.cookies.get('miclist_session', '')
        with store.connect() as c:
            c.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
        response.delete_cookie('miclist_session', path='/')
        return {'authenticated': False}

    @app.get('/api/health')
    async def health():
        with store.connect() as c:
            heartbeat = c.execute("SELECT value FROM meta WHERE key='worker_heartbeat'").fetchone()
        return {'status': 'ok', 'storage': 'postgresql' if store.postgres else 'sqlite',
                'scheduler_enabled': scheduler_enabled, 'worker_heartbeat': float(heartbeat[0]) if heartbeat else None}

    @app.get('/api/cron/sync')
    async def scheduled_sync(request: Request):
        from .scheduling import authorize, run_due
        await asyncio.to_thread(authorize, request.headers.get('authorization', ''))
        return await run_due(store, sync_one)

    @app.get('/api/sync-status')
    async def sync_status():
        return {'last_sync_at': store.last_live_sync(), 'timezone': 'America/New_York'}

    @app.get('/api/public')
    async def public():
        return store.public_data()

    @app.get('/api/sources', dependencies=[Depends(admin)])
    async def sources():
        with store.connect() as c:
            hb = c.execute("SELECT value FROM meta WHERE key='worker_heartbeat'").fetchone()
        return {'sources': store.list_sources(), 'activity': store.activity(), 'review': store.review_rows(),
                'club_candidates': json.loads((Path(__file__).parent / 'club_catalog.json').read_text()),
                'scheduler_enabled': scheduler_enabled, 'heartbeat_max_age': 240 if background_worker else 2700,
                'heartbeat': float(hb[0]) if hb else None}

    @app.post('/api/sources/preview', dependencies=[Depends(admin)])
    async def preview(config: SourceConfig):
        check_config(config)
        if config.kind == 'upload':
            raise HTTPException(400, 'Use the upload form for local files.')
        try:
            config.url = validate_url(config.url)
            fetched = await fetcher.fetch(config.url)
            parsed = await asyncio.to_thread(extract, fetched['content'], config, fetched['headers'].get('Content-Type', ''))
            validators = {'etag': fetched['headers'].get('Etag') or fetched['headers'].get('ETag'),
                          'modified': fetched['headers'].get('Last-Modified'), 'hash': digest(fetched['content'])}
            # Store the original URL: it is revalidated and redirects are checked on every request.
            preview_id = store.preview(config.model_dump(), parsed, validators=validators)
            return {**parsed, 'preview_id': preview_id}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/api/sources/upload-preview', dependencies=[Depends(admin)])
    async def upload_preview(file: UploadFile = File(...), config: str = Form(...)):
        try:
            source = SourceConfig.model_validate_json(config)
            source.kind, source.url = 'upload', ''
            check_config(source)
            name = file.filename or ''
            if not name.lower().endswith(('.csv', '.xlsx')):
                raise ValueError('Upload a .csv or .xlsx file. Legacy .xls and macro files are not accepted.')
            content = await file.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                raise HTTPException(413, 'File exceeds the 5 MB limit.')
            parsed = await asyncio.to_thread(extract, content, source, file.content_type or '', name)
            preview_id = store.preview(source.model_dump(), parsed, content)
            return {**parsed, 'preview_id': preview_id}
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            await file.close()

    @app.post('/api/sources', dependencies=[Depends(admin)])
    async def commit(body: PreviewCommit):
        try:
            return {'id': store.commit_preview(body.preview_id, body.replacement_source_id)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.patch('/api/sources/{source_id}', dependencies=[Depends(admin)])
    async def update_source(source_id: str, body: SourceUpdate):
        source = store.get_source(source_id)
        if not source:
            raise HTTPException(404, 'Source not found.')
        changes = body.model_dump(exclude_none=True)
        if 'enabled' in changes and source['config']['kind'] == 'upload':
            raise HTTPException(400, 'Uploaded files are snapshots and cannot automatically refresh.')
        for field in ('interval_minutes', 'priority'):
            if field in changes:
                source['config'][field] = changes[field]
        enabled = changes.get('enabled', bool(source['enabled']))
        with store.connect() as c:
            c.execute('UPDATE sources SET enabled=?,config=?,next_check=? WHERE id=?',
                      (int(enabled), json.dumps(source['config']), time.time() + source['config']['interval_minutes']*60, source_id))
        return {'ok': True}

    @app.post('/api/sources/{source_id}/sync', dependencies=[Depends(admin)])
    async def sync(source_id: str):
        return await sync_one(source_id)

    @app.delete('/api/sources/{source_id}', dependencies=[Depends(admin)])
    async def delete_source(source_id: str):
        source = store.get_source(source_id)
        if source and source['lease_until'] > time.time():
            raise HTTPException(409, 'A source check is running. Remove it after that check finishes.')
        with store.connect() as c:
            c.execute('DELETE FROM sources WHERE id=?', (source_id,))
        return {'ok': True}

    @app.patch('/api/listings/{source_id}/{remote_key}/visibility', dependencies=[Depends(admin)])
    async def visibility(source_id: str, remote_key: str, body: VisibilityUpdate):
        with store.connect() as c:
            result = c.execute('UPDATE observations SET hidden=? WHERE source_id=? AND remote_key=?',
                               (int(body.hidden), source_id, remote_key))
            if not result.rowcount:
                raise HTTPException(404, 'Listing not found.')
        return {'ok': True}

    @app.get('/api/export', dependencies=[Depends(admin)])
    async def export():
        # Exports configurations and observations, never session tokens or passwords.
        with store.connect() as c:
            obs = [dict(r) for r in c.execute('SELECT * FROM observations')]
        data = {'schema': 1, 'exported_at': time.time(), 'sources': store.list_sources(), 'observations': obs}
        return Response(json.dumps(data, indent=2), media_type='application/json',
                        headers={'Content-Disposition': 'attachment; filename="mic-list-backup.json"'})

    @app.get('/api/export.csv')
    async def export_csv():
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        fields = ['name', 'venue', 'address', 'borough', 'weekday', 'date', 'start_time', 'signup_time', 'cost_text', 'purchase_minimum', 'set_minutes', 'signup_method', 'signup_url', 'status']
        writer.writerow(fields + ['sources', 'last_checked_utc'])
        for row in store.public_data()['listings']:
            values = [row.get(f, '') for f in fields] + [' | '.join(s['url'] for s in row['sources']), row['checked_at']]
            # Prevent formula execution when an export is opened in a spreadsheet.
            writer.writerow(["'"+str(v) if str(v).lstrip().startswith(('=', '+', '-', '@')) else v for v in values])
        return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="nyc-open-mics.csv"'})

    from .community import register
    register(app, store, admin, local_dev)

    @app.get('/api/admin/about', dependencies=[Depends(admin)])
    async def about_settings():
        from .about import read_content
        return read_content(store).model_dump()

    from .about import AboutContent

    @app.put('/api/admin/about', dependencies=[Depends(admin)])
    async def update_about(body: AboutContent):
        from .about import save_content
        save_content(store, body)
        return {'ok': True}

    @app.get('/api/shop/config')
    async def shop_config():
        token = os.getenv('FOURTHWALL_STOREFRONT_TOKEN', '')
        if not token.startswith('ptkn_'):
            raise HTTPException(503, 'Shop connection is not configured.')
        # Storefront tokens are public by design; Open API credentials never belong here.
        return {'storefrontToken': token}

    @app.get('/shop')
    async def shop():
        return FileResponse(ROOT / 'assets' / 'shop.html')

    @app.get('/about')
    async def about():
        from .about import render
        from fastapi.responses import HTMLResponse
        return HTMLResponse(render(store), headers={'Cache-Control': 'no-store'})

    @app.get('/robots.txt')
    async def robots():
        return Response('User-agent: *\nAllow: /\nDisallow: /api/\nAllow: /api/public$\nSitemap: https://nycopenmicmasterlist.com/sitemap.xml\n', media_type='text/plain')

    from .seo import register as register_seo
    register_seo(app, store)

    @app.get('/admin')
    @app.get('/owner')
    @app.get('/claim-status')
    @app.get('/claim')
    @app.get('/submit')
    @app.get('/')
    async def index(request: Request):
        headers = {'X-Robots-Tag': 'noindex, nofollow'} if request.url.path != '/' else {}
        return FileResponse(ROOT / 'assets' / 'index.html', headers=headers)

    app.mount('/assets', StaticFiles(directory=ROOT / 'assets'), name='assets')
    return app

app = create_app()
