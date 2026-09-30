"""Shared administrator credentials and session generations.

The deployment password bootstraps authentication and can recover it by being
rotated. Only salted, deliberately expensive password verifiers are persisted.
"""
import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException


ITERATIONS = 600000
META_KEY = 'admin_credentials_v1'


def password_record(password):
    salt = secrets.token_bytes(24)
    return {
        'salt': salt.hex(),
        'password_hash': hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS).hex(),
        'revision': secrets.token_urlsafe(32),
        'weak_password': len(password) < 15,
    }


def matches(password, record):
    candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(record['salt']), ITERATIONS)
    return hmac.compare_digest(candidate.hex(), record['password_hash'])


class AdminAuth:
    def __init__(self, store, deployment_password):
        self.store = store
        self.configured = bool(deployment_password)
        self.bootstrap = password_record(deployment_password) if self.configured else None
        # A slow verifier avoids exposing a cheap dictionary-testable SHA256 of
        # the deployment password in the database. This binding is not used to
        # verify sign-ins; every sign-in uses the random salt above instead.
        self.env_revision = hashlib.pbkdf2_hmac(
            'sha256', deployment_password.encode(), b'MicList deployment credential v1', ITERATIONS
        ).hex() if self.configured else ''

    @staticmethod
    def _read(c):
        row = c.execute('SELECT value FROM meta WHERE key=?', (META_KEY,)).fetchone()
        return json.loads(row['value']) if row else None

    @staticmethod
    def _write(c, record):
        c.execute('INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (META_KEY, json.dumps(record)))

    def current(self):
        if not self.configured:
            return None
        with self.store.connect() as c:
            record = self._read(c)
        if record and record['env_revision'] == self.env_revision:
            return record
        # Bootstrap/recovery is serialized with all password changes and login
        # commits on both SQLite and PostgreSQL (via the shared advisory lock).
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            record = self._read(c)
            if record and record['env_revision'] == self.env_revision:
                return record
            retired = record.get('retired_env_revisions', []) if record else []
            if self.env_revision in retired:
                # An old serverless worker must not undo recovery performed by
                # a newer deployment whose environment password has changed.
                return None
            if record:
                retired = [*retired, record['env_revision']]
            record = {**self.bootstrap, 'env_revision': self.env_revision,
                      'retired_env_revisions': retired}
            self._write(c, record)
            c.execute('DELETE FROM sessions')
            return record

    def session(self, token):
        record = self.current()
        if not token or not record:
            raise HTTPException(401, 'Sign in to manage your sources.')
        with self.store.connect() as c:
            session = c.execute('SELECT token_hash FROM sessions WHERE token_hash=? AND expires>? AND revision=?',
                                (hashlib.sha256(token.encode()).hexdigest(), time.time(), record['revision'])).fetchone()
        if not session:
            raise HTTPException(401, 'Sign in to manage your sources.')
        return record

    def attempt(self, ip):
        now = time.time()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM attempts WHERE created<?', (now - 900,))
            count = c.execute('SELECT COUNT(*) FROM attempts WHERE ip_hash=?', (ip,)).fetchone()[0]
            if count >= 8:
                raise HTTPException(429, 'Too many attempts. Try again in 15 minutes.')
            c.execute('INSERT INTO attempts VALUES (?,?)', (ip, now))

    def login(self, password, ip):
        record = self.current()
        if not record:
            raise HTTPException(503, 'Administrator sign-in is unavailable. Check the deployment ADMIN_PASSWORD.')
        self.attempt(ip)
        if not matches(password, record):
            raise HTTPException(401, 'Incorrect password.')
        token = secrets.token_urlsafe(40)
        now = time.time()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            latest = self._read(c)
            if not latest or latest['revision'] != record['revision']:
                raise HTTPException(401, 'Password changed. Sign in again.')
            c.execute('DELETE FROM sessions WHERE expires<? OR revision!=?', (now, record['revision']))
            c.execute('DELETE FROM attempts WHERE ip_hash=?', (ip,))
            c.execute('INSERT INTO sessions VALUES (?,?,?)',
                      (hashlib.sha256(token.encode()).hexdigest(), now + 43200, record['revision']))
        return token

    def change_password(self, token, current_password, new_password, ip):
        record = self.session(token)
        self.attempt(ip)
        if not matches(current_password, record):
            raise HTTPException(401, 'Incorrect current password.')
        replacement = password_record(new_password)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            latest = self._read(c)
            session = c.execute('SELECT token_hash FROM sessions WHERE token_hash=? AND expires>? AND revision=?',
                                (hashlib.sha256(token.encode()).hexdigest(), time.time(), record['revision'])).fetchone()
            if not latest or latest['revision'] != record['revision'] or not session:
                raise HTTPException(401, 'Password or session changed. Sign in again.')
            self._write(c, {**latest, **replacement})
            c.execute('DELETE FROM sessions')
            c.execute('DELETE FROM attempts WHERE ip_hash=?', (ip,))
        return replacement['weak_password']
