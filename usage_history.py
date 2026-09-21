"""Administrator-only, server-timed usage metadata. Not a billing ledger.

Uses the photo library's persistent volume and SQLite connection policy. Clients
report activity; missing reports are never extrapolated into unlimited usage.
"""
import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import time
import uuid
import unicodedata
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from reference_library import ReferenceStore

HEARTBEAT_GAP = 45
STALE_AFTER = 65
MAX_SESSIONS = 100_000
PLATFORMS = {"website", "meet", "zoom", "teams"}


async def small_json(request):
    data = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 4096:
                    raise HTTPException(413, "Usage request is too large.")
        return json.loads(data) if data else None
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid usage JSON.") from None
    except TimeoutError:
        raise HTTPException(408, "Usage request timed out.") from None


def clean_text(value, maximum=80):
    if not isinstance(value, str):
        raise HTTPException(400, "Invalid usage metadata.")
    value = value.strip()
    if not value or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HTTPException(400, "Invalid usage metadata.")
    return value


def avatar_metadata(value):
    if not isinstance(value, dict) or value.get("kind") not in {"library", "bundled", "upload"}:
        raise HTTPException(400, "Invalid avatar metadata.")
    kind = value["kind"]
    if kind == "library":
        try:
            return {"kind": kind, "id": str(uuid.UUID(value.get("id", "")))}
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "Invalid library avatar identifier.") from None
    if kind == "bundled":
        identifier = value.get("id")
        if identifier not in {"portrait-01", "portrait-02"}:
            raise HTTPException(400, "Invalid bundled avatar identifier.")
        return {"kind": kind, "id": identifier, "name": clean_text(value.get("name", identifier))}
    digest = value.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise HTTPException(400, "Invalid reference fingerprint.")
    # A basename only: never record a local filesystem path.
    name = clean_text(value.get("name", "Uploaded reference")).replace("\\", "/").split("/")[-1]
    return {"kind": kind, "id": digest, "name": name or "Uploaded reference"}


def usage_metadata(body):
    if body is None:
        return None  # Older extensions remain compatible.
    if not isinstance(body, dict):
        raise HTTPException(400, "Invalid usage metadata.")
    value = body.get("usage")
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("source") not in {"website", "extension"}:
        raise HTTPException(400, "Invalid usage source.")
    if value.get("platform") not in PLATFORMS or (value["source"] == "website") != (value["platform"] == "website"):
        raise HTTPException(400, "Invalid usage platform.")
    participant_name = None
    if "participantName" in value:
        raw_name = value["participantName"]
        if not isinstance(raw_name, str) or any(unicodedata.category(c).startswith("C") for c in raw_name):
            raise HTTPException(400, "Enter a valid name (1–80 characters).")
        participant_name = clean_text(unicodedata.normalize("NFC", raw_name), 80)
    return {"source": value["source"], "platform": value["platform"], "participant_name": participant_name,
            "version": clean_text(value.get("version", "unknown"), 32),
            "avatar": avatar_metadata(value.get("avatar"))}


class UsageStore(ReferenceStore):
    def schema(self, db):
        db.executescript("""
        CREATE TABLE IF NOT EXISTS usage_sessions (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, source TEXT NOT NULL,
          platform TEXT NOT NULL, client_version TEXT NOT NULL, created REAL NOT NULL,
          started REAL, last_seen REAL NOT NULL, ended REAL, state TEXT NOT NULL,
          reason TEXT, sequence INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0,
          has_gaps INTEGER NOT NULL DEFAULT 0, participant_name TEXT);
        CREATE INDEX IF NOT EXISTS usage_created ON usage_sessions(created DESC);
        CREATE INDEX IF NOT EXISTS usage_owner ON usage_sessions(owner, created DESC);
        CREATE TABLE IF NOT EXISTS usage_avatars (
          session_id TEXT NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL,
          avatar_id TEXT NOT NULL, name TEXT NOT NULL, selected REAL NOT NULL,
          seconds REAL NOT NULL DEFAULT 0, PRIMARY KEY(session_id, ordinal));
        """)
        # Additive migration: preserve all existing sessions and their ownership.
        # Recheck under the write lock so simultaneous first requests are safe.
        if "participant_name" not in {row[1] for row in db.execute("PRAGMA table_info(usage_sessions)")}:
            db.execute("BEGIN IMMEDIATE")
            if "participant_name" not in {row[1] for row in db.execute("PRAGMA table_info(usage_sessions)")}:
                db.execute("ALTER TABLE usage_sessions ADD COLUMN participant_name TEXT")
            db.commit()

    def resolve_avatar(self, db, owner, avatar):
        if avatar["kind"] == "library":
            row = db.execute("SELECT id,name FROM photos WHERE owner=? AND id=?", (owner, avatar["id"])).fetchone()
            if row:
                return "library", row["id"], row["name"]
            # May have been deleted after the extension downloaded it. Do not
            # reveal another owner's photo or prevent an already valid stream.
            return "library", avatar["id"], "Unavailable library photo"
        if avatar["kind"] == "upload":
            row = db.execute("SELECT id,name FROM photos WHERE owner=? AND digest=?", (owner, avatar["id"])).fetchone()
            if row:
                return "library", row["id"], row["name"]
        return avatar["kind"], avatar["id"], avatar["name"]

    def start(self, owner, metadata):
        now, identifier = time.time(), str(uuid.uuid4())
        with self.connection() as db:
            self.schema(db)
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM usage_sessions").fetchone()[0] >= MAX_SESSIONS:
                raise HTTPException(503, "Usage history capacity reached.")
            avatar = self.resolve_avatar(db, owner, metadata["avatar"])
            db.execute("INSERT INTO usage_sessions(id,owner,source,platform,client_version,created,last_seen,state,participant_name) VALUES(?,?,?,?,?,?,?,?,?)",
                       (identifier, owner, metadata["source"], metadata["platform"], metadata["version"], now, now, "connecting", metadata.get("participant_name")))
            db.execute("INSERT INTO usage_avatars(session_id,ordinal,kind,avatar_id,name,selected) VALUES(?,0,?,?,?,?)", (identifier, *avatar, now))
        return identifier

    def fail(self, identifier, reason):
        with self.connection() as db:
            self.schema(db)
            db.execute("UPDATE usage_sessions SET state='failed',reason=?,ended=? WHERE id=? AND state='connecting'",
                       (reason, time.time(), identifier))

    def event(self, owner, identifier, event):
        now = time.time()
        with self.connection() as db:
            self.schema(db)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM usage_sessions WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if row is None:
                raise HTTPException(404, "Usage session not found.")
            if row["ended"] is not None or event["sequence"] <= row["sequence"]:
                return {"ok": True}  # Idempotent, closed sessions cannot reopen.
            gap = max(0, now - row["last_seen"])
            delta = gap if row["state"] == "active" and gap <= HEARTBEAT_GAP else 0
            has_gaps = row["has_gaps"] or (row["state"] == "active" and gap > HEARTBEAT_GAP)
            current = db.execute("SELECT ordinal FROM usage_avatars WHERE session_id=? ORDER BY ordinal DESC LIMIT 1", (identifier,)).fetchone()[0]
            db.execute("UPDATE usage_avatars SET seconds=seconds+? WHERE session_id=? AND ordinal=?", (delta, identifier, current))
            action = event["action"]
            state = "ended" if action == "end" else "active" if action == "pulse" else row["state"]
            # Avatar changes take effect only after the client confirms application.
            if action == "avatar":
                if current >= 99:
                    raise HTTPException(409, "Start a new session after 100 avatar selections.")
                avatar = self.resolve_avatar(db, owner, event["avatar"])
                db.execute("INSERT INTO usage_avatars(session_id,ordinal,kind,avatar_id,name,selected) VALUES(?,?,?,?,?,?)", (identifier, current + 1, *avatar, now))
            db.execute("""UPDATE usage_sessions SET state=?,started=COALESCE(started,?),last_seen=?,ended=?,reason=?,
                       sequence=?,seconds=seconds+?,has_gaps=? WHERE id=?""",
                       (state, now if action == "pulse" else None, now, now if action == "end" else None,
                        event.get("reason") if action == "end" else None, event["sequence"], delta, int(has_gaps), identifier))
        return {"ok": True}

    def listing(self, owner="", platform="", after=None, before=None, offset=0, limit=50):
        clauses, params = ["1=1"], []
        for column, op, value in [("owner", "=", owner or None), ("platform", "=", platform or None),
                                  ("created", ">=", after), ("created", "<", before)]:
            if value is not None:
                clauses.append(f"{column}{op}?")
                params.append(value)
        where = " AND ".join(clauses)
        now = time.time()
        with self.connection() as db:
            self.schema(db)
            totals = db.execute(f"SELECT COUNT(*),COALESCE(SUM(seconds),0),COUNT(DISTINCT owner) FROM usage_sessions WHERE {where}", params).fetchone()
            rows = db.execute(f"SELECT * FROM usage_sessions WHERE {where} ORDER BY created DESC,id DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                if item["ended"] is None and now - item["last_seen"] > STALE_AFTER:
                    item["state"] = "interrupted" if item["started"] is not None else "unconfirmed"
                item["estimated"] = True
                item["avatars"] = [dict(a) for a in db.execute("SELECT ordinal,kind,avatar_id,name,selected,seconds FROM usage_avatars WHERE session_id=? ORDER BY ordinal", (row["id"],))]
                items.append(item)
        return {"items": items, "total": totals[0], "seconds": totals[1], "users": totals[2], "offset": offset,
                "limit": limit, "asOf": now, "durationBasis": "Server-timed client activity reports; estimates, not billable time."}


def register_usage_routes(app, access_keys):
    store = UsageStore()
    admin_hash = os.getenv("VCAM_ADMIN_KEY_SHA256", "").strip().lower()
    admin_ready = bool(re.fullmatch(r"[a-f0-9]{64}", admin_hash)) and admin_hash not in access_keys.values()
    requests = defaultdict(deque)

    def authenticate(request, admin=False):
        header = request.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        if not 32 <= len(supplied) <= 256:
            raise HTTPException(401, "Valid credentials required.")
        digest = hashlib.sha256(supplied.encode()).hexdigest()
        if admin:
            if not admin_ready:
                raise HTTPException(503, "Configure a separate VCAM_ADMIN_KEY_SHA256 credential.")
            if not hmac.compare_digest(digest, admin_hash):
                raise HTTPException(403, "Administrator access required.")
            owner = "@administrator"
        else:
            owner = None
            for user, hashed in access_keys.items():
                if hmac.compare_digest(digest, hashed):
                    owner = user
            if owner is None:
                raise HTTPException(401, "Access key is invalid or revoked.")
        bucket, now = requests[owner], time.monotonic()
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= 180:
            raise HTTPException(429, "Usage request limit reached.")
        bucket.append(now)
        return owner

    @app.post("/api/usage/{identifier}/events")
    async def event(identifier: str, request: Request):
        owner = authenticate(request)
        body = await small_json(request)
        if not isinstance(body, dict) or body.get("action") not in {"pulse", "avatar", "end"}:
            raise HTTPException(400, "Invalid usage event.")
        sequence = body.get("sequence")
        if type(sequence) is not int or not 1 <= sequence <= 10_000_000:
            raise HTTPException(400, "Invalid usage sequence.")
        normalized = {"action": body["action"], "sequence": sequence}
        if body["action"] == "avatar":
            normalized["avatar"] = avatar_metadata(body.get("avatar"))
        if body["action"] == "end":
            reason = body.get("reason", "stopped")
            if reason not in {"stopped", "closed", "replaced", "disconnected", "failed"}:
                raise HTTPException(400, "Invalid end reason.")
            normalized["reason"] = reason
        return await run_in_threadpool(store.event, owner, identifier, normalized)

    @app.get("/api/admin/usage")
    async def listing(request: Request, owner: str = "", platform: str = "", after: float | None = None,
                      before: float | None = None, offset: int = 0, limit: int = 50):
        authenticate(request, admin=True)
        if (owner and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", owner)) or (platform and platform not in PLATFORMS):
            raise HTTPException(400, "Invalid usage filter.")
        if not 0 <= offset <= MAX_SESSIONS or not 1 <= limit <= 100:
            raise HTTPException(400, "Invalid page.")
        if any(value is not None and (not math.isfinite(value) or value < 0) for value in (after, before)):
            raise HTTPException(400, "Invalid time filter.")
        return await run_in_threadpool(store.listing, owner, platform, after, before, offset, limit)

    return store
