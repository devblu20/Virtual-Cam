"""Private reference library. No public image URLs; ownership is an access-record ID."""
import asyncio
import base64
import hashlib
import hmac
import io
import os
import re
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException, Request
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

MAX_BYTES = 2 * 1024 * 1024
MAX_PHOTOS = 20
MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def validate_image(data, content_type):
    try:
        with Image.open(io.BytesIO(data)) as image:
            mime = MIMES.get(image.format)
            if not mime or mime != content_type:
                raise HTTPException(415, "Use an actual PNG, JPEG or WebP image.")
            width, height = image.size
            if min(width, height) < 512 or width * height > 16_000_000:
                raise HTTPException(400, "Use an image at least 512 x 512 and at most 16 megapixels.")
            if getattr(image, "n_frames", 1) != 1:
                raise HTTPException(400, "Animated images are not supported.")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            thumb = ImageOps.exif_transpose(image).convert("RGB")
            thumb.thumbnail((160, 160))
            output = io.BytesIO()
            thumb.save(output, format="JPEG", quality=75)
        return mime, width, height, output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise HTTPException(400, "The image could not be decoded. Choose another photo.") from None


class ReferenceStore:
    def __init__(self):
        configured = os.getenv("VCAM_DATA_DIR", "").strip()
        self.directory = Path(configured).resolve() if configured else None
        # In Railway never silently store in the ephemeral deployment filesystem.
        if os.getenv("RAILWAY_ENVIRONMENT_ID") and self.directory:
            mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")
            if not mount or not self.directory.is_relative_to(Path(mount).resolve()):
                self.directory = None

    @contextmanager
    def connection(self):
        if self.directory is None:
            raise HTTPException(503, "Photo storage is not configured. Ask Bluqq to attach a Railway volume.")
        db = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.directory / "references.sqlite3", timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA secure_delete=ON")
            db.execute("""CREATE TABLE IF NOT EXISTS photos (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                mime TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                created INTEGER NOT NULL, digest TEXT NOT NULL, image BLOB NOT NULL,
                thumbnail BLOB NOT NULL, UNIQUE(owner, digest))""")
            db.execute("CREATE INDEX IF NOT EXISTS photos_owner ON photos(owner)")
            with db:
                yield db
        except (sqlite3.Error, OSError):
            raise HTTPException(503, "Photo storage is unavailable. Contact Bluqq support.") from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def metadata(row):
        return {"id": row["id"], "name": row["name"], "width": row["width"],
                "height": row["height"], "createdAt": row["created"],
                "thumbnail": "data:image/jpeg;base64," + base64.b64encode(row["thumbnail"]).decode("ascii")}

    def list(self, owner):
        with self.connection() as db:
            rows = db.execute("SELECT id,name,width,height,created,thumbnail FROM photos WHERE owner=? ORDER BY created DESC,id", (owner,)).fetchall()
            return {"references": [self.metadata(row) for row in rows], "limit": MAX_PHOTOS}

    def add(self, owner, name, data, content_type):
        mime, width, height, thumbnail = validate_image(data, content_type)
        digest = hashlib.sha256(data).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM photos WHERE owner=? AND digest=?", (owner, digest)).fetchone()
            if existing:
                return self.metadata(existing)
            count = db.execute("SELECT COUNT(*) FROM photos WHERE owner=?", (owner,)).fetchone()[0]
            if count >= MAX_PHOTOS:
                raise HTTPException(409, "Your library is full (20 photos). Delete a saved photo first.")
            total = db.execute("SELECT COALESCE(SUM(length(image)+length(thumbnail)),0) FROM photos").fetchone()[0]
            if total + len(data) + len(thumbnail) > 256 * 1024 * 1024:
                raise HTTPException(409, "The service photo library is full. Contact Bluqq support.")
            photo_id = str(uuid.uuid4())
            db.execute("INSERT INTO photos VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (photo_id, owner, name, mime, width, height, int(time.time()), digest, data, thumbnail))
            return self.metadata(db.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone())

    def get(self, owner, photo_id):
        with self.connection() as db:
            row = db.execute("SELECT image,mime FROM photos WHERE owner=? AND id=?", (owner, photo_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Photo not found in this key's library.")
            return bytes(row["image"]), row["mime"]

    def delete(self, owner, photo_id):
        with self.connection() as db:
            if db.execute("DELETE FROM photos WHERE owner=? AND id=?", (owner, photo_id)).rowcount == 0:
                raise HTTPException(404, "Photo not found in this key's library.")


def register_reference_routes(app, access_keys):
    store = ReferenceStore()
    requests = defaultdict(deque)
    uploads = defaultdict(deque)
    upload_lock = asyncio.Lock()

    def authenticate(request):
        authorization = request.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not 32 <= len(supplied) <= 256:
            raise HTTPException(401, "Enter a valid personal access key.")
        candidate = hashlib.sha256(supplied.encode()).hexdigest()
        owner = None
        for user_id, digest in access_keys.items():
            if hmac.compare_digest(candidate, digest):
                owner = user_id
        if owner is None:
            raise HTTPException(401, "Access key is invalid or revoked.")
        bucket = requests[owner]
        now = time.monotonic()
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= 120:
            raise HTTPException(429, "Too many library requests. Try again in a minute.", headers={"Retry-After": "60"})
        bucket.append(now)
        return owner

    def check_id(photo_id):
        if not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", photo_id):
            raise HTTPException(404, "Photo not found.")

    @app.get("/api/references")
    async def list_photos(request: Request):
        owner = authenticate(request)
        return await run_in_threadpool(store.list, owner)

    @app.post("/api/references", status_code=201)
    async def upload_photo(request: Request):
        owner = authenticate(request)
        if request.headers.get("X-Reference-Consent") != "true":
            raise HTTPException(400, "Permission to store and share this photo with this key is required.")
        name = unquote(request.headers.get("X-Reference-Name", "Reference photo")).strip()
        if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
            raise HTTPException(400, "Use a photo name between 1 and 80 characters.")
        content_type = request.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type not in MIMES.values():
            raise HTTPException(415, "Use PNG, JPEG or WebP.")
        bucket = uploads[owner]
        now = time.monotonic()
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= 10:
            raise HTTPException(429, "Upload limit reached. Try again in a minute.", headers={"Retry-After": "60"})
        bucket.append(now)
        # Bound simultaneous buffers and decoding work in the single-worker deployment.
        if upload_lock.locked():
            raise HTTPException(429, "Another upload is in progress. Try again shortly.", headers={"Retry-After": "3"})
        async with upload_lock:
            data = bytearray()
            try:
                async with asyncio.timeout(30):
                    async for chunk in request.stream():
                        if len(data) + len(chunk) > MAX_BYTES:
                            raise HTTPException(413, "Photo must be at most 2 MB.")
                        data.extend(chunk)
            except TimeoutError:
                raise HTTPException(408, "Photo upload timed out.") from None
            if not data:
                raise HTTPException(400, "Choose a non-empty image.")
            return await run_in_threadpool(store.add, owner, name, bytes(data), content_type)

    @app.get("/api/references/{photo_id}")
    async def get_photo(photo_id: str, request: Request):
        owner = authenticate(request)
        check_id(photo_id)
        data, mime = await run_in_threadpool(store.get, owner, photo_id)
        return Response(data, media_type=mime)

    @app.delete("/api/references/{photo_id}", status_code=204)
    async def delete_photo(photo_id: str, request: Request):
        owner = authenticate(request)
        check_id(photo_id)
        await run_in_threadpool(store.delete, owner, photo_id)
        return Response(status_code=204)
