"""Moderated corrections, administrator-approved claims, scoped owner accounts.

Invitations are deliberately delivered by the administrator. No email delivery is
implied. Passwords and bearer tokens are hashed; membership is checked per write.
"""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from datetime import datetime
from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ConfigDict
from .models import SourceConfig
from .parsers import normalize_rows, DAYS, FIELDS, key

SCHEMA = '''
CREATE TABLE IF NOT EXISTS submissions (
 id TEXT PRIMARY KEY, mic_id TEXT NOT NULL, mic_name TEXT NOT NULL,
 kind TEXT NOT NULL, name TEXT NOT NULL, email TEXT NOT NULL, message TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL, reviewed REAL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mic_proposals (
 id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 email TEXT NOT NULL, message TEXT NOT NULL, payload TEXT NOT NULL,
 fingerprint TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 created REAL NOT NULL, reviewed REAL, reply TEXT NOT NULL DEFAULT '',
 mic_id TEXT, claim_id TEXT
);
CREATE TABLE IF NOT EXISTS owners (
 id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 password_hash TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ownership (
 mic_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES owners(id), approved REAL NOT NULL,
 claim_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invitations (
 token_hash TEXT PRIMARY KEY, submission_id TEXT NOT NULL, expires REAL NOT NULL, used REAL
);
CREATE TABLE IF NOT EXISTS owner_sessions (
 token_hash TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES owners(id), expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS overlays (
 mic_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL, actor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mic_flags (mic_id TEXT PRIMARY KEY, hidden INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS mic_registry (
 source_id TEXT NOT NULL, remote_key TEXT NOT NULL, mic_id TEXT NOT NULL,
 PRIMARY KEY(source_id,remote_key)
);
CREATE TABLE IF NOT EXISTS audit (
 id TEXT PRIMARY KEY, mic_id TEXT, actor TEXT NOT NULL, action TEXT NOT NULL,
 created REAL NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS abuse (bucket TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_abuse ON abuse(bucket,created);
CREATE TABLE IF NOT EXISTS locations (
 address_key TEXT PRIMARY KEY, lat REAL, lng REAL, state TEXT NOT NULL,
 checked REAL NOT NULL, label TEXT NOT NULL DEFAULT ''
);
'''

class Submission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(pattern=r'^(fix|claim|report)$')
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(default='', max_length=254)
    message: str = Field(min_length=10, max_length=3000)
    website: str = Field(default='', max_length=300)  # Honeypot, not a genuine data field.

class MicProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    message: str = Field(min_length=10, max_length=3000)
    fields: dict
    website: str = Field(default='', max_length=300)

class ProposalDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: str = Field(pattern=r'^(approve|reject)$')
    reply: str = Field(default='', max_length=1000)

class Decision(BaseModel):
    action: str = Field(pattern=r'^(approve|reject|resolve|hide)$')
    note: str = Field(default='', max_length=1000)

class Redeem(BaseModel):
    token: str = Field(min_length=30, max_length=150)
    password: str = Field(min_length=1, max_length=128)

class InvitationLookup(BaseModel):
    token: str = Field(min_length=30, max_length=150)

class OwnerLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

class OwnerPasswordUpdate(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=15, max_length=128)

class Edit(BaseModel):
    fields: dict = Field(default_factory=dict)

class Hide(BaseModel):
    hidden: bool


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(24)
    result = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
    return f'pbkdf2-sha256$600000${salt}${result}'


def verify_password(password, encoded):
    try:
        _, _, salt, _ = encoded.split('$')
        return hmac.compare_digest(hash_password(password, salt), encoded)
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def email_value(value):
    value = value.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise HTTPException(400, 'Enter a valid email address.')
    return value


def register(app, store, admin, local_dev):
    dummy_hash = hash_password(secrets.token_urlsafe(24))

    def throttle(request, prefix, limit, seconds=900):
        # Only trust the ASGI client IP. Configure a trusted reverse proxy at deployment.
        ip = request.client.host if request.client else 'unknown'
        bucket = token_hash(prefix + '|' + ip)
        now = time.time()
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM abuse WHERE created<?', (now-86400,))
            count = c.execute('SELECT COUNT(*) FROM abuse WHERE bucket=? AND created>?', (bucket,now-seconds)).fetchone()[0]
            if count >= limit:
                raise HTTPException(429, 'Too many requests. Try again later.')
            c.execute('INSERT INTO abuse VALUES (?,?)', (bucket,now))

    def record(c, mic_id, actor, action, detail=''):
        c.execute('INSERT INTO audit VALUES (?,?,?,?,?,?)', (uuid.uuid4().hex, mic_id, actor, action, time.time(), detail[:8000]))

    def listing(mic_id, include_hidden=False):
        result = next((r for r in store.public_data(include_hidden=include_hidden)['listings'] if r['id']==mic_id), None)
        if not result:
            raise HTTPException(404, 'Mic not found or no longer published.')
        return result

    def current_owner(request: Request):
        token = request.cookies.get('miclist_owner', '')
        with store.connect() as c:
            row = c.execute('''SELECT o.id,o.email,o.name FROM owners o JOIN owner_sessions s ON s.owner_id=o.id
                               WHERE s.token_hash=? AND s.expires>? AND EXISTS (SELECT 1 FROM ownership m WHERE m.owner_id=o.id)''', (token_hash(token), time.time())).fetchone()
        if not token or not row:
            raise HTTPException(401, 'Sign in to your approved host account.')
        return dict(row)

    def owner_cookie(c, owner_id, response):
        token = secrets.token_urlsafe(40)
        c.execute('DELETE FROM owner_sessions WHERE expires<?', (time.time(),))
        c.execute('INSERT INTO owner_sessions VALUES (?,?,?)', (token_hash(token),owner_id,time.time()+43200))
        response.set_cookie('miclist_owner', token, max_age=43200, httponly=True, secure=not local_dev, samesite='strict', path='/')

    def normalize_edit(existing, fields):
        allowed = set(FIELDS)-{'external_id','display_name'}
        if set(fields)-allowed:
            raise HTTPException(400, 'Some fields cannot be edited.')
        if any(isinstance(v, (dict, list)) or len(str(v))>3000 for v in fields.values()):
            raise HTTPException(400, 'Invalid field value.')
        raw = {k: existing.get(k, '') for k in FIELDS}
        raw['weekday'] = DAYS[existing['weekday']] if existing.get('weekday') is not None else ''
        raw['cost'] = existing.get('cost_text') or existing.get('cost') or ''
        raw['excluded_dates'] = ','.join(existing.get('excluded_dates') or [])
        raw.update(fields)
        if isinstance(raw.get('weekday'), int) or str(raw.get('weekday','')).isdigit():
            raw['weekday'] = int(raw['weekday'])
            if not 0 <= raw['weekday'] < 7:
                raise HTTPException(400,'Invalid weekday.')
            raw['weekday'] = DAYS[raw['weekday']]
        rows, warnings, skipped = normalize_rows([raw], SourceConfig(name='Editor', permission_confirmed=True))
        if skipped or len(rows)!=1:
            raise HTTPException(400, ' '.join(warnings) or 'Edit one mic session at a time.')
        result = rows[0]
        result['remote_key'] = existing.get('remote_key', result['remote_key'])
        return result

    def apply_edit(mic_id, fields, actor):
        before = listing(mic_id, include_hidden=True)
        after = normalize_edit(before, fields)
        # Store only intentional overrides. Scraping still updates fields not overridden.
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if actor != 'admin':
                member = c.execute('SELECT 1 FROM ownership WHERE mic_id=? AND owner_id=?',(mic_id,actor)).fetchone()
                if not member:
                    raise HTTPException(403, 'You do not have permission to edit this mic.')
            old = c.execute('SELECT payload FROM overlays WHERE mic_id=?',(mic_id,)).fetchone()
            patch = json.loads(old[0]) if old else {}
            expanded = set(fields)
            if 'cost' in expanded:
                expanded.add('cost_text')
            if 'date' in expanded or 'weekday' in expanded:
                expanded.update(('date','weekday'))
            if ('address' in fields and fields['address'] != before.get('address')) or ('borough' in fields and fields['borough'] != before.get('borough')):
                # Never leave the old pin attached to a new address.
                for field in ('latitude','longitude'):
                    patch[field] = after.get(field) if field in fields else None
            for field in expanded:
                patch[field] = after.get(field)
            c.execute('INSERT INTO overlays VALUES (?,?,?,?) ON CONFLICT(mic_id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated,actor=excluded.actor',
                      (mic_id,json.dumps(patch),time.time(),actor))
            record(c,mic_id,actor,'edit',json.dumps({'before':{k:before.get(k) for k in patch},'after':patch}))
        return {'ok': True}

    def proposal_fingerprint(payload):
        values = [key(payload.get(k, '')) for k in ('name', 'venue', 'borough')]
        values += [str(payload.get('weekday')), payload.get('start_time', '')]
        return token_hash(json.dumps(values))

    @app.post('/api/mic-proposals', status_code=201)
    async def propose_mic(body: MicProposal, request: Request):
        throttle(request, 'mic-proposals', 5, 3600)
        if body.website:
            raise HTTPException(400, 'Unable to submit this form.')
        email = email_value(body.email)
        if len(body.name.strip()) < 2 or len(body.message.strip()) < 10:
            raise HTTPException(400, 'Include your name and how we can verify that you run this mic.')
        allowed = {'name', 'venue', 'address', 'borough', 'weekday', 'date', 'start_time', 'cost'}
        if set(body.fields) - allowed:
            raise HTTPException(400, 'Unsupported mic submission fields.')
        if not str(body.fields.get('address', '')).strip():
            raise HTTPException(400, 'A street address is required.')
        payload = normalize_edit({}, body.fields)
        fingerprint = proposal_fingerprint(payload)
        raw = secrets.token_urlsafe(40)
        proposal_id = uuid.uuid4().hex
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM mic_proposals WHERE email=? AND fingerprint=? AND state='pending'", (email, fingerprint)).fetchone():
                raise HTTPException(409, 'This mic is already awaiting review. Use the private status link from your first submission.')
            c.execute('INSERT INTO mic_proposals(id,token_hash,name,email,message,payload,fingerprint,created) VALUES (?,?,?,?,?,?,?,?)',
                      (proposal_id, token_hash(raw), body.name.strip(), email, body.message.strip(), json.dumps(payload), fingerprint, time.time()))
        return {'ok': True, 'status_path': '/submit#' + raw,
                'message': 'Submitted for review. Save your private status link; it shows the decision and unlocks host setup if approved. No email has been sent.'}

    @app.post('/api/mic-proposals/status')
    async def proposal_status(body: InvitationLookup, request: Request):
        throttle(request, 'proposal-status', 150)
        with store.connect() as c:
            row = c.execute('SELECT * FROM mic_proposals WHERE token_hash=?', (token_hash(body.token),)).fetchone()
            if not row:
                raise HTTPException(404, 'This private status link is invalid.')
            state = row['state']
            setup = False
            expires = None
            if row['claim_id']:
                claim = c.execute('SELECT state FROM submissions WHERE id=?', (row['claim_id'],)).fetchone()
                if not claim or claim['state'] in ('rejected', 'revoked'):
                    state = 'revoked'
                elif claim['state'] == 'activated':
                    state = 'activated'
                elif claim['state'] == 'approved':
                    invite = c.execute('SELECT expires FROM invitations WHERE token_hash=? AND submission_id=? AND used IS NULL AND expires>?',
                                       (row['token_hash'], row['claim_id'], time.time())).fetchone()
                    setup = bool(invite)
                    expires = invite['expires'] if invite else None
            return {'state': state, 'mic_name': json.loads(row['payload'])['name'], 'reply': row['reply'],
                    'created': row['created'], 'reviewed': row['reviewed'], 'mic_id': row['mic_id'],
                    'can_setup': setup, 'expires_at': expires}

    @app.post('/api/admin/mic-proposals/{proposal_id}', dependencies=[Depends(admin)])
    async def review_proposal(proposal_id: str, body: ProposalDecision):
        now = time.time()
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM mic_proposals WHERE id=?', (proposal_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Submission not found.')
            if row['state'] != 'pending':
                raise HTTPException(409, 'This submission has already been reviewed.')
            if body.action == 'reject':
                c.execute("UPDATE mic_proposals SET state='rejected',reviewed=?,reply=? WHERE id=?", (now, body.reply, proposal_id))
                record(c, None, 'admin', 'mic-submission-rejected', proposal_id)
                return {'ok': True, 'message': 'The decision is now visible on the submitter’s private status page. No email was sent.'}
            payload = json.loads(row['payload'])
            for observation in c.execute('SELECT payload FROM observations WHERE hidden=0 AND missing_count=0'):
                if proposal_fingerprint(json.loads(observation['payload'])) == row['fingerprint']:
                    raise HTTPException(409, 'This mic appears to be listed already. Reject this submission and ask the host to claim the existing listing instead.')
            mic_id, remote_key, claim_id = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
            payload['external_id'] = remote_key
            payload['remote_key'] = remote_key
            config = SourceConfig(name='Manually added', kind='upload', permission_confirmed=True, priority=100).model_dump()
            c.execute("INSERT INTO sources(id,config,enabled,state,created,next_check,last_success) VALUES ('manual',?,0,'snapshot',?,?,?) ON CONFLICT(id) DO NOTHING", (json.dumps(config), now, now, now))
            store._apply(c, 'manual', [payload], now, mark_missing=False)
            c.execute('INSERT INTO mic_registry VALUES (?,?,?)', ('manual', remote_key, mic_id))
            c.execute("INSERT INTO submissions(id,mic_id,mic_name,kind,name,email,message,state,created,reviewed) VALUES (?,?,?,'claim',?,?,?,'approved',?,?)",
                      (claim_id, mic_id, payload['name'], row['name'], row['email'], row['message'], row['created'], now))
            # The private status capability becomes usable for approved setup.
            # Only its hash is stored. A second one-use link can be shared by admin.
            raw = secrets.token_urlsafe(40)
            for hashed in (row['token_hash'], token_hash(raw)):
                c.execute('INSERT INTO invitations VALUES (?,?,?,NULL)', (hashed, claim_id, now + 7*86400))
            c.execute("UPDATE mic_proposals SET state='approved',reviewed=?,reply=?,mic_id=?,claim_id=? WHERE id=?",
                      (now, body.reply, mic_id, claim_id, proposal_id))
            record(c, mic_id, 'admin', 'mic-submission-approved', proposal_id)
            return {'ok': True, 'mic_id': mic_id, 'email': row['email'], 'invite_path': '/claim#' + raw,
                    'message': 'Published. The submitter’s private status page now offers host setup. No email was sent.'}

    @app.post('/api/mics/{mic_id}/submissions', status_code=201)
    async def submit(mic_id: str, body: Submission, request: Request):
        throttle(request,'submissions',6,3600)
        mic = listing(mic_id)
        if body.website:
            return {'ok':True,'message':'Submission received.'}
        email = email_value(body.email) if body.email else ''
        if body.kind=='claim' and not email:
            raise HTTPException(400,'An email is required to claim a mic.')
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if body.kind=='claim':
                if c.execute('SELECT 1 FROM ownership WHERE mic_id=?',(mic_id,)).fetchone():
                    raise HTTPException(409,'This mic has an approved owner. Submit a fix to contact the administrator.')
                if c.execute("SELECT 1 FROM submissions WHERE mic_id=? AND email=? AND kind='claim' AND state IN ('pending','approved')",(mic_id,email)).fetchone():
                    raise HTTPException(409,'A claim for this mic and email is already being reviewed.')
            c.execute('INSERT INTO submissions(id,mic_id,mic_name,kind,name,email,message,created) VALUES (?,?,?,?,?,?,?,?)',
                      (uuid.uuid4().hex,mic_id,mic['name'],body.kind,body.name.strip(),email,body.message.strip(),time.time()))
        return {'ok':True,'message':'Submitted for administrator review. Nothing changes until approved.'}

    @app.get('/api/admin/community', dependencies=[Depends(admin)])
    async def queue():
        with store.connect() as c:
            submissions=[dict(r) for r in c.execute('SELECT * FROM submissions ORDER BY created DESC LIMIT 1000')]
            proposals=[{**{k:r[k] for k in ('id','name','email','message','state','created','reviewed','reply','mic_id','claim_id')},'listing':json.loads(r['payload'])} for r in c.execute('SELECT * FROM mic_proposals ORDER BY created DESC LIMIT 1000')]
            ownership=[dict(r) for r in c.execute('SELECT m.*,o.email,o.name FROM ownership m JOIN owners o ON o.id=m.owner_id')]
            audit=[dict(r) for r in c.execute('SELECT * FROM audit ORDER BY created DESC LIMIT 150')]
        return {'proposals':proposals,'submissions':submissions,'ownership':ownership,'audit':audit,'listings':store.public_data(include_hidden=True)['listings']}

    @app.post('/api/admin/submissions/{submission_id}', dependencies=[Depends(admin)])
    async def decide(submission_id: str, body: Decision):
        now=time.time()
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM submissions WHERE id=?',(submission_id,)).fetchone()
            if not row:
                raise HTTPException(404,'Submission not found.')
            if row['state'] not in ('pending','approved'):
                raise HTTPException(409,'This submission has already been closed.')
            if row['kind']=='claim' and body.action=='approve':
                if c.execute('SELECT 1 FROM ownership WHERE mic_id=?',(row['mic_id'],)).fetchone():
                    raise HTTPException(409,'This mic already has an approved owner; revoke access first.')
                competing=c.execute("SELECT 1 FROM submissions WHERE mic_id=? AND id!=? AND kind='claim' AND state='approved'",(row['mic_id'],submission_id)).fetchone()
                if competing:
                    raise HTTPException(409,'Another invitation for this mic is active. Reject it before approving this claim.')
                raw=secrets.token_urlsafe(40)
                c.execute('DELETE FROM invitations WHERE submission_id=?',(submission_id,))
                c.execute('INSERT INTO invitations VALUES (?,?,?,NULL)',(token_hash(raw),submission_id,now+7*86400))
                c.execute("UPDATE submissions SET state='approved',reviewed=?,note=? WHERE id=?",(now,body.note,submission_id))
                record(c,row['mic_id'],'admin','claim-approved',submission_id)
                return {'ok':True,'invite_path':'/claim#'+raw,'email':row['email'],'expires_at':now+7*86400,
                        'message':'Copy and send this one-use signup link to the verified host. It has not been emailed.'}
            if body.action == 'hide':
                if row['kind'] != 'report':
                    raise HTTPException(400, 'Only an inactive-mic report can be resolved by hiding the mic.')
                c.execute('INSERT INTO mic_flags VALUES (?,1) ON CONFLICT(mic_id) DO UPDATE SET hidden=1', (row['mic_id'],))
                record(c, row['mic_id'], 'admin', 'hidden-after-report', submission_id)
            if row['kind'] == 'report' and body.action == 'approve':
                raise HTTPException(400, 'Hide the inactive mic or dismiss its report.')
            if row['kind']=='claim' and body.action=='resolve':
                raise HTTPException(400,'Approve or reject a claim.')
            if row['kind']=='fix' and body.action=='approve':
                raise HTTPException(400,'Edit the listing first, then mark the fix resolved.')
            state='rejected' if body.action=='reject' else 'resolved'
            c.execute('UPDATE submissions SET state=?,reviewed=?,note=? WHERE id=?',(state,now,body.note,submission_id))
            c.execute('DELETE FROM invitations WHERE submission_id=?',(submission_id,))
            record(c,row['mic_id'],'admin',state+'-'+row['kind'],submission_id)
        return {'ok':True}

    @app.post('/api/owner/invitation')
    async def invitation_info(body: InvitationLookup, request: Request):
        throttle(request,'invite',20)
        with store.connect() as c:
            r=c.execute("SELECT s.email,s.mic_name,s.state FROM invitations i JOIN submissions s ON s.id=i.submission_id WHERE i.token_hash=? AND i.used IS NULL AND i.expires>? AND s.state='approved'",(token_hash(body.token),time.time())).fetchone()
            if not r:
                raise HTTPException(400,'This invitation is invalid, used, or expired. Ask the administrator for a new one.')
            exists=bool(c.execute('SELECT 1 FROM owners WHERE email=?',(r['email'],)).fetchone())
            return {'email':r['email'],'mic_name':r['mic_name'],'existing_account':exists}

    @app.post('/api/owner/redeem')
    async def redeem(body: Redeem, request: Request, response: Response):
        throttle(request,'redeem',8)
        # Expensive password work is done outside the write transaction.
        with store.connect() as c:
            invitation=c.execute("SELECT s.* FROM invitations i JOIN submissions s ON s.id=i.submission_id WHERE i.token_hash=? AND i.used IS NULL AND i.expires>? AND s.state='approved'",(token_hash(body.token),time.time())).fetchone()
            if not invitation:
                raise HTTPException(400,'This invitation is invalid, used, or expired.')
            existing=c.execute('SELECT * FROM owners WHERE email=?',(invitation['email'],)).fetchone()
        if existing:
            if not await asyncio.to_thread(verify_password,body.password,existing['password_hash']):
                raise HTTPException(401,'Use the existing password for this approved host account.')
            encoded=None
        else:
            if len(body.password)<15:
                raise HTTPException(400,'Choose a password of at least 15 characters.')
            encoded=await asyncio.to_thread(hash_password,body.password)
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute("SELECT i.* FROM invitations i JOIN submissions s ON s.id=i.submission_id WHERE i.token_hash=? AND i.used IS NULL AND i.expires>? AND s.state='approved'",(token_hash(body.token),time.time())).fetchone()
            if not current:
                raise HTTPException(409,'Invitation already used or withdrawn.')
            if c.execute('SELECT 1 FROM ownership WHERE mic_id=?',(invitation['mic_id'],)).fetchone():
                raise HTTPException(409,'This mic already has an owner.')
            owner_id=existing['id'] if existing else uuid.uuid4().hex
            if existing:
                # Verification ran outside the transaction. A concurrent password
                # change must not let the old password activate another claim.
                current_owner_row=c.execute('SELECT password_hash FROM owners WHERE id=?',(owner_id,)).fetchone()
                if not current_owner_row or current_owner_row['password_hash']!=existing['password_hash']:
                    raise HTTPException(401,'Your host credentials changed. Sign in again.')
            else:
                if c.execute('SELECT 1 FROM owners WHERE email=?',(invitation['email'],)).fetchone():
                    raise HTTPException(409,'Account created in another session. Try again with its password.')
                c.execute('INSERT INTO owners VALUES (?,?,?,?,?)',(owner_id,invitation['email'],invitation['name'],encoded,time.time()))
            c.execute('INSERT INTO ownership VALUES (?,?,?,?)',(invitation['mic_id'],owner_id,time.time(),invitation['id']))
            c.execute('UPDATE invitations SET used=? WHERE token_hash=?',(time.time(),token_hash(body.token)))
            c.execute("UPDATE submissions SET state='activated' WHERE id=?",(invitation['id'],))
            record(c,invitation['mic_id'],owner_id,'account-activated')
            owner_cookie(c,owner_id,response)
        return {'ok':True}

    @app.post('/api/owner/login')
    async def login(body: OwnerLogin, request: Request, response: Response):
        throttle(request,'owner-login',8)
        email=body.email.strip().lower()
        with store.connect() as c:
            owner=c.execute('SELECT o.* FROM owners o WHERE o.email=? AND EXISTS (SELECT 1 FROM ownership m WHERE m.owner_id=o.id)',(email,)).fetchone()
        valid=await asyncio.to_thread(verify_password,body.password,owner['password_hash'] if owner else dummy_hash)
        if not owner or not valid:
            raise HTTPException(401,'Email or password is incorrect.')
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT password_hash FROM owners WHERE id=? AND EXISTS (SELECT 1 FROM ownership WHERE owner_id=?)',(owner['id'],owner['id'])).fetchone()
            if not current or current['password_hash']!=owner['password_hash']:
                raise HTTPException(401,'Your host credentials changed. Sign in again.')
            owner_cookie(c,owner['id'],response)
        return {'ok':True}

    @app.post('/api/owner/password')
    async def change_password(body: OwnerPasswordUpdate, request: Request, response: Response, owner=Depends(current_owner)):
        # Password confirmation and login share one attempt budget.
        throttle(request,'owner-login',8)
        with store.connect() as c:
            previous=c.execute('SELECT password_hash FROM owners WHERE id=?',(owner['id'],)).fetchone()
        if not previous or not await asyncio.to_thread(verify_password,body.current_password,previous['password_hash']):
            raise HTTPException(401,'Your current password is incorrect.')
        replacement=await asyncio.to_thread(hash_password,body.new_password)
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('''SELECT o.password_hash FROM owners o JOIN owner_sessions s ON s.owner_id=o.id
                                 WHERE o.id=? AND s.token_hash=? AND s.expires>?
                                 AND EXISTS (SELECT 1 FROM ownership m WHERE m.owner_id=o.id)''',
                              (owner['id'],token_hash(request.cookies.get('miclist_owner','')),time.time())).fetchone()
            # The stored hash is the credential generation. Recheck it and the
            # session atomically so only one concurrent password change wins.
            if not current or current['password_hash']!=previous['password_hash']:
                raise HTTPException(401,'Your host credentials changed. Sign in again.')
            c.execute('UPDATE owners SET password_hash=? WHERE id=?',(replacement,owner['id']))
            c.execute('DELETE FROM owner_sessions WHERE owner_id=?',(owner['id'],))
            record(c,None,owner['id'],'password-changed')
        response.delete_cookie('miclist_owner',path='/')
        return {'ok':True,'signin_required':True}

    @app.post('/api/owner/logout')
    async def logout(request: Request,response: Response):
        with store.connect() as c:
            c.execute('DELETE FROM owner_sessions WHERE token_hash=?',(token_hash(request.cookies.get('miclist_owner','')),))
        response.delete_cookie('miclist_owner',path='/')
        return {'ok':True}

    @app.get('/api/owner/me')
    async def me(owner=Depends(current_owner)):
        listings=store.public_data(include_hidden=True)['listings']
        with store.connect() as c:
            ids={r[0] for r in c.execute('SELECT mic_id FROM ownership WHERE owner_id=?',(owner['id'],))}
        return {**owner,'listings':[r for r in listings if r['id'] in ids]}

    @app.patch('/api/owner/mics/{mic_id}')
    async def edit_owned(mic_id: str, body: Edit, owner=Depends(current_owner)):
        return apply_edit(mic_id,body.fields,owner['id'])

    @app.patch('/api/admin/mics/{mic_id}', dependencies=[Depends(admin)])
    async def edit_admin(mic_id: str, body: Edit):
        return apply_edit(mic_id,body.fields,'admin')

    @app.post('/api/admin/mics', dependencies=[Depends(admin)], status_code=201)
    async def create_mic(body: Edit):
        payload=normalize_edit({},body.fields)
        payload['external_id']=uuid.uuid4().hex
        payload['remote_key']=uuid.uuid4().hex
        now=time.time()
        config=SourceConfig(name='Manually added',kind='upload',permission_confirmed=True,priority=100).model_dump()
        with store.connect() as c:
            c.execute("INSERT INTO sources(id,config,enabled,state,created,next_check,last_success) VALUES ('manual',?,0,'snapshot',?,?,?) ON CONFLICT(id) DO NOTHING",(json.dumps(config),now,now,now))
            store._apply(c,'manual',[payload],now,mark_missing=False)
            record(c,None,'admin','mic-created',payload['name'])
        return {'ok':True}

    @app.post('/api/admin/mics/{mic_id}/restore', dependencies=[Depends(admin)])
    async def restore(mic_id: str):
        with store.connect() as c:
            c.execute('DELETE FROM overlays WHERE mic_id=?',(mic_id,))
            record(c,mic_id,'admin','restore-source-values')
        return {'ok':True}

    @app.patch('/api/admin/mics/{mic_id}/visibility', dependencies=[Depends(admin)])
    async def hide(mic_id: str, body: Hide):
        listing(mic_id,include_hidden=True)
        with store.connect() as c:
            c.execute('INSERT INTO mic_flags VALUES (?,?) ON CONFLICT(mic_id) DO UPDATE SET hidden=excluded.hidden',(mic_id,int(body.hidden)))
            record(c,mic_id,'admin','hidden' if body.hidden else 'published')
        return {'ok':True}

    @app.delete('/api/admin/mics/{mic_id}/owner', dependencies=[Depends(admin)])
    async def revoke(mic_id: str):
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            owner=c.execute('SELECT owner_id,claim_id FROM ownership WHERE mic_id=?',(mic_id,)).fetchone()
            if owner:
                # Recurring dates share one approved claim, so revocation must
                # cover its whole family without touching separately approved mics.
                c.execute('DELETE FROM ownership WHERE claim_id=?',(owner['claim_id'],))
                c.execute("UPDATE submissions SET state='revoked' WHERE id=? AND kind='claim'",(owner['claim_id'],))
                c.execute('DELETE FROM invitations WHERE submission_id=?',(owner['claim_id'],))
            else:
                c.execute('DELETE FROM invitations WHERE submission_id IN (SELECT id FROM submissions WHERE mic_id=? AND kind=\'claim\' AND state IN (\'approved\',\'activated\'))',(mic_id,))
                c.execute("UPDATE submissions SET state='revoked' WHERE mic_id=? AND kind='claim' AND state IN ('approved','activated')",(mic_id,))
            if owner and not c.execute('SELECT 1 FROM ownership WHERE owner_id=?',(owner['owner_id'],)).fetchone():
                c.execute('DELETE FROM owner_sessions WHERE owner_id=?',(owner['owner_id'],))
                c.execute('DELETE FROM owners WHERE id=?',(owner['owner_id'],))
            record(c,mic_id,'admin','owner-revoked')
        return {'ok':True}
