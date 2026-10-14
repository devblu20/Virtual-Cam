"""Persistent user credentials and plain-text prompt assignments (no remote code)."""
import json
import hashlib
import hmac
import os
import re
import secrets
import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from reference_library import ReferenceStore
from processing_config import load_processing_config
from usage_history import small_json


def prompt_text(value):
    if not isinstance(value, str):
        raise HTTPException(400, "Prompt must be text.")
    value = value.strip()
    if not value or any(0xD800 <= ord(c) <= 0xDFFF for c in value) or len(value.encode('utf-16-le')) // 2 > 1024 or any(ord(c) < 32 and c not in '\n\t\r' for c in value):
        raise HTTPException(400, "Use a prompt of 1–1024 characters.")
    return value


def label(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(ord(c) < 32 for c in value):
        raise HTTPException(400, "Use a name of 1–80 characters.")
    return value.strip()


class ManagedUsers(ReferenceStore):
    def __init__(self, legacy):
        super().__init__()
        self.legacy = dict(legacy)

    def schema(self, db):
        db.execute('''CREATE TABLE IF NOT EXISTS managed_users (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
          active INTEGER NOT NULL DEFAULT 1, prompt TEXT, room TEXT NOT NULL DEFAULT '',
          revision INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS managed_defaults (
          id INTEGER PRIMARY KEY CHECK(id=1), prompt TEXT NOT NULL, revision INTEGER NOT NULL)''')

    def runtime(self):
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS managed_runtime (id INTEGER PRIMARY KEY, settings TEXT NOT NULL, revision INTEGER NOT NULL)")
            row = db.execute("SELECT settings,revision FROM managed_runtime WHERE id=1").fetchone()
        return dict(json.loads(row['settings']), revision=row['revision']) if row else dict(model='lucy-2.5', enhance=True, selfAnchor=True, revision=0)

    def save_runtime(self, body):
        if body.get('model') not in ('lucy-2.1', 'lucy-2.5') or type(body.get('enhance')) is not bool or type(body.get('selfAnchor')) is not bool or type(body.get('revision')) is not int:
            raise HTTPException(400, 'Choose a supported model and valid settings.')
        self.runtime()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM managed_runtime WHERE id=1').fetchone()
            revision = row['revision'] if row else 0
            if revision != body['revision']:
                raise HTTPException(409, 'Settings changed. Refresh before saving.')
            settings = {k: body[k] for k in ('model', 'enhance', 'selfAnchor')}
            db.execute('INSERT INTO managed_runtime VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET settings=excluded.settings,revision=excluded.revision', (json.dumps(settings), revision+1))
        return dict(settings, revision=revision+1)

    def rows(self):
        if self.directory is None or not (self.directory / 'references.sqlite3').exists():
            return []
        with self.connection() as db:
            self.schema(db)
            return [dict(r) for r in db.execute('SELECT * FROM managed_users ORDER BY created,id')]

    # Existing auth routes use this live mapping. Explicit managed revocation or
    # rotation overrides an environment key; it must never fall back to that key.
    def items(self):
        keys = dict(self.legacy)
        for row in self.rows():
            keys.pop(row['id'], None)
            if row['active']:
                keys[row['id']] = row['key_hash']
        return keys.items()

    def values(self):
        return dict(self.items()).values()

    def __bool__(self):
        return bool(self.legacy) or self.directory is not None

    def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        key = header[7:] if header.startswith('Bearer ') else ''
        if not 32 <= len(key) <= 256:
            raise HTTPException(401, 'Valid personal access key required.')
        digest = hashlib.sha256(key.encode()).hexdigest()
        for owner, stored in self.items():
            if hmac.compare_digest(digest, stored):
                return owner
        raise HTTPException(401, 'Access key is invalid or revoked.')

    def user(self, owner):
        for row in self.rows():
            if row['id'] == owner:
                return row
        if owner in self.legacy:
            return dict(id=owner, name=owner, key_hash=self.legacy[owner], active=1,
                        prompt=None, room='', revision=0, created=0, updated=0)
        raise HTTPException(404, 'User not found.')

    @staticmethod
    def public(row):
        return {k: v for k, v in row.items() if k != 'key_hash'}

    def default(self):
        if self.directory is not None:
            with self.connection() as db:
                self.schema(db)
                row = db.execute('SELECT prompt,revision FROM managed_defaults WHERE id=1').fetchone()
                if row:
                    return dict(row)
        return dict(prompt=prompt_text(load_processing_config()['prompt']), revision=0)

    def config(self, owner, version=2):
        row = self.user(owner)
        if not row['active']:
            raise HTTPException(401, 'Access key is revoked.')
        prompt = row['prompt'] or self.default()['prompt']
        result = dict(schemaVersion=2, prompt=prompt_text(prompt),
                    revision=hashlib.sha256(prompt.encode()).hexdigest(),
                    userId=owner, displayName=row['name'], room=row['room'],
                    model='lucy-2.1', enhance=False)
        if version == 3:
            runtime = self.runtime()
            result.update(schemaVersion=3, **{k: runtime[k] for k in ('model', 'enhance', 'selfAnchor')})
            result['revision'] = hashlib.sha256(json.dumps([prompt, runtime], sort_keys=True).encode()).hexdigest()
        return result

    def listing(self):
        rows = {k: self.public(dict(id=k, name=k, active=1, prompt=None, room='', revision=0, created=0, updated=0)) for k in self.legacy}
        rows.update({r['id']: self.public(r) for r in self.rows()})
        return dict(users=list(rows.values()), default=self.default(), runtime=self.runtime())

    def create(self, name):
        name = label(name)
        key, owner, now = secrets.token_urlsafe(36), uuid.uuid4().hex, time.time()
        with self.connection() as db:
            self.schema(db)
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM managed_users').fetchone()[0] >= 1000:
                raise HTTPException(409, 'User limit reached.')
            db.execute('INSERT INTO managed_users(id,name,key_hash,created,updated) VALUES(?,?,?,?,?)',
                       (owner, name, hashlib.sha256(key.encode()).hexdigest(), now, now))
        return dict(user=self.public(self.user(owner)), accessKey=key)

    def update(self, owner, body, rotate=False):
        original = self.user(owner)
        if type(body.get('revision')) is not int:
            raise HTTPException(400, 'Refresh the user before saving.')
        name = label(body.get('name', original['name']))
        room = body.get('room', original['room'])
        if not isinstance(room, str) or len(room) > 160 or any(ord(c) < 32 for c in room):
            raise HTTPException(400, 'Room note must be at most 160 characters.')
        active = body.get('active', bool(original['active']))
        if type(active) is not bool:
            raise HTTPException(400, 'Invalid access status.')
        prompt = body.get('prompt', original['prompt'])
        if prompt is not None:
            prompt = prompt_text(prompt)
        key = secrets.token_urlsafe(36) if rotate else None
        with self.connection() as db:
            self.schema(db)
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT revision FROM managed_users WHERE id=?', (owner,)).fetchone()
            revision = current[0] if current else 0
            if revision != body['revision']:
                raise HTTPException(409, 'Another change was saved. Refresh before editing.')
            db.execute('''INSERT INTO managed_users VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET name=excluded.name,key_hash=excluded.key_hash,
              active=excluded.active,prompt=excluded.prompt,room=excluded.room,
              revision=excluded.revision,updated=excluded.updated''',
              (owner, name, hashlib.sha256(key.encode()).hexdigest() if key else original['key_hash'],
               int(active), prompt, room.strip(), revision+1, original['created'] or time.time(), time.time()))
        result = dict(user=self.public(self.user(owner)))
        if key:
            result['accessKey'] = key
        return result

    def set_default(self, body):
        prompt = prompt_text(body.get('prompt'))
        with self.connection() as db:
            self.schema(db)
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM managed_defaults WHERE id=1').fetchone()
            revision = row[0] if row else 0
            if type(body.get('revision')) is not int or body['revision'] != revision:
                raise HTTPException(409, 'Default prompt changed. Refresh before saving.')
            db.execute('INSERT INTO managed_defaults VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET prompt=excluded.prompt,revision=excluded.revision', (prompt, revision+1))
        return dict(prompt=prompt, revision=revision+1)


def register_managed_routes(app, store):
    admin_hash = os.getenv('VCAM_ADMIN_KEY_SHA256', '').strip().lower()
    requests = defaultdict(deque)

    def admin(request):
        if not re.fullmatch('[a-f0-9]{64}', admin_hash) or admin_hash in store.values():
            raise HTTPException(503, 'Configure a separate administrator credential.')
        header = request.headers.get('Authorization', '')
        supplied = header[7:] if header.startswith('Bearer ') else ''
        if not 32 <= len(supplied) <= 256 or not hmac.compare_digest(hashlib.sha256(supplied.encode()).hexdigest(), admin_hash):
            raise HTTPException(403, 'Administrator access required.')

    @app.get('/api/client-config')
    async def configuration(request: Request, version: int = 2):
        if version not in (2, 3):
            raise HTTPException(400, "Unsupported configuration version.")
        owner = await run_in_threadpool(store.authenticate, request)
        now, bucket = time.monotonic(), requests[owner]
        while bucket and now-bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= 30:
            raise HTTPException(429, 'Too many configuration checks.')
        bucket.append(now)
        return await run_in_threadpool(store.config, owner, version)

    @app.put('/api/admin/runtime')
    async def runtime(request: Request):
        await run_in_threadpool(admin, request)
        body = await small_json(request)
        if not isinstance(body, dict):
            raise HTTPException(400, 'Settings required.')
        return await run_in_threadpool(store.save_runtime, body)

    @app.get('/api/admin/users')
    async def listing(request: Request):
        await run_in_threadpool(admin, request)
        return await run_in_threadpool(store.listing)

    @app.post('/api/admin/users')
    async def create(request: Request):
        await run_in_threadpool(admin, request)
        body = await small_json(request)
        if not isinstance(body, dict):
            raise HTTPException(400, 'User details required.')
        return await run_in_threadpool(store.create, body.get('name'))

    @app.put('/api/admin/users/{owner}')
    async def update(owner: str, request: Request):
        await run_in_threadpool(admin, request)
        body = await small_json(request, 8192)
        if not isinstance(body, dict):
            raise HTTPException(400, 'User details required.')
        return await run_in_threadpool(store.update, owner, body)

    @app.post('/api/admin/users/{owner}/rotate-key')
    async def rotate(owner: str, request: Request):
        await run_in_threadpool(admin, request)
        body = await small_json(request)
        if not isinstance(body, dict):
            raise HTTPException(400, 'User details required.')
        return await run_in_threadpool(store.update, owner, body, True)

    @app.put('/api/admin/default-prompt')
    async def default(request: Request):
        await run_in_threadpool(admin, request)
        body = await small_json(request, 8192)
        if not isinstance(body, dict):
            raise HTTPException(400, 'Prompt details required.')
        return await run_in_threadpool(store.set_default, body)
